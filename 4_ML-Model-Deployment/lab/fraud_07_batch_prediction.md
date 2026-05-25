# Fraud 07 - Batch prediction con Offline Store

## Objetivo

Ejecutar scoring batch usando transacciones curadas en S3 y features historicas desde Offline Store/export S3 con point-in-time joins.

## Que vas a construir o validar

Este paso valida:

- Lectura de `lake/curated/transactions_to_score.csv`.
- Lectura de features historicas desde `feature-store/offline-export/`.
- Point-in-time join por `event_time`.
- Ensamblaje del dataset model-ready.
- Predicciones batch educativas en S3 para trazabilidad.
- SageMaker Batch Transform Job real visible en la consola.
- Escritura de outputs en S3.

## Input del paso

Curated transactions:

```csv
transaction_id,user_id,card_id,merchant_id,device_id,amount,currency,timestamp
B001,U123,C789,M999,D123,500.0,PEN,2026-05-17T14:20:00Z
```

Offline export:

```csv
user_id,event_time,user_txn_count_1h,user_avg_amount_30d
U123,2026-05-17T14:00:00Z,4,87.5
U123,2026-05-17T15:00:00Z,7,91.0
```

Variables relevantes:

```bash
FRAUD_BATCH_INSTANCE_TYPE=ml.c6i.large,ml.m6i.large,ml.m5.xlarge,ml.m5.large
FRAUD_BATCH_INSTANCE_COUNT=1
FRAUD_REQUIRE_BATCH_TRANSFORM=false
```

## Output esperado del paso

Predicciones:

```text
s3://<bucket>/<prefix>/batch/predictions/batch_predictions.csv
```

Dataset model-ready:

```text
s3://<bucket>/<prefix>/batch/model-ready/batch_model_ready.csv
```

Input usado por SageMaker Batch Transform:

```text
s3://<bucket>/<prefix>/batch/transform-input/batch_transform_input.csv
```

Output producido por SageMaker Batch Transform:

```text
s3://<bucket>/<prefix>/batch/transform-output/<transform-job-name>/
```

Metadata local del job:

```text
artifacts/local_outputs/fraud_batch_transform_job.json
```

## Conceptos claves

Batch prediction no debe consultar Online Store registro por registro. Online Store esta optimizado para lookups de baja latencia en requests individuales, no para reconstruir historicos masivos.

Offline Store es la fuente correcta para batch porque guarda versiones historicas de features. Para cada transaccion, el pipeline debe usar la ultima feature con `feature_event_time <= transaction_event_time`. Esto evita usar informacion del futuro.

En produccion, SageMaker Batch Transform no lee directamente desde Feature Store ni desde Athena. Batch Transform consume archivos en S3. Por eso el patron recomendado es preparar primero un dataset model-ready desde Offline Store y luego entregarlo a Batch Transform:

```text
Feature Store Offline Store
  -> Glue Data Catalog / Athena, Glue, EMR o SageMaker Processing
  -> dataset model-ready en S3
  -> SageMaker Batch Transform
  -> predicciones en S3
```

Athena es comun cuando el Offline Store esta registrado en AWS Glue Data Catalog y el dataset se puede preparar con SQL. Permite filtrar particiones, unir Feature Groups, aplicar point-in-time joins y escribir resultados con CTAS o UNLOAD hacia S3. Para volumen alto o transformaciones complejas, Glue/Spark, EMR o SageMaker Processing suelen ser mejores opciones.

La buena practica no es leer manualmente los archivos internos del Offline Store por prefijo S3. Aunque fisicamente estan en S3, la tabla de Glue/Athena abstrae particiones, schema y evolucion de datos. El resultado final si debe quedar como archivo S3 limpio, estable y model-ready para Batch Transform.

SageMaker Batch Transform Job consume archivos en S3. En este laboratorio de fraude, el script cloud primero prepara el dataset model-ready desde curated data + Offline Store y despues lanza un Transform Job real usando el SageMaker Model registrado en el paso 04/desplegado en el paso 05.

No existe un "Batch Endpoint" persistente. Batch inference en SageMaker se observa como un **Batch Transform Job**. SageMaker crea capacidad temporal, procesa el archivo de entrada, escribe resultados en S3 y libera la capacidad al terminar.

