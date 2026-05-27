# Fraud 03 - SageMaker Feature Store Online y Offline

## Objetivo

Crear y cargar Feature Groups de fraude en SageMaker Feature Store con Online Store y Offline Store.

Este paso convierte datos business-ready del Data Lake en datos ML-ready organizados por entidad, tiempo y contrato de features.

## Que vas a construir o validar

Este paso crea o reutiliza Feature Groups para:

- `user_profile_features`
- `user_behavior_features`
- `card_velocity_features`
- `merchant_risk_features`
- `device_features`
- `last_transaction_features`

Cada Feature Group tiene:

- Record identifier: llave de entidad, por ejemplo `user_id`, `card_id`, `merchant_id` o `device_id`.
- `event_time`: timestamp que indica desde cuando ese registro de features es valido.
- Online Store habilitado para lookup de baja latencia.
- Offline Store en S3 para historico.
- Export CSV controlado para batch prediction y retraining.

## Input del paso

Requiere:

```bash
S3_BUCKET_NAME=<bucket>
SAGEMAKER_EXECUTION_ROLE_ARN=<role>
FRAUD_S3_PREFIX=ml-deploy-lab/lab/fraud
FRAUD_FEATURE_GROUP_PREFIX=ml-deploy-lab-fraud
```

Registros base de ejemplo:

```json
{
  "user_id": "U123",
  "event_time": "2026-05-17T14:00:00Z",
  "user_txn_count_1h": 4,
  "user_avg_amount_30d": 87.5,
  "user_max_amount_30d": 520.0
}
```

## Output esperado del paso

Feature Groups fisicos con nombres como:

```text
ml-deploy-lab-fraud-user-profile-features
ml-deploy-lab-fraud-user-behavior-features
ml-deploy-lab-fraud-card-velocity-features
ml-deploy-lab-fraud-merchant-risk-features
ml-deploy-lab-fraud-device-features
ml-deploy-lab-fraud-last-transaction-features
```

Exports S3:

```text
s3://<bucket>/<prefix>/feature-store/offline-export/user_behavior_features/features.csv
s3://<bucket>/<prefix>/feature-store/offline-export/card_velocity_features/features.csv
```

## Ejemplos de Feature Groups

| Feature Group | Entidad | Ejemplo de feature | Definicion | Uso en el laboratorio |
| --- | --- | --- | --- | --- |
| `user_profile_features` | `user_id` | `account_age_days` | Dias desde la creacion de la cuenta del usuario. Es una feature estatica o de cambio lento. | Se usa como senal de madurez de la cuenta. Cuentas muy nuevas pueden aportar mas riesgo que cuentas antiguas. |
| `user_behavior_features` | `user_id` | `user_avg_amount_30d` | Promedio del monto transaccional del usuario durante los ultimos 30 dias. | Permite comparar la transaccion actual contra el comportamiento normal del usuario. Alimenta razonamientos como `high_amount_vs_user_avg`. |
| `card_velocity_features` | `card_id` | `card_txn_count_5m` | Numero de transacciones recientes de la tarjeta en una ventana de 5 minutos. | Representa velocidad. Muchas transacciones en poco tiempo elevan el riesgo de fraude o abuso automatizado. |
| `merchant_risk_features` | `merchant_id` | `merchant_risk_score` | Score normalizado de riesgo del comercio, calculado desde fraude historico, chargebacks, categoria y perfil del merchant. | Eleva el score cuando la transaccion ocurre en un comercio riesgoso. Puede generar reason code `risky_merchant`. |
| `device_features` | `device_id` | `device_trust_score` | Score de confianza del dispositivo. Valores bajos indican dispositivo nuevo, compartido, sospechoso o con baja reputacion. | Reduce o aumenta riesgo segun la confiabilidad del dispositivo. Puede activar `new_or_risky_device`. |
| `last_transaction_features` | `user_id` | `last_transaction_country` | Ultimo pais observado para una transaccion del usuario. | Se actualiza de forma asincrona para futuras predicciones. No se guarda para leerlo inmediatamente en la misma transaccion. |

