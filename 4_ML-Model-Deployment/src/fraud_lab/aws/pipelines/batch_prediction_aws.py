from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import date, datetime
from typing import Any

from src.aws_clients import client_error_code
from src.config import timestamp_slug, utc_now, write_json

from fraud_lab.aws.deploy_endpoint import create_fraud_sagemaker_model
from fraud_lab.aws.feature_store import AwsFeatureStore
from fraud_lab.aws.s3_data_lake import S3DataLake
from fraud_lab.features.current_transaction_features import build_current_transaction_features
from fraud_lab.features.feature_contract import default_contract, load_feature_order
from fraud_lab.features.feature_vector import assemble_feature_vector
from fraud_lab.model.model_loader import load_model_endpoint


def _metadata_path(config: Any, file_name: str) -> Any:
    return config.lab_config.metadata_path(file_name)


def _coerce_curated(row: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(row)
    coerced["amount"] = float(coerced["amount"])
    coerced["amount_pen"] = float(coerced["amount_pen"])
    return coerced


def _csv_without_header(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writerows(rows)
    return output.getvalue()


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _run_sagemaker_batch_transform(
    *,
    feature_store: AwsFeatureStore,
    s3_lake: S3DataLake,
    transform_input_s3_uri: str,
) -> dict[str, Any]:
    config = feature_store.config
    clients = feature_store.clients
    model_metadata = create_fraud_sagemaker_model(config, clients)

    job_name = f"{config.lab_config.resource_prefix}-fraud-batch-{timestamp_slug()}"
    job_name = job_name[:63].strip("-")
    output_s3_uri = config.s3_uri("batch", "transform-output", job_name)
    wait = config.lab_config.wait_for_batch
    request: dict[str, Any] = {
        "TransformJobName": job_name,
        "ModelName": model_metadata["model_name"],
        "TransformInput": {
            "DataSource": {
                "S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": transform_input_s3_uri,
                }
            },
            "ContentType": "text/csv",
            "SplitType": "Line",
        },
        "TransformOutput": {
            "S3OutputPath": output_s3_uri,
            "Accept": "application/json",
            "AssembleWith": "Line",
        },
        "TransformResources": {
            "InstanceType": config.fraud_batch_instance_type_candidates[0],
            "InstanceCount": config.fraud_batch_instance_count,
        },
        "BatchStrategy": "SingleRecord",
        "MaxPayloadInMB": config.lab_config.max_payload_mb,
        "MaxConcurrentTransforms": config.lab_config.max_concurrent_transforms,
        "Tags": config.tags,
    }

    create_errors: list[str] = []
    selected_instance_type = ""
    for instance_type in config.fraud_batch_instance_type_candidates:
        request["TransformResources"]["InstanceType"] = instance_type
        try:
            clients.sagemaker.create_transform_job(**request)
            selected_instance_type = instance_type
            break
        except Exception as exc:
            code = client_error_code(exc)
            message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            can_try_next = code in {"ResourceLimitExceeded", "ValidationException"}
            if can_try_next:
                create_errors.append(f"{instance_type}: {code} - {message}")
                print(
                    "No se pudo crear Batch Transform con "
                    f"{instance_type}. Probando siguiente candidato si existe."
                )
                continue
            raise

    if not selected_instance_type:
        details = "\n".join(f"- {item}" for item in create_errors)
        message = (
            "No se pudo crear el SageMaker Batch Transform Job con ningun tipo "
            "de instancia candidato porque la cuenta no tiene cuota disponible "
            "para transform job usage.\n"
            f"Candidatos: {', '.join(config.fraud_batch_instance_type_candidates)}\n"
            f"Errores:\n{details}\n\n"
            "Revisa Service Quotas para 'transform job usage' en SageMaker o define "
            "FRAUD_BATCH_INSTANCE_TYPE con una instancia valida y con cuota."
        )
        if config.fraud_require_batch_transform:
            raise RuntimeError(message)
        print(
            "ADVERTENCIA: se omitio el SageMaker Batch Transform Job real porque "
            "no hay cuota disponible. El paso continuara con los outputs S3 "
            "educativos. Define FRAUD_REQUIRE_BATCH_TRANSFORM=true para fallar "
            "cuando no se pueda crear el job."
        )
        metadata = {
            "transform_job_name": "",
            "model_name": model_metadata["model_name"],
            "batch_input_s3_uri": transform_input_s3_uri,
            "batch_output_s3_uri": "",
            "status": "SkippedQuotaUnavailable",
            "waited": False,
            "content_type": "text/csv",
            "accept": "application/json",
            "split_type": "Line",
            "batch_strategy": "SingleRecord",
            "max_payload_mb": config.lab_config.max_payload_mb,
            "max_concurrent_transforms": config.lab_config.max_concurrent_transforms,
            "instance_type": "",
            "instance_type_candidates": config.fraud_batch_instance_type_candidates,
            "instance_count": config.fraud_batch_instance_count,
            "created_at": utc_now(),
            "description": {
                "TransformJobStatus": "SkippedQuotaUnavailable",
                "Reason": message,
                "CreateErrors": create_errors,
            },
        }
        write_json(_metadata_path(config, "fraud_batch_transform_job.json"), _json_safe(metadata))
        return metadata

    description: dict[str, Any] = {"TransformJobStatus": "Submitted"}
    status = "Submitted"
    if wait:
        waiter = clients.sagemaker.get_waiter("transform_job_completed_or_stopped")
        waiter.wait(TransformJobName=job_name)
        description = clients.sagemaker.describe_transform_job(TransformJobName=job_name)
        status = str(description.get("TransformJobStatus", status))

    metadata = {
        "transform_job_name": job_name,
        "model_name": model_metadata["model_name"],
        "batch_input_s3_uri": transform_input_s3_uri,
        "batch_output_s3_uri": output_s3_uri,
        "status": status,
        "waited": wait,
        "content_type": "text/csv",
        "accept": "application/json",
        "split_type": "Line",
        "batch_strategy": "SingleRecord",
        "max_payload_mb": config.lab_config.max_payload_mb,
        "max_concurrent_transforms": config.lab_config.max_concurrent_transforms,
        "instance_type": selected_instance_type,
        "instance_type_candidates": config.fraud_batch_instance_type_candidates,
        "instance_count": config.fraud_batch_instance_count,
        "created_at": utc_now(),
        "description": description,
    }
    write_json(_metadata_path(config, "fraud_batch_transform_job.json"), _json_safe(metadata))
    return metadata


def batch_prediction_aws() -> dict[str, str]:
    feature_store = AwsFeatureStore()
    s3_lake = S3DataLake(feature_store.config, feature_store.clients)
    rows = [
        _coerce_curated(row)
        for row in s3_lake.read_csv("lake", "curated", "transactions_to_score.csv")
    ]
    if not rows:
        raise RuntimeError(
            "No existe lake/curated/transactions_to_score.csv en S3. "
            "Ejecuta make fraud-generate-data-aws primero."
        )
    if not feature_store.read_offline_export("user_behavior_features"):
        feature_store.seed_feature_store()

    endpoint = load_model_endpoint()
    contract = default_contract()
    feature_order = load_feature_order()
    predictions: list[dict[str, Any]] = []
    model_ready_rows: list[dict[str, Any]] = []
    transform_input_rows: list[dict[str, Any]] = []

    for row in rows:
        current_features, warnings = build_current_transaction_features(row)
        offline_features = feature_store.get_many_offline_for_transaction(row)
        vector = assemble_feature_vector(
            row,
            current_features,
            offline_features,
            contract,
            warnings,
        )
        prediction = endpoint.predict(
            vector.values,
            request_id=f"batch-{row['transaction_id']}",
        )
        predictions.append(
            {
                "transaction_id": row["transaction_id"],
                "fraud_score": prediction["fraud_score"],
                "decision": prediction["decision"],
                "model_version": prediction["model_version"],
                "feature_version": prediction["feature_version"],
            }
        )
        model_ready_row = {"transaction_id": row["transaction_id"], **vector.values}
        model_ready_rows.append(model_ready_row)
        transform_input_rows.append(model_ready_row)

    transform_input_text = _csv_without_header(
        transform_input_rows,
        fieldnames=["transaction_id", *feature_order],
    )
    transform_input_s3_uri = s3_lake.put_text(
        ("batch", "transform-input", "batch_transform_input.csv"),
        transform_input_text,
        "text/csv",
    )
    transform_job = _run_sagemaker_batch_transform(
        feature_store=feature_store,
        s3_lake=s3_lake,
        transform_input_s3_uri=transform_input_s3_uri,
    )

    outputs = {
        "predictions": s3_lake.put_csv(
            ("batch", "predictions", "batch_predictions.csv"),
            predictions,
        ),
        "model_ready": s3_lake.put_csv(
            ("batch", "model-ready", "batch_model_ready.csv"),
            model_ready_rows,
            fieldnames=["transaction_id", *feature_order],
        ),
        "transform_input": transform_input_s3_uri,
        "transform_job_name": transform_job["transform_job_name"],
        "transform_job_status": transform_job["status"],
        "transform_output": transform_job["batch_output_s3_uri"],
    }
    if transform_job["status"] == "SkippedQuotaUnavailable":
        outputs["transform_job_note"] = (
            "No se creo un Batch Transform Job real porque la cuenta tiene cuota 0 "
            "para los tipos de instancia candidatos."
        )
    print("Batch prediction cloud usando Offline Store export y SageMaker Batch Transform Job:")
    print(json.dumps(outputs, indent=2, sort_keys=True))
    return outputs


def main() -> None:
    argparse.ArgumentParser(
        description="Run AWS-backed fraud batch prediction using Offline Store exports."
    ).parse_args()
    batch_prediction_aws()


if __name__ == "__main__":
    main()
