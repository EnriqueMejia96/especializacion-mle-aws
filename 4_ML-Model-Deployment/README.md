# Lab 04 - Arquitectura de inferencia de fraude en AWS

Este laboratorio implementa una arquitectura cloud para deteccion de fraude en transacciones de tarjeta usando servicios AWS. La ruta activa es la ruta de fraude; los pasos genericos previos del laboratorio fueron retirados para evitar duplicidad y mantener una ejecucion clara.

## Objetivo

Construir y validar un flujo de inferencia con:

- Data Lake en Amazon S3: raw, cleaned y curated.
- SageMaker Feature Store: Online Store para inferencia online y Offline Store para batch/retraining.
- SageMaker Model Registry con un modelo simple de fraude versionado.
- SageMaker Real-Time Endpoint para scoring online.
- SageMaker Batch Transform Job para batch prediction cuando la cuenta tenga cuota disponible.
- SQS para eventos asincronos.
- DynamoDB para decisiones operacionales.
- CloudWatch Logs y metricas para revisar endpoint y jobs.

DynamoDB y SQS se crean en el paso 01, pero se usan realmente despues del scoring online. DynamoDB guarda la decision final por `transaction_id` para consulta operacional de baja latencia. SQS recibe un evento `fraud_prediction_completed` para que el paso 06 actualice S3 y Feature Store sin bloquear la respuesta del endpoint.

## Arquitectura

Flujo online:

```text
Transaccion
  -> Fraud Scoring Service
  -> current transaction features en memoria
  -> lookup de historical/entity features en Feature Store Online Store
  -> vector model-ready
  -> SageMaker Real-Time Endpoint
  -> decision + logs + evento SQS
```

Flujo asincrono:

```text
SQS event
  -> pipeline async
  -> S3 raw/cleaned/curated
  -> actualizacion de Feature Store Online/Offline para futuras predicciones
```

Flujo batch:

```text
S3 curated transactions
  -> Offline Store export / point-in-time features
  -> batch model-ready input
  -> SageMaker Batch Transform Job
  -> predicciones en S3
```

## Pasos del laboratorio

La documentacion activa vive en `lab/`:

- `lab/fraud_00_architecture.md`
- `lab/fraud_01_aws_setup.md`
- `lab/fraud_02_data_lake.md`
- `lab/fraud_03_feature_store.md`
- `lab/fraud_04_model_registry.md`
- `lab/fraud_05_online_score.md`
- `lab/fraud_06_async_update.md`
- `lab/fraud_07_batch_prediction.md`
- `lab/fraud_08_retraining_dataset.md`
- `lab/fraud_09_cleanup.md`

## Instalacion

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuracion

Copia `.env.example` a `.env` si necesitas fijar `AWS_PROFILE`, `AWS_REGION` o parametros del laboratorio:

```bash
cp .env.example .env
```

El paso 01 crea o actualiza infraestructura base y genera `.env.cloud` con valores como:

- `S3_BUCKET_NAME`
- `SAGEMAKER_EXECUTION_ROLE_ARN`
- `FRAUD_S3_PREFIX`
- `FRAUD_DECISION_TABLE_NAME`
- `FRAUD_EVENT_QUEUE_URL`
- `FRAUD_EVENT_QUEUE_NAME`

No hardcodees credenciales. Usa AWS profiles, variables de entorno o roles IAM.

## Ejecucion por pasos

Listar pasos:

```bash
python -m src.lab_runner list
```

Ejecutar paso por paso:

```bash
python -m src.lab_runner step 00
python -m src.lab_runner step 01
python -m src.lab_runner step 02
python -m src.lab_runner step 03
python -m src.lab_runner step 04
python -m src.lab_runner step 05
python -m src.lab_runner step 06
python -m src.lab_runner step 07
python -m src.lab_runner step 08
python -m src.lab_runner step 09   # cleanup conservador
python -m src.lab_runner step 10   # cleanup total
```

Ejecutar todo el flujo sin cleanup:

```bash
python -m src.lab_runner all
```

Al finalizar, el runner escribe el resumen de tiempos en:

```text
artifacts/local_outputs/lab_execution_times.json
```

Ese archivo incluye `started_at`, `finished_at`, `total_duration_seconds` y la duracion de cada paso ejecutado. Si un paso falla, el runner tambien intenta guardar el reporte parcial con el estado `failed`.

Cleanup explicito de endpoint/model/Feature Groups:

```bash
python -m src.lab_runner cleanup
```