## Conceptos claves

Online Store guarda el ultimo valor disponible por entidad. Es la fuente para inferencia online porque permite `GetRecord` de baja latencia. En este caso, una transaccion con `user_id=U123`, `card_id=C789`, `merchant_id=M999` y `device_id=D123` consulta features de usuario, tarjeta, comercio y dispositivo antes de invocar el modelo.

Offline Store guarda historico por `event_time`. Es la fuente correcta para batch prediction y retraining porque permite reconstruir que features existian al momento de una transaccion pasada. Esta propiedad es clave para evitar data leakage.

Un Feature Group no representa una tabla transaccional completa. Representa un conjunto de features con una entidad y una semantica comun. Por eso el usuario aparece en dos grupos distintos: `user_profile_features` para atributos lentos y `user_behavior_features` para agregaciones dinamicas.

La columna `event_time` no es decorativa. Para online indica la frescura del registro disponible. Para offline permite point-in-time joins: si una transaccion ocurrio a las 14:20, se debe usar el snapshot de features con `event_time <= 14:20`, no un valor calculado a las 15:00.

Un snapshot es el valor de un registro de features en un momento especifico. Por ejemplo, `user_behavior_features` puede tener varias filas para `U123` con distintos `event_time`:

```text
user_id | event_time           | user_txn_count_1h | user_avg_amount_30d
U123    | 2026-05-17T13:00:00Z | 2                 | 80.0
U123    | 2026-05-17T14:00:00Z | 4                 | 87.5
U123    | 2026-05-17T15:00:00Z | 7                 | 91.0
U456    | 2026-05-17T14:00:00Z | 1                 | 65.0
```

En este ejemplo hay cuatro registros historicos, pero no cuatro usuarios. `U123` tiene tres snapshots porque su comportamiento cambio con el tiempo. Online Store esta optimizado para recuperar el ultimo estado por entidad. Offline Store conserva todos los snapshots historicos para reconstruir correctamente el pasado.

Si una transaccion de `U123` ocurrio a las 14:20, el dataset batch o de entrenamiento debe usar el snapshot de las 14:00. No debe usar el de las 15:00 porque ese valor aun no existia al momento de la transaccion y produciria data leakage.

El laboratorio tambien escribe un `offline-export` en S3. SageMaker Batch Transform consume archivos S3; no consulta Online Store registro por registro. En produccion, ese export puede venir de Athena, Glue, SageMaker Processing o consultas sobre la tabla del Offline Store.

`last_transaction_features` muestra un patron valido de escritura directa a Online Store: guardar datos que seran utiles para futuras predicciones. No representa el patron incorrecto de guardar una current feature solo para leerla inmediatamente.

## Contrato de features y orden del vector

El paso publica dos artefactos de contrato:

| Artefacto | Rol | Quien lo usa |
|---|---|---|
| `feature_contract.yaml` | Describe el feature set, version de modelo/features, features calculadas desde la transaccion actual, features leidas desde Online Store y valores por defecto. | Model Registry, servicio online, batch prediction, retraining y documentacion del contrato. |
| `feature_order.json` | Define el orden exacto de columnas que recibe el modelo. | Endpoint real-time, Batch Transform, batch educativo y dataset de retraining. |

El contrato evita que entrenamiento, batch y real-time armen vectores distintos. Si una columna cambia de posicion, el modelo puede interpretar `merchant_risk_score` como si fuera `device_trust_score`, por ejemplo. Por eso `feature_order.json` es tan importante como el modelo.

Ejemplo resumido de `feature_contract.yaml`:

```yaml
feature_set: fraud_realtime_v1
model_name: fraud_model_simulator
model_version: fraud_model_v1
feature_version: fraud_features_v1

current_transaction_features:
  - name: amount_normalized
    type: float
    default: 0.0
  - name: hour_of_day
    type: int
    default: 0
  - name: category_electronics
    type: int
    default: 0

online_store_features:
  - name: user_avg_amount_30d
    feature_group: user_behavior_features
    entity_key: user_id
    type: float
    default: 0.0
  - name: merchant_risk_score
    feature_group: merchant_risk_features
    entity_key: merchant_id
    type: float
    default: 0.0

feature_order:
  - amount_normalized
  - currency_normalized_amount
  - hour_of_day
  - day_of_week
```

