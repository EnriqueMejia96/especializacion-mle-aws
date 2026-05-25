from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from src.aws_clients import client_error_code, clients
from src.cleanup_batch_resources import _delete_s3_prefix
from src.config import GENERATED_ENV_FILE, LOCAL_CACHE_DIR, LOCAL_OUTPUTS_DIR, ROOT_DIR, utc_now, write_json

from fraud_lab.aws.config import load_fraud_aws_config
from fraud_lab.aws.pipelines.cleanup_feature_store_aws import cleanup_feature_store_aws


def _delete_fraud_model_registry(config: Any, sagemaker: Any) -> dict[str, list[str]]:
    deleted: list[str] = []
    skipped: list[str] = []
    group_name = config.fraud_model_package_group_name

    try:
        sagemaker.describe_model_package_group(ModelPackageGroupName=group_name)
    except Exception as exc:
        if client_error_code(exc) in {"ValidationException", "ResourceNotFound"}:
            return {"deleted": deleted, "skipped": [group_name]}
        raise

    paginator = sagemaker.get_paginator("list_model_packages")
    package_arns: list[str] = []
    for page in paginator.paginate(ModelPackageGroupName=group_name):
        package_arns.extend(
            package["ModelPackageArn"] for package in page.get("ModelPackageSummaryList", [])
        )

    for package_arn in package_arns:
        try:
            sagemaker.delete_model_package(ModelPackageName=package_arn)
            deleted.append(package_arn)
        except Exception as exc:
            if client_error_code(exc) in {"ValidationException", "ResourceNotFound"}:
                skipped.append(package_arn)
            else:
                raise

    deadline = time.time() + 300
    while time.time() < deadline:
        remaining = sagemaker.list_model_packages(ModelPackageGroupName=group_name).get(
            "ModelPackageSummaryList",
            [],
        )
        if not remaining:
            break
        time.sleep(10)

    try:
        sagemaker.delete_model_package_group(ModelPackageGroupName=group_name)
        deleted.append(group_name)
    except Exception as exc:
        if client_error_code(exc) in {"ValidationException", "ResourceNotFound"}:
            skipped.append(group_name)
        else:
            raise
    return {"deleted": deleted, "skipped": skipped}


def _stack_details(cf: Any, stack_name: str) -> dict[str, Any]:
    try:
        stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    except Exception as exc:
        message = getattr(exc, "response", {}).get("Error", {}).get("Message", "")
        if "does not exist" in message or client_error_code(exc) == "ValidationError":
            return {}
        raise
    return {
        "outputs": {
            item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])
        },
        "parameters": {
            item["ParameterKey"]: item["ParameterValue"] for item in stack.get("Parameters", [])
        },
        "status": stack.get("StackStatus", ""),
    }


def _empty_lab_s3(config: Any, s3: Any, *, empty_stack_bucket: bool) -> dict[str, Any]:
    deleted_objects = 0
    prefixes = {
        config.lab_config.s3_prefix.strip("/") + "/",
        config.fraud_s3_prefix.strip("/") + "/",
    }
    prefixes_deleted: list[str] = []
    skipped: list[str] = []
    for prefix in sorted(prefixes):
        try:
            deleted_objects += _delete_s3_prefix(s3, config.s3_bucket_name, prefix.rstrip("/"))
            prefixes_deleted.append(prefix)
        except Exception as exc:
            if client_error_code(exc) in {"NoSuchBucket", "404"}:
                skipped.append(f"bucket_not_found:{config.s3_bucket_name}")
                return {
                    "bucket": config.s3_bucket_name,
                    "prefixes_requested": sorted(prefixes),
                    "prefixes_deleted": prefixes_deleted,
                    "empty_stack_bucket": empty_stack_bucket,
                    "deleted_objects": deleted_objects,
                    "skipped": skipped,
                }
            raise

    if empty_stack_bucket:
        paginator = s3.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=config.s3_bucket_name):
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if objects:
                    s3.delete_objects(Bucket=config.s3_bucket_name, Delete={"Objects": objects})
                    deleted_objects += len(objects)
        except Exception as exc:
            if client_error_code(exc) in {"NoSuchBucket", "404"}:
                skipped.append(f"bucket_not_found:{config.s3_bucket_name}")
            else:
                raise

    return {
        "bucket": config.s3_bucket_name,
        "prefixes_requested": sorted(prefixes),
        "prefixes_deleted": prefixes_deleted,
        "empty_stack_bucket": empty_stack_bucket,
        "deleted_objects": deleted_objects,
        "skipped": skipped,
    }


def _delete_stack(config: Any, cf: Any) -> dict[str, Any]:
    stack_name = config.lab_config.stack_name
    details = _stack_details(cf, stack_name)
    if not details:
        return {"deleted": [], "skipped": [stack_name]}
    cf.delete_stack(StackName=stack_name)
    cf.get_waiter("stack_delete_complete").wait(StackName=stack_name)
    return {
        "deleted": [stack_name],
        "skipped": [],
        "previous_status": details.get("status", ""),
    }


def _relative_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _safe_remove_path(path: Path) -> tuple[bool, str | None]:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True, None
    except FileNotFoundError:
        return False, None
    except (PermissionError, OSError) as exc:
        return False, f"{_relative_label(path)}: {exc}"


