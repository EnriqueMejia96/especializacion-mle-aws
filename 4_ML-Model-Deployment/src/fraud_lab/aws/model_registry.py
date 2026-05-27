from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

from src.aws_clients import client_error_code, get_sklearn_image_uri
from src.config import ConfigError, LOCAL_CACHE_DIR, ROOT_DIR, timestamp_slug, utc_now, write_json

from fraud_lab.aws.clients import FraudAwsClients
from fraud_lab.aws.config import FraudAwsConfig, load_fraud_aws_config
from fraud_lab.features.feature_contract import FEATURE_VERSION, MODEL_VERSION, default_contract

ARTIFACT_PACKAGING_VERSION = "fraud-sklearn-split-model-code-v7"
SAGEMAKER_ENTRY_POINT = "fraud_entry.py"

INFERENCE_SOURCE_FILES = (
    SAGEMAKER_ENTRY_POINT,
    "inference.py",
    "model_fn.py",
    "input_fn.py",
    "predict_fn.py",
    "output_fn.py",
    "requirements.txt",
    "setup.py",
)


def _fraud_training_rows() -> tuple[list[list[float]], list[int]]:
    contract = default_contract()
    feature_order = contract.feature_order
    rows: list[list[float]] = []
    labels: list[int] = []
    for idx in range(240):
        amount = float(20 + (idx * 37) % 1450)
        currency_amount = amount if idx % 5 else amount * 3.72
        hour = float(idx % 24)
        day = float(idx % 7)
        is_weekend = float(day in {5.0, 6.0})
        category_electronics = float(idx % 3 == 0)
        category_travel = float(idx % 7 == 0)
        category_grocery = float(idx % 4 == 0)
        channel_mobile = float(idx % 2 == 0)
        channel_web = 1.0 - channel_mobile
        is_cross_border = float(idx % 6 == 0)
        account_age_days = float(30 + (idx * 13) % 1500)
        customer_segment_premium = float(idx % 5 == 0)
        user_txn_count_1h = float((idx * 2) % 12)
        user_avg_amount_30d = float(35 + (idx * 11) % 260)
        card_txn_count_5m = float((idx * 3) % 8)
        card_declined_count_1h = float(idx % 4)
        merchant_fraud_rate_30d = round(((idx * 17) % 80) / 1000.0, 4)
        merchant_risk_score = round(((idx * 19) % 100) / 100.0, 4)
        device_users_count_7d = float((idx * 5) % 14)
        device_trust_score = round(1.0 - ((idx * 23) % 85) / 100.0, 4)
        values = {
            "amount_normalized": amount,
            "currency_normalized_amount": currency_amount,
            "hour_of_day": hour,
            "day_of_week": day,
            "is_weekend": is_weekend,
            "category_electronics": category_electronics,
            "category_travel": category_travel,
            "category_grocery": category_grocery,
            "channel_mobile": channel_mobile,
            "channel_web": channel_web,
            "is_cross_border": is_cross_border,
            "account_age_days": account_age_days,
            "customer_segment_premium": customer_segment_premium,
            "user_txn_count_1h": user_txn_count_1h,
            "user_avg_amount_30d": user_avg_amount_30d,
            "card_txn_count_5m": card_txn_count_5m,
            "card_declined_count_1h": card_declined_count_1h,
            "merchant_fraud_rate_30d": merchant_fraud_rate_30d,
            "merchant_risk_score": merchant_risk_score,
            "device_users_count_7d": device_users_count_7d,
            "device_trust_score": device_trust_score,
        }
        risk = (
            0.0022 * max(amount - max(user_avg_amount_30d * 3.0, 250.0), 0.0)
            + 0.11 * card_txn_count_5m
            + 0.18 * card_declined_count_1h
            + 0.75 * merchant_risk_score
            + 0.07 * device_users_count_7d
            + 0.35 * is_cross_border
            - 0.55 * device_trust_score
            - 0.0002 * account_age_days
        )
        rows.append([float(values[name]) for name in feature_order])
        labels.append(int(risk >= 0.85))
    return rows, labels


def _write_fraud_model_tarball(model_dir: Path, artifact_path: Path) -> Path:
    with tarfile.open(artifact_path, "w:gz") as tar:
        tar.add(model_dir / "model.joblib", arcname="model.joblib")
        tar.add(model_dir / "model_metadata.json", arcname="model_metadata.json")
    return artifact_path


def _write_fraud_source_dir_tarball(code_dir: Path, artifact_path: Path) -> Path:
    with tarfile.open(artifact_path, "w:gz") as tar:
        for child in code_dir.iterdir():
            tar.add(child, arcname=child.name)
    return artifact_path