Ejemplo resumido de `feature_order.json`:

```json
[
  "amount_normalized",
  "currency_normalized_amount",
  "hour_of_day",
  "day_of_week",
  "is_weekend",
  "category_electronics",
  "category_travel",
  "category_grocery",
  "channel_mobile",
  "channel_web",
  "is_cross_border",
  "account_age_days",
  "customer_segment_premium",
  "user_txn_count_1h",
  "user_avg_amount_30d",
  "card_txn_count_5m",
  "card_declined_count_1h",
  "merchant_fraud_rate_30d",
  "merchant_risk_score",
  "device_users_count_7d",
  "device_trust_score"
]
```

Si agregas una feature nueva, actualiza el contrato, el orden, la definicion del Feature Group y el codigo que ensambla el vector. Despues vuelve a entrenar o validar el modelo, porque el input del modelo cambio.

## Ingesta inicial y patron de produccion

En este laboratorio, `fraud-step 03` crea los Feature Groups y hace una ingesta inicial usando codigo Python local. El comando ejecuta `fraud_lab.aws.pipelines.curated_to_offline_features_aws`, que internamente usa `AwsFeatureStore.seed_feature_store()`.

Nota de implementacion: aunque el nombre del modulo contiene `curated`, la version actual del laboratorio no recalcula todas las features leyendo directamente `lake/curated/historical_transactions.csv`. Para mantener el paso corto y facil de inspeccionar, carga registros semilla definidos en `src/fraud_lab/feature_store/seed_feature_store.py`. Ese archivo simula features historicas ya calculadas por una capa previa de feature engineering.

Ese flujo hace tres cosas:

1. Crea o reutiliza los Feature Groups fisicos en SageMaker Feature Store.
2. Escribe registros iniciales con `PutRecord` en Online Store y Offline Store.
3. Escribe exports CSV controlados bajo `feature-store/offline-export/` para los pasos batch y retraining del laboratorio.

En un caso productivo, la ingesta inicial y las actualizaciones recurrentes normalmente se separan:

| Necesidad productiva | Patron recomendado |
|---|---|
| Carga inicial historica | Job batch con Glue, EMR, Spark o SageMaker Processing que lee curated data, calcula features y escribe a Feature Store. |
| Actualizacion cada cierto tiempo | EventBridge Scheduler -> SageMaker Processing o Glue Job -> transformaciones compartidas -> `PutRecord`/batch ingestion. |
| Actualizacion near-real-time | Kinesis, MSK, Lambda, Flink o ECS worker -> transformaciones online -> Feature Store Online Store y persistencia historica. |
| Consistencia entrenamiento-inferencia | Codigo de transformacion compartido, contrato versionado y pruebas de schema. |

La buena practica es centralizar las transformaciones de features en funciones reutilizables. Asi el pipeline batch, el consumidor streaming y el servicio online calculan las mismas columnas con las mismas reglas.

## Offline Store nativo vs offline export del laboratorio

Hay dos rutas relacionadas con datos offline:

| Ruta | Quien la crea | Tipo | Para que sirve |
|---|---|---|---|
| `feature-store/offline-store/<group>/` | SageMaker Feature Store | Offline Store nativo | Historico administrado por Feature Store. Puede consultarse con Glue/Athena cuando se usan tablas nativas. |
| `feature-store/offline-export/<group>/features.csv` | Codigo del laboratorio | Export CSV custom | Facilita batch prediction y retraining sin obligar al estudiante a consultar Parquet/Athena. |

El Offline Store nativo se activa con `OfflineStoreConfig` al crear el Feature Group. Ademas, el codigo configura `DisableGlueTableCreation=False`, por lo que SageMaker puede crear tablas en AWS Glue Data Catalog para consultar el historico.

