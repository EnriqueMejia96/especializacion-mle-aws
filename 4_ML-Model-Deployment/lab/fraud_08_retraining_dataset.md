# Fraud 08 - Retraining dataset

## Objetivo

Construir un dataset supervisado de retraining usando transacciones historicas, labels tardios y features point-in-time desde Offline Store/export S3.

## Que vas a construir o validar

Este paso valida:

- Lectura de transacciones historicas curadas.
- Lectura de labels de fraude.
- Point-in-time joins con features historicas.
- Union de labels posteriores a la transaccion.
- Escritura de dataset supervisado en S3.

## Input del paso

Transacciones historicas:

```csv
transaction_id,event_time,user_id,card_id,merchant_id,device_id,amount
T001,2026-05-17T14:20:00Z,U123,C789,M999,D123,500.0
```

Labels:

```csv
transaction_id,label,label_source,label_event_time
T001,1,chargeback,2026-05-20T10:00:00Z
```

## Output esperado del paso

Dataset:

```text
s3://<bucket>/<prefix>/retraining/training_dataset.csv
```

## Conceptos claves

En fraude, el label suele llegar despues de la transaccion. Puede venir de chargeback, disputa del cliente, revision manual o investigacion posterior. Por eso el dataset de entrenamiento une features disponibles al momento de la transaccion con labels observados dias despues.

El punto critico es evitar data leakage. Si una transaccion ocurrio a las 14:20, no se pueden usar features calculadas a las 15:00. El modelo entrenaria con informacion que no existia en produccion y luego fallaria al desplegarse.

Offline Store permite reconstruir el estado historico de features. El point-in-time join selecciona la version mas reciente antes o igual al timestamp de la transaccion.

El dataset de retraining debe respetar el mismo `feature_order.json` usado por online y batch. Esto mantiene consistencia entre entrenamiento, batch inference y real-time inference.

## Flujo detallado del paso

| Orden | Script o componente | Input principal | Recurso AWS usado | Output principal | Proposito |
| --- | --- | --- | --- | --- | --- |
| 1 | `fraud_lab.aws.pipelines.build_retraining_dataset_aws` | `lake/curated/historical_transactions.csv` | S3 | Transacciones historicas en memoria | Recuperar la base historica de eventos. |
| 2 | `build_retraining_dataset_aws` | `lake/curated/fraud_labels.csv` | S3 | Labels por `transaction_id` | Agregar supervision observada despues del evento. |
| 3 | `AwsFeatureStore.get_many_offline_for_transaction` | Entidades y `event_time` | Offline export en S3 | Historical/entity features point-in-time | Reconstruir el estado que existia al momento de la transaccion. |
| 4 | `build_current_transaction_features` | Transaccion historica | Local | Current features | Calcular features deterministicas del evento. |
| 5 | `assemble_feature_vector` | Current features + offline features + label | Local | Dataset supervisado | Mantener el mismo contrato usado en online y batch. |
| 6 | `S3DataLake.put_csv` | Dataset final | S3 | `retraining/training_dataset.csv` | Dejar el dataset listo para un futuro pipeline de entrenamiento. |

El paso no entrena un modelo nuevo. Solo construye el dataset que un pipeline de entrenamiento posterior podria consumir.

## Paths principales

| Tipo | Ruta o recurso | Contenido esperado |
| --- | --- | --- |
| S3 input | `<FRAUD_S3_PREFIX>/lake/curated/historical_transactions.csv` | Transacciones historicas curadas. |
| S3 input | `<FRAUD_S3_PREFIX>/lake/curated/fraud_labels.csv` | Labels tardios de fraude. |
| S3 input | `<FRAUD_S3_PREFIX>/feature-store/offline-export/<feature-group>/features.csv` | Features historicas disponibles para point-in-time join. |
| S3 output | `<FRAUD_S3_PREFIX>/retraining/training_dataset.csv` | Dataset supervisado de retraining. |

## Ficha tecnica del paso

| Necesidad | Archivo donde revisar o cambiar |
| --- | --- |
| Cambiar construccion del dataset de retraining | `src/fraud_lab/aws/pipelines/build_retraining_dataset_aws.py` |
| Cambiar logica reusable de retraining local | `src/fraud_lab/pipelines/build_retraining_dataset.py` |
| Cambiar features historicas o point-in-time lookup | `src/fraud_lab/aws/feature_store.py` |
| Cambiar current features | `src/fraud_lab/features/current_transaction_features.py` |
| Cambiar contrato u orden de columnas | `src/fraud_lab/features/feature_contract.py` y `src/fraud_lab/features/feature_vector.py` |
| Cambiar lectura/escritura del Data Lake | `src/fraud_lab/aws/s3_data_lake.py` |

## Prerrequisitos

- Haber ejecutado `fraud-step 02`.
- Haber ejecutado `fraud-step 03`.
- Labels disponibles en `lake/curated/fraud_labels.csv`.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 08
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.build_retraining_dataset_aws
```

## Resultado esperado

Se genera `training_dataset.csv` con `transaction_id`, `event_time`, todas las features del contrato y columnas de label.

## Validacion local

El stdout imprime la URI de `training_dataset`.

## Validacion en consola AWS

En S3 revisa:

```text
<FRAUD_S3_PREFIX>/retraining/training_dataset.csv
```

Confirma que contiene:

- `label`
- `label_source`
- `label_event_time`
- features del contrato
- `transaction_id`