def create_fraud_model_artifact(config: FraudAwsConfig | None = None) -> Path:
    try:
        import joblib
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise ConfigError(
            "El registro del modelo de fraude requiere scikit-learn y joblib. "
            "Ejecuta: pip install -r requirements.txt"
        ) from exc

    config = config or load_fraud_aws_config(require_operational=False)
    rows, labels = _fraud_training_rows()
    model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=500, class_weight="balanced")),
        ]
    )
    model.fit(rows, labels)

    model_dir = LOCAL_CACHE_DIR / "fraud_model_registry_model"
    if model_dir.exists():
        shutil.rmtree(model_dir)
    code_dir = model_dir / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_dir / "model.joblib")

    inference_dir = Path(__file__).resolve().parent / "sagemaker_inference"
    for file_name in INFERENCE_SOURCE_FILES:
        shutil.copy2(inference_dir / file_name, code_dir / file_name)

    metadata = {
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "feature_order": default_contract().feature_order,
        "model_type": "sklearn_logistic_regression",
        "artifact_packaging_version": ARTIFACT_PACKAGING_VERSION,
        "created_at": utc_now(),
    }
    (model_dir / "model_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    artifact_path = LOCAL_CACHE_DIR / "fraud_model.tar.gz"
    return _write_fraud_model_tarball(model_dir, artifact_path)


def create_fraud_source_dir_artifact(config: FraudAwsConfig | None = None) -> Path:
    config = config or load_fraud_aws_config(require_operational=False)
    model_dir = LOCAL_CACHE_DIR / "fraud_model_registry_model"
    code_dir = model_dir / "code"
    if not code_dir.exists():
        create_fraud_model_artifact(config)
    source_artifact_path = LOCAL_CACHE_DIR / "fraud_source_dir.tar.gz"
    return _write_fraud_source_dir_tarball(code_dir, source_artifact_path)


def upload_fraud_model_artifact(
    artifact_path: Path,
    config: FraudAwsConfig,
    clients: FraudAwsClients,
) -> str:
    key = config.s3_key(
        "model-registry",
        "artifacts",
        timestamp_slug(),
        "model.tar.gz",
    )
    clients.s3.upload_file(str(artifact_path), config.s3_bucket_name, key)
    return f"s3://{config.s3_bucket_name}/{key}"


def upload_fraud_source_dir_artifact(
    artifact_path: Path,
    config: FraudAwsConfig,
    clients: FraudAwsClients,
) -> str:
    key = config.s3_key(
        "model-registry",
        "source-dir",
        timestamp_slug(),
        "source_dir.tar.gz",
    )
    clients.s3.upload_file(str(artifact_path), config.s3_bucket_name, key)
    return f"s3://{config.s3_bucket_name}/{key}"


def ensure_model_package_group(
    config: FraudAwsConfig,
    clients: FraudAwsClients,
) -> str:
    sagemaker = clients.sagemaker
    group_name = config.fraud_model_package_group_name
    try:
        response = sagemaker.describe_model_package_group(
            ModelPackageGroupName=group_name
        )
        return str(response["ModelPackageGroupArn"])
    except Exception as exc:
        if client_error_code(exc) not in {"ValidationException", "ResourceNotFound"}:
            raise
    response = sagemaker.create_model_package_group(
        ModelPackageGroupName=group_name,
        ModelPackageGroupDescription=(
            "Modelo simple de fraude para el laboratorio 4 de despliegue ML en AWS."
        ),
        Tags=config.tags,
    )
    return str(response["ModelPackageGroupArn"])


def register_fraud_model_package(approval_status: str = "Approved") -> dict[str, Any]:
    config = load_fraud_aws_config(require_operational=False)
    clients = FraudAwsClients(config)
    artifact_path = create_fraud_model_artifact(config)
    source_dir_path = create_fraud_source_dir_artifact(config)
    artifact_s3_uri = upload_fraud_model_artifact(artifact_path, config, clients)
    source_dir_s3_uri = upload_fraud_source_dir_artifact(source_dir_path, config, clients)
    package_group_arn = ensure_model_package_group(config, clients)
    image_uri = get_sklearn_image_uri(config.lab_config)

    response = clients.sagemaker.create_model_package(
        ModelPackageGroupName=config.fraud_model_package_group_name,
        ModelPackageDescription=(
            "Modelo scikit-learn simple para inferencia de fraude con Feature Store."
        ),
        InferenceSpecification={
            "Containers": [
                {
                    "Image": image_uri,
                    "ModelDataUrl": artifact_s3_uri,
                }
            ],
            "SupportedContentTypes": ["application/json", "text/csv"],
            "SupportedResponseMIMETypes": ["application/json"],
            "SupportedRealtimeInferenceInstanceTypes": ["ml.m5.large"],
            "SupportedTransformInstanceTypes": ["ml.m5.large"],
        },
        ModelApprovalStatus=approval_status,
        CustomerMetadataProperties={
            "use_case": "fraud_detection",
            "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "lab": "04-model-deployment",
        },
    )
    metadata = {
        "model_package_group_name": config.fraud_model_package_group_name,
        "model_package_group_arn": package_group_arn,
        "model_package_arn": response["ModelPackageArn"],
        "model_artifact_s3_uri": artifact_s3_uri,
        "source_dir_s3_uri": source_dir_s3_uri,
        "image_uri": image_uri,
        "approval_status": approval_status,
        "artifact_packaging_version": ARTIFACT_PACKAGING_VERSION,
        "local_artifact_path": str(artifact_path.relative_to(ROOT_DIR)),
        "local_source_dir_path": str(source_dir_path.relative_to(ROOT_DIR)),
        "registered_at": utc_now(),
    }
    output_path = config.lab_config.metadata_path("fraud_model_registry.json")
    write_json(output_path, metadata)
    print("Modelo de fraude registrado en SageMaker Model Registry:")
    print(json.dumps(metadata, indent=2, sort_keys=True))
    print(
        "\nSugerencia: copia estos valores a .env si quieres reutilizarlos explicitamente:\n"
        f"FRAUD_MODEL_PACKAGE_GROUP_NAME={metadata['model_package_group_name']}\n"
        f"FRAUD_MODEL_PACKAGE_ARN={metadata['model_package_arn']}\n"
        f"FRAUD_MODEL_ARTIFACT_S3_URI={metadata['model_artifact_s3_uri']}"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and register a simple fraud model in SageMaker Model Registry."
    )
    parser.add_argument(
        "--approval-status",
        default="Approved",
        choices=["Approved", "Rejected", "PendingManualApproval"],
    )
    args = parser.parse_args()
    register_fraud_model_package(args.approval_status)


if __name__ == "__main__":
    main()