El `offline-export` no es una caracteristica automatica de SageMaker Feature Store. Es una salida custom del lab creada por `AwsFeatureStore.replace_offline_export()` y `AwsFeatureStore.append_offline_export()`. Sirve para que los pasos 07 y 08 puedan leer CSVs simples y concentrarse en point-in-time joins, batch inference y retraining.

## Como interpretar `record_counts`

Al final del comando veras una seccion como:

```json
"record_counts": {
  "card_velocity_features": 2,
  "device_features": 2,
  "merchant_risk_features": 2,
  "user_behavior_features": 4,
  "user_profile_features": 2
}
```

Estos numeros son la cantidad de registros iniciales escritos por Feature Group durante la semilla del laboratorio. No significan necesariamente la cantidad de entidades unicas visibles en Online Store.

Ejemplo:

| Feature Group | Conteo | Interpretacion |
|---|---:|---|
| `user_profile_features` | 2 | Dos usuarios con perfil inicial: `U123` y `U456`. |
| `user_behavior_features` | 4 | Cuatro snapshots historicos de comportamiento. `U123` tiene varios `event_time`; `U456` tiene uno. |
| `card_velocity_features` | 2 | Dos tarjetas con features de velocidad: `C789` y `C101`. |
| `merchant_risk_features` | 2 | Dos comercios con riesgo historico: `M999` y `M111`. |
| `device_features` | 2 | Dos dispositivos con reputacion: `D123` y `D555`. |

Online Store esta optimizado para recuperar el estado mas reciente por entidad. Offline Store conserva el historico por `event_time`. Por eso `user_behavior_features` puede tener 4 registros offline, pero al consultar Online Store para `U123` normalmente veras el ultimo snapshot disponible.

`last_transaction_features` se crea como Feature Group, pero no aparece en `record_counts` durante la carga inicial porque no tiene registros semilla. Se empieza a poblar cuando ejecutas el paso 06, que procesa eventos SQS posteriores a una prediccion online.

## Como se usa en los siguientes pasos

- Paso 05: el Fraud Scoring Service consulta Online Store para armar el vector online.
- Paso 06: el pipeline asincrono actualiza algunos Feature Groups para futuras transacciones.
- Paso 07: batch prediction usa exports de Offline Store y no hace lookups online fila por fila.
- Paso 08: retraining usa Offline Store con point-in-time joins y labels tardios.

## Flujo detallado del paso

| Orden | Script | Input local | Input S3/AWS | Output local | Output S3/AWS | Proposito |
|---:|---|---|---|---|---|---|
| 1 | `fraud_lab.aws.pipelines.curated_to_offline_features_aws` | `.env`, `.env.cloud` | Curated data y role de SageMaker | Ninguno obligatorio | JSON en stdout con grupos fisicos y conteos | Orquestar creacion y carga de Feature Store. |
| 2 | `AwsFeatureStore.create_all_feature_groups` | Definiciones en `src/fraud_lab/feature_store/feature_groups.py` | SageMaker Feature Store, S3 | Ninguno | Feature Groups fisicos con Online/Offline Store | Crear grupos por entidad. |
| 3 | `AwsFeatureStore.seed_feature_store` | Baseline records en `seed_feature_store.py` | Feature Store Runtime | Ninguno | Registros en Online Store y Offline Store | Cargar features iniciales. |
| 4 | `AwsFeatureStore.replace_offline_export` | Registros baseline | S3 | Ninguno | `feature-store/offline-export/<group>/features.csv` | Crear export didactico para batch/retraining. |
| 5 | `AwsFeatureStore.upload_contract_artifacts` | Contrato default | S3 | Ninguno | `feature_contract.yaml`, `feature_order.json` | Publicar contrato compartido. |

## Paths principales