def _safe_clear_directory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"deleted": 0, "skipped": []}
    resolved_root = ROOT_DIR.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise RuntimeError(f"Refusing to delete outside lab root: {resolved_path}")

    deleted = 0
    skipped: list[str] = []
    for child in path.iterdir():
        if child.name == ".gitkeep":
            continue
        was_deleted, skip_reason = _safe_remove_path(child)
        if was_deleted:
            deleted += 1
        elif skip_reason:
            skipped.append(skip_reason)
    return {"deleted": deleted, "skipped": skipped}


def _delete_pycache() -> dict[str, Any]:
    deleted = 0
    skipped: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(ROOT_DIR):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in {".git", ".venv", "venv", "env"}
        ]
        if Path(dirpath).name != "__pycache__":
            continue
        was_deleted, skip_reason = _safe_remove_path(Path(dirpath))
        if was_deleted:
            deleted += 1
            dirnames[:] = []
        elif skip_reason:
            skipped.append(skip_reason)
    pytest_cache = ROOT_DIR / ".pytest_cache"
    if pytest_cache.exists():
        was_deleted, skip_reason = _safe_remove_path(pytest_cache)
        if was_deleted:
            deleted += 1
        elif skip_reason:
            skipped.append(skip_reason)
    return {"deleted": deleted, "skipped": skipped}


def cleanup_local_generated(*, delete_env_cloud: bool = True) -> dict[str, Any]:
    directories = [
        LOCAL_OUTPUTS_DIR,
        LOCAL_CACHE_DIR,
        ROOT_DIR / "data" / "lake",
        ROOT_DIR / "data" / "feature_store",
        ROOT_DIR / "data" / "operational",
        ROOT_DIR / "data" / "events",
        ROOT_DIR / "data" / "batch",
        ROOT_DIR / "data" / "retraining",
        ROOT_DIR / "artifacts" / "model",
        ROOT_DIR / "artifacts" / "preprocessing",
        ROOT_DIR / "artifacts" / "local_outputs",
    ]
    cleared = {str(path.relative_to(ROOT_DIR)): _safe_clear_directory(path) for path in directories}
    removed_files: list[str] = []
    skipped_files: list[str] = []
    if delete_env_cloud and GENERATED_ENV_FILE.exists():
        was_deleted, skip_reason = _safe_remove_path(GENERATED_ENV_FILE)
        if was_deleted:
            removed_files.append(str(GENERATED_ENV_FILE.relative_to(ROOT_DIR)))
        elif skip_reason:
            skipped_files.append(skip_reason)
    return {
        "cleared_directories": cleared,
        "removed_files": removed_files,
        "skipped_files": skipped_files,
        "removed_caches": _delete_pycache(),
    }


def full_cleanup_aws(
    *,
    delete_s3: bool = False,
    empty_stack_bucket: bool = False,
    delete_model_registry: bool = False,
    delete_stack: bool = False,
    delete_local: bool = False,
) -> dict[str, Any]:
    cloud_cleanup_requested = (
        delete_s3 or empty_stack_bucket or delete_model_registry or delete_stack
    )
    if delete_local and not cloud_cleanup_requested:
        result = {
            "local": cleanup_local_generated(),
            "completed_at": utc_now(),
        }
        print("Cleanup local fraud completado/solicitado:")
        print(json.dumps(result, indent=2, sort_keys=True))
        return result

    config = load_fraud_aws_config(require_operational=False)
    aws = clients(config.lab_config)
    result: dict[str, Any] = {
        "fraud_endpoint_and_feature_groups": cleanup_feature_store_aws(),
        "completed_at": utc_now(),
    }
    if delete_model_registry:
        result["model_registry"] = _delete_fraud_model_registry(config, aws.sagemaker)
    if delete_s3:
        result["s3"] = _empty_lab_s3(config, aws.s3, empty_stack_bucket=empty_stack_bucket)
    if delete_stack:
        result["cloudformation_stack"] = _delete_stack(config, aws.cloudformation)
    if delete_local:
        result["local"] = cleanup_local_generated()

    if not delete_local:
        write_json(config.lab_config.metadata_path("fraud_full_cleanup.json"), result)
    print("Full cleanup fraud completado/solicitado:")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cleanup total opcional del laboratorio cloud de fraude."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Atajo para --delete-model-registry --delete-s3 --delete-stack "
            "--delete-local. No activa --empty-stack-bucket."
        ),
    )
    parser.add_argument("--delete-s3", action="store_true", help="Borrar prefijos S3 del lab.")
    parser.add_argument(
        "--empty-stack-bucket",
        action="store_true",
        help="Vaciar completamente el bucket antes de borrar el stack. Usar solo si el bucket fue creado para este lab.",
    )
    parser.add_argument(
        "--delete-model-registry",
        action="store_true",
        help="Borrar Model Packages y Model Package Group de fraude.",
    )
    parser.add_argument("--delete-stack", action="store_true", help="Borrar stack CloudFormation.")
    parser.add_argument("--delete-local", action="store_true", help="Borrar archivos locales generados.")
    args = parser.parse_args()
    delete_model_registry = args.delete_model_registry or args.all
    delete_s3 = args.delete_s3 or args.all
    delete_stack = args.delete_stack or args.all
    delete_local = args.delete_local or args.all
    full_cleanup_aws(
        delete_s3=delete_s3,
        empty_stack_bucket=args.empty_stack_bucket,
        delete_model_registry=delete_model_registry,
        delete_stack=delete_stack,
        delete_local=delete_local,
    )


if __name__ == "__main__":
    main()
