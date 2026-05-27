from __future__ import annotations

import json
import tarfile
from types import SimpleNamespace

from fraud_lab.aws.config import load_fraud_aws_config
from fraud_lab.aws.feature_store import AwsFeatureStore, _feature_type
from fraud_lab.aws.model_registry import (
    ARTIFACT_PACKAGING_VERSION,
    INFERENCE_SOURCE_FILES,
    SAGEMAKER_ENTRY_POINT,
    _fraud_training_rows,
    _write_fraud_model_tarball,
    _write_fraud_source_dir_tarball,
)
from fraud_lab.aws.operational_store import _to_dynamodb_value
from fraud_lab.features.feature_contract import default_contract


def _set_minimal_aws_env(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "standalone")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET_NAME", "fake-lab-bucket")
    monkeypatch.setenv(
        "SAGEMAKER_EXECUTION_ROLE_ARN",
        "arn:aws:iam::123456789012:role/fake-sagemaker-role",
    )
    monkeypatch.setenv("FRAUD_S3_PREFIX", "ml-deploy-lab/lab/fraud")
    monkeypatch.setenv("FRAUD_DECISION_TABLE_NAME", "fraud-decisions")
    monkeypatch.setenv(
        "FRAUD_EVENT_QUEUE_URL",
        "https://sqs.us-east-1.amazonaws.com/123456789012/fraud-events",
    )
    monkeypatch.setenv("FRAUD_FEATURE_GROUP_PREFIX", "ml-deploy-lab-fraud")
    monkeypatch.setenv("FRAUD_MODEL_PACKAGE_GROUP_NAME", "ml-deploy-lab-fraud-models")
    monkeypatch.setenv("FRAUD_MODEL_NAME", "ml-deploy-lab-fraud-model")
    monkeypatch.setenv("FRAUD_ENDPOINT_CONFIG_NAME", "ml-deploy-lab-fraud-realtime-config")
    monkeypatch.setenv("FRAUD_ENDPOINT_NAME", "ml-deploy-lab-fraud-realtime-endpoint")


def test_fraud_aws_config_builds_s3_uris_and_feature_group_names(monkeypatch):
    _set_minimal_aws_env(monkeypatch)

    config = load_fraud_aws_config()

    assert config.s3_uri("lake", "raw", "event.json") == (
        "s3://fake-lab-bucket/ml-deploy-lab/lab/fraud/lake/raw/event.json"
    )
    physical_name = config.physical_feature_group_name("user_profile_features")
    assert physical_name == "ml-deploy-lab-fraud-user-profile-features"
    assert "_" not in physical_name
    assert len(physical_name) <= 63
    assert config.fraud_model_package_group_name == "ml-deploy-lab-fraud-models"
    assert config.fraud_model_name == "ml-deploy-lab-fraud-model"
    assert config.fraud_endpoint_config_name == "ml-deploy-lab-fraud-realtime-config"
    assert config.fraud_endpoint_name == "ml-deploy-lab-fraud-realtime-endpoint"
    assert config.fraud_batch_instance_type == "ml.c6i.large,ml.m6i.large,ml.m5.xlarge,ml.m5.large"
    assert config.fraud_batch_instance_type_candidates == [
        "ml.c6i.large",
        "ml.m6i.large",
        "ml.m5.xlarge",
        "ml.m5.large",
    ]
    assert config.fraud_batch_instance_count == 1
    assert config.fraud_require_batch_transform is False


def test_feature_store_record_targets_online_and_offline(monkeypatch):
    _set_minimal_aws_env(monkeypatch)
    config = load_fraud_aws_config()
    captured = {}

    class Runtime:
        def put_record(self, **kwargs):
            captured.update(kwargs)

    fake_clients = SimpleNamespace(
        sagemaker=SimpleNamespace(),
        featurestore_runtime=Runtime(),
    )
    store = AwsFeatureStore(
        config=config,
        clients=fake_clients,
        s3_lake=SimpleNamespace(),
    )

    store.put_record(
        "user_behavior_features",
        {
            "user_id": "U123",
            "event_time": "2026-05-17T14:00:00Z",
            "user_txn_count_1h": 4,
            "user_avg_amount_30d": 87.5,
            "unexpected": "ignored",
        },
    )

    assert captured["FeatureGroupName"] == "ml-deploy-lab-fraud-user-behavior-features"
    assert captured["TargetStores"] == ["OnlineStore", "OfflineStore"]
    names = {item["FeatureName"] for item in captured["Record"]}
    assert "unexpected" not in names
    assert {"user_id", "event_time", "user_txn_count_1h", "user_avg_amount_30d"} <= names


def test_feature_types_match_sagemaker_feature_store_expectations():
    assert _feature_type("user_id") == "String"
    assert _feature_type("event_time") == "String"
    assert _feature_type("card_txn_count_5m") == "Integral"
    assert _feature_type("merchant_risk_score") == "Fractional"


def test_dynamodb_values_do_not_use_raw_float():
    converted = _to_dynamodb_value({"fraud_score": 0.87, "nested": [0.1]})

    assert str(converted["fraud_score"]) == "0.87"
    assert str(converted["nested"][0]) == "0.1"


def test_fraud_model_registry_training_rows_match_feature_contract():
    rows, labels = _fraud_training_rows()

    assert rows
    assert len(rows[0]) == len(default_contract().feature_order)
    assert set(labels) == {0, 1}


def test_fraud_model_artifact_and_source_dir_are_split_for_sagemaker_sklearn(tmp_path):
    model_dir = tmp_path / "model"
    code_dir = model_dir / "code"
    code_dir.mkdir(parents=True)
    (model_dir / "model.joblib").write_text("fake model", encoding="utf-8")
    (model_dir / "model_metadata.json").write_text(
        json.dumps({"artifact_packaging_version": ARTIFACT_PACKAGING_VERSION}),
        encoding="utf-8",
    )
    for file_name in INFERENCE_SOURCE_FILES:
        (code_dir / file_name).write_text("# fake inference file\n", encoding="utf-8")

    artifact_path = _write_fraud_model_tarball(
        model_dir,
        tmp_path / "fraud_model.tar.gz",
    )

    with tarfile.open(artifact_path, "r:gz") as tar:
        names = set(tar.getnames())
        metadata_file = tar.extractfile("model_metadata.json")
        assert metadata_file is not None
        metadata = json.loads(metadata_file.read().decode("utf-8"))

    assert "model.joblib" in names
    assert "model_metadata.json" in names
    assert SAGEMAKER_ENTRY_POINT not in names
    assert "code/inference.py" not in names
    assert "setup.py" not in names
    assert metadata["artifact_packaging_version"] == ARTIFACT_PACKAGING_VERSION

    source_artifact_path = _write_fraud_source_dir_tarball(
        code_dir,
        tmp_path / "fraud_source_dir.tar.gz",
    )
    with tarfile.open(source_artifact_path, "r:gz") as tar:
        source_names = set(tar.getnames())

    assert SAGEMAKER_ENTRY_POINT in source_names
    assert set(INFERENCE_SOURCE_FILES) <= source_names
    assert "setup.py" in source_names
    assert all(not name.startswith("code/") for name in source_names)
    assert "model.joblib" not in source_names
