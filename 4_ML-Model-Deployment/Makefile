.PHONY: help setup deploy-infra test list lab-list step lab-step lab all all-cloud cleanup fraud-cleanup full-cleanup fraud-full-cleanup fraud-full-cleanup-aws fraud-cloud-all fraud-aws-all fraud-deploy-infra fraud-generate-data-aws fraud-raw-to-cleaned-aws fraud-cleaned-to-curated-aws fraud-curated-to-offline-features-aws fraud-register-model-aws fraud-deploy-endpoint-aws fraud-online-score-aws fraud-async-update-aws fraud-batch-predict-aws fraud-build-retraining-dataset-aws

PYTHON ?= python

help:
	@echo "AWS ML Lab 04 - Fraud inference architecture"
	@echo "  make setup                         Install Python dependencies"
	@echo "  make list                          List numbered fraud steps"
	@echo "  make step STEP=05                  Run one numbered fraud step"
	@echo "  make all                           Run fraud steps 00-08 without cleanup"
	@echo "  make cleanup                       Delete fraud endpoint/model and Feature Groups"
	@echo "  make full-cleanup                  Delete cloud resources and local generated files"
	@echo "  make fraud-full-cleanup-aws ARGS='--all'  Optional full cloud/local teardown"
	@echo "  make fraud-cloud-all               Run the AWS fraud flow via lab_runner all"
	@echo "  make test                          Run unit tests"

setup:
	$(PYTHON) -m pip install -r requirements.txt

deploy-infra fraud-deploy-infra:
	$(PYTHON) -m src.deploy_infra

list lab-list:
	$(PYTHON) -m src.lab_runner list

step lab-step:
	$(PYTHON) -m src.lab_runner step $(STEP)

lab all all-cloud:
	$(PYTHON) -m src.lab_runner all

cleanup fraud-cleanup:
	$(PYTHON) -m src.lab_runner cleanup

full-cleanup fraud-full-cleanup:
	$(PYTHON) -m src.lab_runner full-cleanup

fraud-generate-data-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.generate_synthetic_data_aws

fraud-raw-to-cleaned-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.raw_to_cleaned_aws

fraud-cleaned-to-curated-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.cleaned_to_curated_aws

fraud-curated-to-offline-features-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.curated_to_offline_features_aws

fraud-register-model-aws:
	$(PYTHON) -m fraud_lab.aws.model_registry

fraud-deploy-endpoint-aws:
	$(PYTHON) -m fraud_lab.aws.deploy_endpoint

fraud-online-score-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.online_predict_aws

fraud-async-update-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.async_update_online_features_aws

fraud-batch-predict-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.batch_prediction_aws

fraud-build-retraining-dataset-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.build_retraining_dataset_aws

fraud-full-cleanup-aws:
	$(PYTHON) -m fraud_lab.aws.pipelines.full_cleanup_aws $(ARGS)

fraud-cloud-all fraud-aws-all:
	$(PYTHON) -m src.lab_runner all
	@echo "Flujo fraude cloud completado. No se destruyen recursos automaticamente."
	@echo "Ejecuta make cleanup para borrar endpoint/model/Feature Groups o make full-cleanup para teardown total."

test:
	$(PYTHON) -m pytest -q