| Tipo | Path | Quien lo crea | Quien lo consume |
|---|---|---|---|
| Definicion de grupos | `src/fraud_lab/feature_store/feature_groups.py` | Codigo fuente | `AwsFeatureStore`. |
| Registros iniciales | `src/fraud_lab/feature_store/seed_feature_store.py` | Codigo fuente | `seed_feature_store`. |
| Feature Groups fisicos | `ml-deploy-lab-fraud-*-features` | `AwsFeatureStore.create_feature_group` | Online scoring, batch y retraining. |
| Offline Store nativo | `s3://<bucket>/<FRAUD_S3_PREFIX>/feature-store/offline-store/<group>/` | SageMaker Feature Store | Auditoria historica y Glue/Athena si se consulta la tabla nativa. |
| Offline export controlado | `s3://<bucket>/<FRAUD_S3_PREFIX>/feature-store/offline-export/<group>/features.csv` | `AwsFeatureStore.replace_offline_export` | Batch prediction y retraining del laboratorio. |
| Contrato de features | `s3://<bucket>/<FRAUD_S3_PREFIX>/artifacts/preprocessing/feature_contract.yaml` | Paso 02 y paso 03 | Model Registry, endpoint, batch y retraining. |

## Prerrequisitos

- Haber ejecutado `fraud-step 01`.
- Haber ejecutado `fraud-step 02` para tener Data Lake y artifacts.
- Permisos para `sagemaker:CreateFeatureGroup`, `sagemaker:PutRecord`, `sagemaker:GetRecord` y S3.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 03
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.curated_to_offline_features_aws
```

## Resultado esperado

Feature Store queda cargado con registros historicos y ultimos valores por entidad. Los exports offline quedan disponibles para los pasos batch y retraining.

## Validacion local

El comando imprime un JSON con `physical_feature_groups`, `record_counts`, `offline_exports_prefix` y artifacts.

## Validacion en consola AWS

Revisa en SageMaker Feature Store:

- Estado `Created`.
- Online Store habilitado.
- Offline Store apuntando a S3.
- Record identifier correcto para cada grupo.
- `event_time` como Event time feature.

Revisa en S3:

```text
<FRAUD_S3_PREFIX>/feature-store/offline-export/
<FRAUD_S3_PREFIX>/feature-store/offline-store/
```

Si `CreateFeatureGroup` falla con `s3:GetBucketAcl`, actualiza la infraestructura con:

```bash
python -m src.lab_runner fraud-step 01
```

Luego ejecuta otra vez:

```bash
python -m src.lab_runner fraud-step 03
```

El paso intenta eliminar y recrear Feature Groups que hayan quedado en `CreateFailed`.

## Ficha tecnica del paso

| Componente | Ruta | Responsabilidad | Entradas | Salidas |
|---|---|---|---|---|
| Orquestador cloud | `src/fraud_lab/aws/pipelines/curated_to_offline_features_aws.py` | Ejecutar `AwsFeatureStore.seed_feature_store`. | Configuracion AWS. | Resumen con `physical_feature_groups`, `record_counts`, `offline_exports_prefix`. |
| Feature Store AWS | `src/fraud_lab/aws/feature_store.py` | Crear Feature Groups, hacer `PutRecord`, `GetRecord`, exports y point-in-time lookup. | Config, definiciones de grupos, registros baseline. | Online Store, Offline Store, exports S3. |
| Definiciones | `src/fraud_lab/feature_store/feature_groups.py` | Declarar entidades, llaves y features por grupo. | Ninguno. | Estructura de Feature Groups. |
| Seed records | `src/fraud_lab/feature_store/seed_feature_store.py` | Generar registros base para usuario, tarjeta, merchant y device. | Contrato de features. | Registros listos para `PutRecord`. |
| Contrato ML | `src/fraud_lab/features/feature_contract.py` | Definir feature order y versionado. | Ninguno. | `feature_contract.yaml`, `feature_order.json`. |

Para agregar una feature nueva, actualiza `feature_groups.py`, `seed_feature_store.py`, `feature_contract.py` y las funciones que ensamblan el vector en `src/fraud_lab/features/feature_vector.py`. Si la feature se usa en batch/retraining, tambien valida el export offline.