El tipo de instancia del Transform Job se controla con `FRAUD_BATCH_INSTANCE_TYPE`. SageMaker Batch Transform no soporta `ml.t3.medium`; por eso el laboratorio usa una lista de candidatos validos. El script intenta crear el job con el primer tipo y, si AWS devuelve cuota 0 o una validacion de instancia, prueba el siguiente candidato.

Si todos los candidatos fallan, revisa Service Quotas buscando cuotas de **transform job usage**, no cuotas de training. Tambien puedes definir una lista explicita en `.env` con instancias disponibles para tu cuenta:

```bash
FRAUD_BATCH_INSTANCE_TYPE=ml.c6i.large,ml.m6i.large,ml.m5.xlarge
```

Por defecto `FRAUD_REQUIRE_BATCH_TRANSFORM=false`. Esto permite que el paso continue aunque la cuenta tenga cuota 0 para todos los candidatos: se generan `batch_model_ready.csv`, `batch_transform_input.csv`, predicciones educativas y metadata local con `status=SkippedQuotaUnavailable`. En ese caso no aparecera ningun job en la consola de SageMaker porque AWS no permitio crearlo.

Si necesitas que el laboratorio falle obligatoriamente cuando no se cree un job real, configura:

```bash
FRAUD_REQUIRE_BATCH_TRANSFORM=true
```

El archivo `batch/model-ready/batch_model_ready.csv` conserva encabezados y `transaction_id` para inspeccion humana. El archivo `batch/transform-input/batch_transform_input.csv` es el input operativo del Transform Job: no tiene encabezado y mantiene `transaction_id` como primera columna para trazabilidad por orden de linea. El contenedor ignora esa columna de ID y usa las features en el orden definido por `feature_order.json`.

El mismo contrato se usa en online y batch. `feature_order.json` evita que las columnas cambien de posicion. Las transformaciones compartidas reducen training-serving skew.

## Flujo detallado del paso

| Orden | Script o componente | Input principal | Recurso AWS usado | Output principal | Proposito |
| --- | --- | --- | --- | --- | --- |
| 1 | `fraud_lab.aws.pipelines.batch_prediction_aws` | `lake/curated/transactions_to_score.csv` | S3 | Filas curadas en memoria | Tomar el lote de transacciones a puntuar. |
| 2 | `AwsFeatureStore.read_offline_export` | IDs y timestamps de las transacciones | S3 Offline export | Historical/entity features point-in-time | Obtener features historicas sin consultar Online Store por cada fila. |
| 3 | `build_current_transaction_features` | Transaccion actual | Local | Current features | Calcular features que dependen solo del evento actual. |
| 4 | `assemble_feature_vector` | Current features + offline features | Local | Vector model-ready | Respetar el contrato y el orden de columnas. |
| 5 | `load_model_endpoint().predict` | Vector model-ready | Local | Predicciones educativas | Generar `batch_predictions.csv` para inspeccion del laboratorio. |
| 6 | `_run_sagemaker_batch_transform` | `batch_transform_input.csv` | SageMaker Batch Transform, S3 | Transform Job y output S3 | Ejecutar inferencia batch administrada por SageMaker cuando hay cuota disponible. |

El paso prepara dos salidas de inferencia: una educativa con encabezados y `transaction_id` para inspeccion humana, y una operativa sin encabezado para SageMaker Batch Transform.

## Paths principales

| Tipo | Ruta o recurso | Contenido esperado |
| --- | --- | --- |
| S3 input | `<FRAUD_S3_PREFIX>/lake/curated/transactions_to_score.csv` | Transacciones batch curadas. |
| S3 input | `<FRAUD_S3_PREFIX>/feature-store/offline-export/<feature-group>/features.csv` | Features historicas exportadas por Feature Store. |
| S3 output | `<FRAUD_S3_PREFIX>/batch/predictions/batch_predictions.csv` | Predicciones educativas con `transaction_id`, score y decision. |
| S3 output | `<FRAUD_S3_PREFIX>/batch/model-ready/batch_model_ready.csv` | Dataset model-ready con encabezados. |
| S3 output | `<FRAUD_S3_PREFIX>/batch/transform-input/batch_transform_input.csv` | Input operativo para SageMaker Batch Transform. |
| S3 output | `<FRAUD_S3_PREFIX>/batch/transform-output/<transform-job-name>/` | Respuesta nativa del Transform Job. |
| Metadata local | `artifacts/local_outputs/fraud_batch_transform_job.json` | Nombre, estado, instancia y outputs del Transform Job. |

