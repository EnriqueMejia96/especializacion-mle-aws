from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import LOCAL_OUTPUTS_DIR, ROOT_DIR, utc_now, write_json

Command = tuple[str, ...]


def resolve_python_executable() -> str:
    """Prefer the lab-local virtualenv for child commands when it exists."""
    candidates = (
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


@dataclass(frozen=True)
class LabStep:
    number: str
    slug: str
    title: str
    commands: tuple[Command, ...]

    @property
    def key(self) -> str:
        return f"{self.number}-{self.slug}"


FRAUD_STEPS: tuple[LabStep, ...] = (
    LabStep(
        "00",
        "fraud-architecture",
        "Arquitectura de fraude y mapeo local a AWS",
        (("message", "Documentacion: lab/fraud_00_architecture.md"),),
    ),
    LabStep(
        "01",
        "fraud-aws-setup",
        "Infraestructura AWS para fraude: S3, IAM, DynamoDB y SQS",
        (
            ("message", "Documentacion: lab/fraud_01_aws_setup.md"),
            ("src.deploy_infra",),
            ("src.config", "--check-aws"),
            ("message", "Infraestructura fraud cloud lista. Revisa .env.cloud para FRAUD_S3_PREFIX, FRAUD_DECISION_TABLE_NAME y FRAUD_EVENT_QUEUE_URL."),
        ),
    ),
    LabStep(
        "02",
        "fraud-data-lake",
        "Data Lake AWS: raw, cleaned y curated en S3",
        (
            ("message", "Documentacion: lab/fraud_02_data_lake.md"),
            ("fraud_lab.aws.pipelines.generate_synthetic_data_aws",),
            ("fraud_lab.aws.pipelines.raw_to_cleaned_aws",),
            ("fraud_lab.aws.pipelines.cleaned_to_curated_aws",),
        ),
    ),
    LabStep(
        "03",
        "fraud-feature-store",
        "SageMaker Feature Store Online Store y Offline Store",
        (
            ("message", "Documentacion: lab/fraud_03_feature_store.md"),
            ("fraud_lab.aws.pipelines.curated_to_offline_features_aws",),
        ),
    ),
    LabStep(
        "04",
        "fraud-model-registry",
        "Modelo simple de fraude en SageMaker Model Registry",
        (
            ("message", "Documentacion: lab/fraud_04_model_registry.md"),
            ("fraud_lab.aws.model_registry",),
        ),
    ),
    LabStep(
        "05",
        "fraud-online-score",
        "Deploy Real-Time Endpoint y prediccion online",
        (
            ("message", "Documentacion: lab/fraud_05_online_score.md"),
            ("fraud_lab.aws.deploy_endpoint",),
            ("fraud_lab.aws.pipelines.online_predict_aws",),
        ),
    ),
    LabStep(
        "06",
        "fraud-async-update",
        "Actualizacion asincrona con SQS, S3 y Feature Store",
        (
            ("message", "Documentacion: lab/fraud_06_async_update.md"),
            ("fraud_lab.aws.pipelines.async_update_online_features_aws",),
        ),
    ),
    LabStep(
        "07",
        "fraud-batch-prediction",
        "Batch prediction con Offline Store exportado a S3",
        (
            ("message", "Documentacion: lab/fraud_07_batch_prediction.md"),
            ("fraud_lab.aws.pipelines.batch_prediction_aws",),
        ),
    ),
    LabStep(
        "08",
        "fraud-retraining-dataset",
        "Dataset de retraining con point-in-time joins y labels",
        (
            ("message", "Documentacion: lab/fraud_08_retraining_dataset.md"),
            ("fraud_lab.aws.pipelines.build_retraining_dataset_aws",),
        ),
    ),
)

FRAUD_CLEANUP_STEP = LabStep(
    "09",
    "fraud-cleanup-feature-store",
    "Cleanup explicito de endpoint y Feature Groups del caso de fraude",
    (
        ("message", "Documentacion: lab/fraud_09_cleanup.md"),
        ("fraud_lab.aws.pipelines.cleanup_feature_store_aws",),
    ),
)

FRAUD_FULL_CLEANUP_STEP = LabStep(
    "10",
    "fraud-full-cleanup",
    "Cleanup total de recursos cloud y archivos locales del laboratorio",
    (
        ("message", "Documentacion: lab/fraud_09_cleanup.md"),
        ("fraud_lab.aws.pipelines.full_cleanup_aws", "--all"),
    ),
)


def run_command(command: Command) -> None:
    if command[0] == "message":
        print(command[1], flush=True)
        return
    python_executable = resolve_python_executable()
    print(f"$ {python_executable} -m {' '.join(command)}", flush=True)
    try:
        subprocess.run([python_executable, "-m", *command], cwd=ROOT_DIR, check=True)
    except subprocess.CalledProcessError as exc:
        joined = " ".join(command)
        raise SystemExit(
            f"El comando del paso fallo: python -m {joined}\n"
            "Revisa el mensaje anterior. Si era un comando cloud, valida .env, permisos IAM y region AWS."
        ) from exc


def find_fraud_step(identifier: str) -> LabStep:
    normalized = identifier.strip().lower()
    normalized = normalized.removeprefix("fraud-").removeprefix("f")
    for step in (*FRAUD_STEPS, FRAUD_CLEANUP_STEP, FRAUD_FULL_CLEANUP_STEP):
        aliases = {
            step.number,
            step.slug,
            step.key,
            f"fraud-{step.number}",
            f"fraud-{step.key}",
            f"f{step.number}",
        }
        if identifier.strip().lower() in aliases or normalized in aliases:
            return step
    valid = ", ".join(
        step.key for step in (*FRAUD_STEPS, FRAUD_CLEANUP_STEP, FRAUD_FULL_CLEANUP_STEP)
    )
    raise SystemExit(f"Unknown step '{identifier}'. Valid fraud steps: {valid}")


def run_step(step: LabStep) -> None:
    print(f"\n=== Lab 04 {step.key}: {step.title} ===", flush=True)
    for command in step.commands:
        run_command(command)


def _step_timing_payload(step: LabStep, started_at: str) -> dict[str, Any]:
    return {
        "number": step.number,
        "slug": step.slug,
        "key": step.key,
        "title": step.title,
        "started_at": started_at,
        "finished_at": "",
        "duration_seconds": 0.0,
        "status": "running",
    }


def run_timed_step(step: LabStep, timings: list[dict[str, Any]]) -> None:
    started_at = utc_now()
    started_monotonic = time.monotonic()
    payload = _step_timing_payload(step, started_at)
    try:
        run_step(step)
        payload["status"] = "succeeded"
    except BaseException as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        raise
    finally:
        payload["finished_at"] = utc_now()
        payload["duration_seconds"] = round(time.monotonic() - started_monotonic, 3)
        timings.append(payload)


def write_all_execution_times(
    *,
    timings: list[dict[str, Any]],
    started_at: str,
    total_started_monotonic: float,
    status: str,
) -> Path:
    total_duration = round(time.monotonic() - total_started_monotonic, 3)
    payload = {
        "lab": "04-model-deployment-fraud",
        "command": "python -m src.lab_runner all",
        "status": status,
        "started_at": started_at,
        "finished_at": utc_now(),
        "total_duration_seconds": total_duration,
        "steps_total": len(timings),
        "steps_succeeded": sum(1 for item in timings if item["status"] == "succeeded"),
        "steps_failed": sum(1 for item in timings if item["status"] == "failed"),
        "steps": timings,
        "cleanup_note": (
            "El comando all no ejecuta cleanup. Usa python -m src.lab_runner cleanup "
            "o python -m src.lab_runner full-cleanup al terminar."
        ),
    }
    path = LOCAL_OUTPUTS_DIR / "lab_execution_times.json"
    write_json(path, payload)
    print(f"\nExecution timing report written to {path}", flush=True)
    return path


def list_steps() -> None:
    print("Ruta fraud - arquitectura de inferencia con AWS:")
    list_fraud_steps()


def list_fraud_steps() -> None:
    for step in FRAUD_STEPS:
        print(f"{step.key}: {step.title}")
    print(f"{FRAUD_CLEANUP_STEP.key}: {FRAUD_CLEANUP_STEP.title}")
    print(f"{FRAUD_FULL_CLEANUP_STEP.key}: {FRAUD_FULL_CLEANUP_STEP.title}")


def run_all() -> None:
    run_fraud_all()


def run_fraud_all() -> None:
    timings: list[dict[str, Any]] = []
    started_at = utc_now()
    total_started_monotonic = time.monotonic()
    status = "succeeded"
    try:
        for step in FRAUD_STEPS:
            run_timed_step(step, timings)
    except BaseException:
        status = "failed"
        raise
    finally:
        write_all_execution_times(
            timings=timings,
            started_at=started_at,
            total_started_monotonic=total_started_monotonic,
            status=status,
        )
    print(
        "\nRuta fraud completada. El cleanup de Feature Groups esta separado: "
        "python -m src.lab_runner fraud-cleanup. Para teardown total: "
        "python -m src.lab_runner fraud-full-cleanup"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Lab 04 fraud architecture by complete flow or individual steps.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List available lab steps.")
    subparsers.add_parser("all", help="Run the full lab without cleanup.")
    step_parser = subparsers.add_parser("step", help="Run one lab step by number or name.")
    step_parser.add_argument("identifier", help="Example: 05, fraud-online-score")
    subparsers.add_parser("cleanup", help="Run fraud cleanup without deleting governance resources.")
    subparsers.add_parser("full-cleanup", help="Run full fraud cloud/local teardown.")
    subparsers.add_parser("fraud-list", help="List AWS fraud architecture steps.")
    subparsers.add_parser("fraud-all", help="Run the AWS fraud architecture flow without cleanup.")
    fraud_step_parser = subparsers.add_parser("fraud-step", help="Run one fraud step by number or name.")
    fraud_step_parser.add_argument("identifier", help="Example: 05, fraud-online-score")
    subparsers.add_parser("fraud-cleanup", help="Delete fraud Feature Groups created by the lab.")
    subparsers.add_parser("fraud-full-cleanup", help="Delete all fraud lab resources and local outputs.")
    args = parser.parse_args()

    if args.command == "list":
        list_steps()
    elif args.command == "all":
        run_all()
    elif args.command == "step":
        run_step(find_fraud_step(args.identifier))
    elif args.command == "cleanup":
        run_step(FRAUD_CLEANUP_STEP)
    elif args.command == "full-cleanup":
        run_step(FRAUD_FULL_CLEANUP_STEP)
    elif args.command == "fraud-list":
        list_fraud_steps()
    elif args.command == "fraud-all":
        run_fraud_all()
    elif args.command == "fraud-step":
        run_step(find_fraud_step(args.identifier))
    elif args.command == "fraud-cleanup":
        run_step(FRAUD_CLEANUP_STEP)
    elif args.command == "fraud-full-cleanup":
        run_step(FRAUD_FULL_CLEANUP_STEP)


if __name__ == "__main__":
    main()