Cleanup total de recursos cloud del laboratorio y archivos locales generados:

```bash
python -m src.lab_runner full-cleanup
```

## Scripts conservados

Solo se mantienen scripts relacionados con la ruta de fraude:

| Script | Estado | Uso |
| --- | --- | --- |
| `scripts/lab.sh` | Activo | Wrapper Linux/macOS/Git Bash para `src.lab_runner`. |
| `scripts/lab.ps1` | Activo | Wrapper Windows PowerShell para `src.lab_runner`. |
| `scripts/deploy_infra.sh` | Activo | Atajo para ejecutar solo `python -m src.deploy_infra`. |
| `scripts/deploy_infra.ps1` | Activo | Version PowerShell del despliegue de infraestructura. |
| `scripts/fraud_cloud_all.sh` | Activo, delegado | Ejecuta `python -m src.lab_runner all`. |
| `scripts/fraud_cloud_all.ps1` | Activo, delegado | Version PowerShell de `python -m src.lab_runner all`. |

`fraud_cloud_all.sh` y `fraud_cloud_all.ps1` delegan en `python -m src.lab_runner all`. Esto evita mantener dos secuencias distintas y garantiza que tambien se genere `artifacts/local_outputs/lab_execution_times.json`.

Los targets directos del `Makefile` como `fraud-register-model-aws`, `fraud-batch-predict-aws` o `fraud-build-retraining-dataset-aws` se conservan para debugging y reejecucion puntual. La secuencia oficial del estudiante sigue siendo `python -m src.lab_runner all` o `python -m src.lab_runner step <numero>`.

Ejemplos:

```bash
bash scripts/lab.sh list
bash scripts/lab.sh step 05
bash scripts/lab.sh full-cleanup
bash scripts/fraud_cloud_all.sh
```

Windows PowerShell:

```powershell
scripts\lab.ps1 list
scripts\lab.ps1 step 05
scripts\lab.ps1 full-cleanup
scripts\fraud_cloud_all.ps1
```

## Makefile

Comandos principales:

```bash
make setup
make list
make step STEP=05
make all
make cleanup
make full-cleanup
make fraud-cloud-all
make fraud-full-cleanup-aws ARGS="--all"
make test
```

## Validacion en AWS

Revisa en consola:

- S3: prefijo `s3://<bucket>/<FRAUD_S3_PREFIX>/`.
- SageMaker Feature Store: grupos con prefijo `FRAUD_FEATURE_GROUP_PREFIX`.
- SageMaker Model Registry: grupo `FRAUD_MODEL_PACKAGE_GROUP_NAME`.
- SageMaker Models/Deployable: modelo `FRAUD_MODEL_NAME`.
- SageMaker Endpoints: endpoint `FRAUD_ENDPOINT_NAME`.
- SageMaker Batch transform jobs: job con prefijo `ml-deploy-lab-fraud-batch` si la cuenta tiene cuota de Transform Job.
- DynamoDB: tabla `FRAUD_DECISION_TABLE_NAME`.
- SQS: cola `FRAUD_EVENT_QUEUE_NAME`.
- CloudWatch Logs: logs del endpoint y jobs.

## Costos y cleanup

SageMaker Real-Time Endpoint genera costo mientras esta activo. Ejecuta cleanup al terminar:

```bash
python -m src.lab_runner cleanup
```

Para borrar recursos adicionales de gobierno, S3, stack y archivos locales:

```bash
python -m src.lab_runner full-cleanup
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.full_cleanup_aws --all
```

`--all` no vacia completamente el bucket por seguridad. Si el bucket fue creado exclusivamente para este laboratorio y CloudFormation no puede borrar el stack porque quedan objetos, usa:

```bash
python -m fraud_lab.aws.pipelines.full_cleanup_aws --all --empty-stack-bucket
```

## Notas importantes

- Batch inference usa `SageMaker Batch Transform Job`, no "Batch Endpoint".
- `ml.t3.medium` no es valido para Batch Transform. Usa candidatos como `ml.c6i.large`, `ml.m6i.large`, `ml.m5.xlarge` o `ml.m5.large` segun cuotas.
- Si la cuenta tiene cuota 0 para Transform Job, el paso 07 genera outputs educativos en S3 y registra `SkippedQuotaUnavailable`, salvo que `FRAUD_REQUIRE_BATCH_TRANSFORM=true`.
- El Playground de SageMaker invoca directamente el endpoint con payload model-ready. No ejecuta el Fraud Scoring Service ni consulta Online Store.