## Ficha tecnica del paso

| Necesidad | Archivo donde revisar o cambiar |
| --- | --- |
| Cambiar flujo batch completo | `src/fraud_lab/aws/pipelines/batch_prediction_aws.py` |
| Cambiar tipos de instancia o si el job es obligatorio | `.env` y `src/fraud_lab/aws/config.py` |
| Cambiar lectura de Offline Store/export | `src/fraud_lab/aws/feature_store.py` |
| Cambiar current features batch | `src/fraud_lab/features/current_transaction_features.py` |
| Cambiar orden o contrato de features | `src/fraud_lab/features/feature_contract.py` y `src/fraud_lab/features/feature_vector.py` |
| Cambiar contenedor usado por Batch Transform | `src/fraud_lab/aws/sagemaker_inference/` |
| Cambiar creacion/reuso del SageMaker Model | `src/fraud_lab/aws/deploy_endpoint.py` |

## Prerrequisitos

- Haber ejecutado `fraud-step 02`.
- Haber ejecutado `fraud-step 03`.
- Haber ejecutado `fraud-step 04`.
- Offline exports disponibles en S3.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 07
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.batch_prediction_aws
```

## Resultado esperado

Se generan predicciones batch educativas y dataset model-ready en S3. Ademas, se crea un SageMaker Batch Transform Job real con nombre parecido a:

```text
ml-deploy-lab-fraud-batch-YYYYMMDDHHMMSS
```

Cada resultado educativo conserva `transaction_id`. El output nativo del Transform Job queda en S3 y puede reconstruirse contra el input por orden de linea.

Si la cuenta no tiene cuota de Batch Transform, el resultado esperado cambia a:

```text
transform_job_status=SkippedQuotaUnavailable
```

En ese caso los archivos S3 educativos se generan, pero no habra job visible en SageMaker hasta que solicites cuota o cambies a un tipo de instancia con cuota disponible.

## Validacion local

El stdout imprime las URIs de `predictions`, `model_ready`, `transform_input`, `transform_output`, y el nombre/estado de `transform_job_name`.

Tambien puedes revisar:

```bash
cat artifacts/local_outputs/fraud_batch_transform_job.json
```

## Validacion en consola AWS

En S3 revisa:

```text
<FRAUD_S3_PREFIX>/batch/predictions/batch_predictions.csv
<FRAUD_S3_PREFIX>/batch/model-ready/batch_model_ready.csv
<FRAUD_S3_PREFIX>/batch/transform-input/batch_transform_input.csv
<FRAUD_S3_PREFIX>/batch/transform-output/<transform-job-name>/
```

Abre el CSV y confirma que existe `transaction_id`, `fraud_score` y `decision`.

En SageMaker revisa:

```text
Amazon SageMaker AI -> Deployments & inference -> Batch transform jobs
```

Debes ver un job con prefijo parecido a:

```text
ml-deploy-lab-fraud-batch
```

El estado esperado es `Completed`. Si aparece `Failed`, abre el job y revisa `Failure reason` y CloudWatch Logs del job.

Si `artifacts/local_outputs/fraud_batch_transform_job.json` muestra `SkippedQuotaUnavailable`, no veras ningun job en esta pantalla. Eso indica que AWS rechazo la creacion por cuota antes de crear el recurso. Solicita incremento en Service Quotas para **transform job usage** o cambia `FRAUD_BATCH_INSTANCE_TYPE`.

## Problemas comunes

Si ves un error como:

```text
TypeError: Object of type datetime is not JSON serializable
```

el problema ocurre al guardar metadata local de `describe_transform_job`, porque boto3 devuelve campos de tipo `datetime`. El script normaliza esos campos antes de escribir `artifacts/local_outputs/fraud_batch_transform_job.json`. Vuelve a ejecutar:

```bash
python -m src.lab_runner fraud-step 07
```

Si el Transform Job anterior alcanzo a crearse en AWS antes del error, puede quedar visible en SageMaker. Esto no rompe el laboratorio; una nueva ejecucion creara un nuevo job con timestamp diferente.
