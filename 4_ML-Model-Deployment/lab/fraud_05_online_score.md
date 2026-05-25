# Fraud 05 - Deploy Real-Time Endpoint y online scoring

## Objetivo

Crear un SageMaker Model deployable desde el modelo registrado, desplegar un SageMaker Real-Time Endpoint y ejecutar una prediccion online de fraude usando Online Store y persistencia operacional en AWS.

## Que vas a construir o validar

Este paso valida:

- Limpieza y validacion del request.
- Feature engineering de la transaccion actual.
- Lookup de historical/entity features con SageMaker Feature Store Online Store.
- Ensamblaje del vector final con `feature_order.json`.
- SageMaker Model visible como modelo deployable.
- Endpoint Configuration con Production Variant y data capture.
- SageMaker Real-Time Endpoint real.
- Prediccion invocando SageMaker Runtime.
- Persistencia de trazas en S3.
- Decision operacional en DynamoDB.
- Evento asincrono en SQS.

## Input del paso

Transaccion default:

```json
{
  "transaction_id": "T001",
  "user_id": "U123",
  "card_id": "C789",
  "merchant_id": "M999",
  "device_id": "D123",
  "amount": "500",
  "currency": "pen",
  "category": "Electronics",
  "channel": "Mobile",
  "location": "Lima|PE",
  "timestamp": "17/05/2026 14:20"
}
```

Variables relevantes:

```bash
FRAUD_MODEL_NAME=ml-deploy-lab-fraud-model
FRAUD_ENDPOINT_CONFIG_NAME=ml-deploy-lab-fraud-realtime-config
FRAUD_USE_SAGEMAKER_ENDPOINT=true
FRAUD_ENDPOINT_NAME=ml-deploy-lab-fraud-realtime-endpoint
FRAUD_INSTANCE_TYPE=ml.m5.large
FRAUD_INITIAL_INSTANCE_COUNT=1
FRAUD_ENABLE_DATA_CAPTURE=true
FRAUD_DECISION_TABLE_NAME=<tabla>
FRAUD_EVENT_QUEUE_URL=<cola>
```

## Output esperado del paso

Respuesta tipo:

```json
{
  "transaction_id": "T001",
  "request_id": "REQ-...",
  "fraud_score": 0.87,
  "decision": "manual_review",
  "reason_codes": ["high_amount_vs_user_avg", "risky_merchant"],
  "model_version": "fraud_model_v1",
  "feature_version": "fraud_features_v1",
  "latency_ms": 142
}
```

Trazas en S3:

```text
operational/inference-logs/raw-events/
operational/inference-logs/cleaned-events/
operational/inference-logs/feature-vectors/
operational/inference-logs/predictions/
```

Metadata local:

```text
artifacts/local_outputs/fraud_sagemaker_model.json
artifacts/local_outputs/fraud_endpoint_config.json
artifacts/local_outputs/fraud_realtime_endpoint.json
```

## Conceptos claves

El Fraud Scoring Service es responsable de transformar el request en un payload model-ready. En esta arquitectura no se usa SageMaker Inference Pipeline; el endpoint recibe el vector final y solo predice.

El Fraud Scoring Service no es un SageMaker Job ni un recurso administrado de AWS. En este laboratorio es una capa de servicio escrita en Python en:

```text
src/fraud_lab/aws/scoring_service.py
```

Puedes pensarlo como el backend de una API de fraude. En produccion podria vivir en API Gateway + Lambda, ECS/Fargate, EKS, EC2, un microservicio FastAPI o una arquitectura equivalente. En el laboratorio corre desde tu terminal para que puedas ver todo el flujo sin desplegar una API adicional.

El servicio orquesta este flujo:

```text
Transaccion cruda
  -> limpieza y validacion
  -> calculo de current transaction features
  -> lectura de historical/entity features desde Online Store
  -> ensamble del vector con feature_order.json
  -> invocacion del SageMaker Real-Time Endpoint
  -> guardado de trazas en S3
  -> guardado de decision en DynamoDB
  -> emision de evento asincrono en SQS
```

El endpoint de SageMaker solo recibe el vector model-ready. No limpia la transaccion original, no consulta Feature Store, no escribe DynamoDB y no publica en SQS. Esa separacion es intencional: el endpoint se concentra en inferencia; el Fraud Scoring Service concentra la logica aplicativa y operacional.

Current transaction features se calculan en memoria porque dependen del evento actual y no requieren historia: `amount_normalized`, `hour_of_day`, `day_of_week`, `is_weekend`, one-hot de categoria, encoding de canal e indicador cross-border.

Historical/entity features se leen desde Online Store. Ejemplos: `user_avg_amount_30d`, `card_txn_count_5m`, `merchant_risk_score`, `device_trust_score`. Estas features representan conocimiento acumulado antes de que la transaccion actual llegue.

El paso anterior registra un modelo simple en Model Registry. Este paso lo convierte en un SageMaker Model deployable, crea una Endpoint Configuration y despliega un Real-Time Endpoint. Por eso en SageMaker Studio deberias ver el modelo en una zona de modelos deployables o como recurso SageMaker Model, ademas del Model Package registrado.

Registrar un modelo no lo despliega. Model Registry es gobierno/versionado. SageMaker Model es el recurso deployable. Endpoint Configuration define instancia, variantes y data capture. Real-Time Endpoint mantiene capacidad activa para inferencia de baja latencia.

Este endpoint genera costo mientras este activo. El cleanup de la ruta fraud borra endpoint, endpoint config y SageMaker Model, pero conserva Model Registry, S3, DynamoDB y SQS.

DynamoDB guarda la decision para consulta operacional. S3 guarda trazas para auditoria y replay. SQS publica un evento para que procesos posteriores actualicen lake y features sin bloquear la respuesta online.

Las trazas bajo `operational/inference-logs/` se generan automaticamente dentro del scoring service cada vez que se ejecuta este paso. No necesitas correr un script manual adicional. Son diferentes de CloudWatch Logs y de SageMaker Data Capture: son evidencia de negocio para reproducir una decision.

## Trazas de negocio vs SageMaker Data Capture

El laboratorio guarda trazas propias en S3 y tambien puede habilitar SageMaker Data Capture. No son lo mismo.

| Mecanismo | Quien lo genera | Donde queda | Que captura | Para que sirve |
| --- | --- | --- | --- | --- |
| Trazas de negocio del Fraud Scoring Service | `src/fraud_lab/aws/scoring_service.py` | `<FRAUD_S3_PREFIX>/operational/inference-logs/` | Evento crudo, evento limpio, vector de features, prediccion, warnings, decision y URIs relacionadas. | Auditoria de negocio, replay, debugging funcional, explicacion de una decision y continuidad hacia SQS. |
| SageMaker Data Capture | SageMaker Endpoint | `<FRAUD_S3_PREFIX>/data-capture/<FRAUD_ENDPOINT_NAME>/` | Request y response observados por el endpoint. En este lab, el endpoint ve principalmente el payload model-ready y la respuesta del modelo. | Monitoreo de modelo, analisis de drift, calidad de datos de entrada al endpoint e integracion con Model Monitor. |
| CloudWatch Logs | Contenedor del endpoint y servicios AWS | CloudWatch Logs | Logs tecnicos del contenedor, errores, arranque, invocaciones y stack traces. | Diagnostico tecnico del runtime. |

Data Capture no reemplaza las trazas del scoring service porque el endpoint no ve todo el proceso. Por ejemplo, Data Capture puede guardar el vector final enviado al endpoint, pero no necesariamente conserva el evento crudo original, la version limpia, las features historicas consultadas, los warnings de validacion, el item escrito en DynamoDB o el mensaje enviado a SQS.

Las trazas de negocio responden preguntas como:

- Que transaccion original llego?
- Como se limpio y normalizo?
- Que features se calcularon?
- Que features vinieron de Online Store?
- Que decision se guardo en DynamoDB?
- Que evento se mando a SQS?

Data Capture responde otra pregunta:

- Que input y output vio el endpoint de SageMaker?

En produccion normalmente se usan ambos. Data Capture es util para monitoreo administrado del endpoint; las trazas de negocio son utiles para auditoria, investigacion, reproducibilidad y cumplimiento.

## Reason codes

`reason_codes` son explicaciones operativas de por que una transaccion parece riesgosa. No son una funcionalidad nativa de SageMaker; en este laboratorio se generan en el codigo de inferencia:

```text
src/fraud_lab/aws/sagemaker_inference/predict_fn.py
```

Ejemplo de respuesta:

```json
{
  "decision": "manual_review",
  "fraud_score": 0.5785,
  "reason_codes": [
    "high_amount_vs_user_avg",
    "risky_merchant",
    "new_or_risky_device",
    "recent_card_declines"
  ]
}
```

Interpretacion:

| Reason code | Significado |
| --- | --- |
| `high_amount_vs_user_avg` | El monto de la transaccion es alto comparado con el promedio historico del usuario. |
| `risky_merchant` | El comercio tiene score de riesgo alto o senales historicas de fraude. |
| `new_or_risky_device` | El dispositivo tiene baja confianza, es nuevo o luce sospechoso. |
| `recent_card_declines` | La tarjeta tuvo rechazos recientes, lo que puede indicar pruebas o comportamiento anomalo. |
| `combined_risk_signal` | No hay una razon individual dominante, pero la combinacion de senales eleva el riesgo. |

Estos codigos ayudan a analistas, soporte, auditoria y sistemas posteriores a entender la decision. Por ejemplo, `manual_review` significa que el sistema no rechaza automaticamente, pero si recomienda revision humana por senales de riesgo.

En este laboratorio los `reason_codes` son reglas simples derivadas despues del scoring. No son SHAP values ni una explicacion completa del modelo. En produccion podrias reemplazarlos o complementarlos con tecnicas de interpretabilidad, politicas de negocio y razonadores especificos del dominio.

## SageMaker Inference Pipeline

Un SageMaker Inference Pipeline es un endpoint compuesto por varios contenedores ejecutados en secuencia dentro de una misma invocacion. El primer contenedor puede transformar el request, el segundo puede ejecutar el modelo y otro puede postprocesar la respuesta.

Inference Pipeline puede usarse tanto para predicciones real-time como para batch inference. No es exclusivo de Batch Transform. El mismo pipeline de contenedores puede servir en un Real-Time Endpoint o procesar archivos con SageMaker Batch Transform.

Ejemplo conceptual:

```text
Request JSON
  -> contenedor de preprocesamiento
  -> contenedor del modelo
  -> contenedor de postprocesamiento
  -> response JSON
```

Es recomendable cuando:

- El preprocesamiento debe viajar junto al modelo para evitar training-serving skew.
- El input del cliente requiere transformaciones relativamente estables y autocontenidas.
- Quieres versionar preprocesamiento + modelo como una unidad de despliegue.
- Necesitas que Batch Transform y Real-Time Endpoint compartan la misma logica de transformacion.
- El preprocesamiento no requiere llamadas externas complejas o de alta latencia.

No suele ser la mejor opcion cuando:

- Debes consultar varios sistemas externos por request, como Feature Store, DynamoDB, servicios antifraude o APIs internas.
- Necesitas guardar decisiones en DynamoDB, publicar eventos en SQS o escribir trazas de negocio complejas.
- La logica de negocio cambia con mas frecuencia que el modelo.
- Necesitas control fino de autenticacion, rate limits, idempotencia, retries o contratos de API.
- El flujo online incluye muchas responsabilidades que no son inferencia pura.

Por eso este laboratorio usa un Fraud Scoring Service fuera del endpoint. El servicio consulta Online Store, arma el vector y maneja persistencia operacional. El SageMaker Real-Time Endpoint se mantiene simple: recibe features model-ready y devuelve score/decision.

Una arquitectura alternativa tambien valida seria usar un Inference Pipeline para preprocesamiento + modelo, pero aun asi muchas empresas mantienen una capa de servicio externa para manejar autorizacion, trazas, decisiones, eventos y dependencias operacionales.

## Cuando usar `input_fn()` para preprocesamiento

`input_fn()` es parte del codigo de inferencia del contenedor. En este laboratorio vive en:

```text
src/fraud_lab/aws/sagemaker_inference/input_fn.py
```

Es recomendable poner transformaciones dentro de `input_fn()` cuando son ligeras, deterministicas, estables y forman parte del contrato del modelo.

Buenos casos para `input_fn()`:

- Parsear JSON o CSV.
- Validar campos requeridos del payload model-ready.
- Convertir tipos, por ejemplo string a float.
- Reordenar columnas usando `feature_order`.
- Completar valores faltantes con defaults versionados.
- Aplicar normalizaciones simples ya conocidas por el modelo.
- Aplicar one-hot encoding si las categorias son fijas y versionadas con el modelo.
- Soportar los mismos formatos en real-time y Batch Transform.

No conviene poner en `input_fn()` tareas que pertenecen al flujo de aplicacion:

- Consultar Feature Store Online Store.
- Consultar DynamoDB.
- Llamar APIs externas.
- Publicar eventos en SQS.
- Escribir trazas de negocio en S3.
- Hacer joins pesados o reconstruccion historica.
- Manejar autorizacion, rate limiting, idempotencia o retries de negocio.

Regla practica:

```text
Si la transformacion es parte del contrato del modelo, puede ir en input_fn() o en un Inference Pipeline.
Si la transformacion es parte del workflow de negocio, mantenla en un scoring service externo.
```

En este laboratorio, `input_fn()` interpreta payloads JSON/CSV model-ready. El enriquecimiento con Feature Store, la persistencia en DynamoDB, las trazas y el evento SQS quedan fuera del endpoint, en el Fraud Scoring Service.

## Que se guarda en DynamoDB

El servicio escribe una fila en la tabla `FRAUD_DECISION_TABLE_NAME`. La llave principal es `transaction_id`. El item contiene:

| Campo | Significado |
| --- | --- |
| `transaction_id` | Transaccion puntuada, por ejemplo `T001`. |
| `request_id` | ID unico de la ejecucion de scoring. |
| `decision` | Decision operacional: `approve`, `manual_review` o `reject`. |
| `fraud_score` | Probabilidad o score de fraude calculado por el modelo. |
| `model_version` | Version logica del modelo. |
| `feature_version` | Version logica del contrato de features. |
| `latency_ms` | Latencia medida por el servicio de scoring. |
| `payload` | Respuesta completa, incluyendo warnings, endpoint usado y reason codes. |

DynamoDB representa el estado operacional consultable por aplicaciones. Si un analista, API interna o sistema de pagos necesita saber rapidamente que ocurrio con `T001`, consulta DynamoDB por llave. No necesita leer S3 ni recorrer logs.

## Que se envia a SQS

Despues de guardar la decision, el servicio envia un mensaje a `FRAUD_EVENT_QUEUE_URL` con este patron:

```json
{
  "event_type": "fraud_prediction_completed",
  "raw_event": {},
  "cleaned_event": {},
  "prediction_event": {},
  "trace_uris": {}
}
```

Este mensaje no bloquea la respuesta online. El cliente ya recibio su decision. La cola solo avisa que existe trabajo posterior: persistir eventos asincronos, enriquecer el Data Lake y actualizar Feature Store para futuras transacciones.

En produccion, esta cola podria ser consumida por Lambda, ECS, Glue Streaming, Flink, Spark Structured Streaming o un worker propio. En el laboratorio, el consumidor es `fraud-step 06`.

## Flujo detallado del paso

| Orden | Script o componente | Input principal | Recurso AWS usado | Output principal | Proposito |
| --- | --- | --- | --- | --- | --- |
| 1 | `fraud_lab.aws.deploy_endpoint` | `.env`, `.env.cloud`, artefactos del paso 04 | SageMaker Model Registry, SageMaker Models, Endpoint Configurations, Endpoints | Endpoint real-time `FRAUD_ENDPOINT_NAME` | Crear o reutilizar el recurso deployable que recibira payloads model-ready. |
| 2 | `fraud_lab.aws.pipelines.online_predict_aws` | Transaccion default o archivo de entrada opcional | SageMaker Runtime, Feature Store Runtime, DynamoDB, SQS, S3 | Respuesta JSON con `fraud_score`, `decision` y trazas | Ejecutar una prediccion online completa desde una transaccion de negocio. |
| 3 | `AwsFraudScoringService.score_transaction` | Evento transaccional crudo | Online Store, Endpoint real-time, DynamoDB, SQS, S3 | Decision operacional persistida | Orquestar limpieza, feature engineering, lookup de features, invocacion del endpoint y persistencia. |

El endpoint no recibe directamente el evento crudo del sistema transaccional. El servicio de scoring primero limpia el evento, calcula current features, consulta Online Store, arma el vector con `feature_order.json` y recien despues invoca SageMaker Runtime.

## Paths principales

| Tipo | Ruta o recurso | Contenido esperado |
| --- | --- | --- |
| Metadata local | `artifacts/local_outputs/fraud_sagemaker_model.json` | Nombre del SageMaker Model creado o reutilizado. |
| Metadata local | `artifacts/local_outputs/fraud_endpoint_config.json` | Endpoint Configuration y Production Variant. |
| Metadata local | `artifacts/local_outputs/fraud_realtime_endpoint.json` | Estado del endpoint real-time. |
| S3 | `<FRAUD_S3_PREFIX>/operational/inference-logs/raw-events/` | Evento original recibido por el scoring service. |
| S3 | `<FRAUD_S3_PREFIX>/operational/inference-logs/cleaned-events/` | Evento normalizado. |
| S3 | `<FRAUD_S3_PREFIX>/operational/inference-logs/feature-vectors/` | Vector model-ready enviado al endpoint. |
| S3 | `<FRAUD_S3_PREFIX>/operational/inference-logs/predictions/` | Resultado de inferencia y decision operacional. |
| S3 | `<FRAUD_S3_PREFIX>/data-capture/<FRAUD_ENDPOINT_NAME>/` | Captura administrada de input/output del endpoint si `FRAUD_ENABLE_DATA_CAPTURE=true`. |
| DynamoDB | `FRAUD_DECISION_TABLE_NAME` | Ultima decision consultable por `transaction_id`. |
| SQS | `FRAUD_EVENT_QUEUE_URL` | Evento pendiente para procesamiento asincrono del paso 06. |
| SageMaker | `FRAUD_ENDPOINT_NAME` | Endpoint real-time en estado `InService`. |

## Ficha tecnica del paso

| Necesidad | Archivo donde revisar o cambiar |
| --- | --- |
| Cambiar nombre, instancia o data capture del endpoint | `src/fraud_lab/aws/config.py` y `.env` |
| Cambiar creacion de SageMaker Model, Endpoint Configuration o Endpoint | `src/fraud_lab/aws/deploy_endpoint.py` |
| Cambiar el flujo online completo | `src/fraud_lab/aws/scoring_service.py` |
| Cambiar limpieza y validacion de requests | `src/fraud_lab/common/cleaning.py` y `src/fraud_lab/common/validation.py` |
| Cambiar current transaction features | `src/fraud_lab/features/current_transaction_features.py` |
| Cambiar ensamble y orden del vector | `src/fraud_lab/features/feature_vector.py` y `artifacts/preprocessing/feature_order.json` |
| Cambiar lectura de Online Store | `src/fraud_lab/aws/feature_store.py` |
| Cambiar persistencia operacional | `src/fraud_lab/aws/operational_store.py` |
| Cambiar evento asincrono | `src/fraud_lab/aws/event_bus.py` |
| Cambiar logica del contenedor de inferencia | `src/fraud_lab/aws/sagemaker_inference/` |

## Prerrequisitos

- Haber ejecutado `fraud-step 01`.
- Haber ejecutado `fraud-step 03` para tener Online Store cargado.
- Haber ejecutado `fraud-step 04` para registrar el modelo en Model Registry.
- Tabla DynamoDB y cola SQS disponibles desde `.env.cloud`.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 05
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.deploy_endpoint
python -m fraud_lab.aws.pipelines.online_predict_aws
```

## Resultado esperado

Se crea o reutiliza el endpoint real-time. Se imprime una prediccion JSON. Se crea un item en DynamoDB y un mensaje en SQS. S3 recibe los logs de raw event, cleaned event, feature vector y prediction event.

## Validacion local

El stdout debe incluir metadata del endpoint y luego `fraud_score`, `decision`, `trace_uris` y `async_message_id`.

## Validacion en consola AWS

Revisa:

- DynamoDB: item con `transaction_id=T001`.
- SQS: un mensaje disponible antes de ejecutar `fraud-step 06`.
- S3: logs bajo `operational/inference-logs/`.
- SageMaker Models: modelo `FRAUD_MODEL_NAME` como recurso deployable.
- SageMaker Endpoints: endpoint `FRAUD_ENDPOINT_NAME` en estado `InService`.
- SageMaker Feature Store: registros consultables para `U123`, `C789`, `M999`, `D123`.

### Probar el endpoint desde SageMaker Studio Playground

En SageMaker Studio puedes abrir:

```text
Deployments -> Endpoints -> ML Deploy Lab Fraud Realtime Endpoint -> Playground
```

Selecciona:

```text
Testing option: Test the sample request
Content type: application/json
```

El Playground invoca directamente el SageMaker Real-Time Endpoint. Por eso no debes enviar la transaccion cruda del sistema transaccional. En esta pantalla no se ejecuta el Fraud Scoring Service, no se consulta Online Store y no se arma el vector de features. Para esta prueba debes enviar un payload model-ready, es decir, el vector final que normalmente construiria el servicio de scoring despues de limpiar la transaccion, consultar Feature Store y ordenar las features con `feature_order.json`.

Payload recomendado:

```json
{
  "features": {
    "amount_normalized": 500.0,
    "currency_normalized_amount": 500.0,
    "hour_of_day": 14,
    "day_of_week": 6,
    "is_weekend": 1,
    "category_electronics": 1,
    "category_travel": 0,
    "category_grocery": 0,
    "channel_mobile": 1,
    "channel_web": 0,
    "is_cross_border": 0,
    "account_age_days": 730,
    "customer_segment_premium": 1,
    "user_txn_count_1h": 4,
    "user_avg_amount_30d": 87.5,
    "card_txn_count_5m": 3,
    "card_declined_count_1h": 2,
    "merchant_fraud_rate_30d": 0.032,
    "merchant_risk_score": 0.71,
    "device_users_count_7d": 8,
    "device_trust_score": 0.35
  }
}
```

Respuesta esperada:

```json
[
  {
    "fraud_score": 0.87,
    "score": 0.87,
    "predicted_label": 1,
    "decision": "reject",
    "reason_codes": [
      "high_amount_vs_user_avg",
      "risky_merchant",
      "new_or_risky_device",
      "recent_card_declines"
    ],
    "model_version": "fraud_model_v1",
    "feature_version": "fraud_features_v1"
  }
]
```

El valor exacto de `fraud_score` puede variar ligeramente porque el modelo es entrenado durante el laboratorio, pero el formato debe conservarse: score numerico, label, decision, reason codes y versiones de modelo/features.

Si la invocacion funciona, CloudWatch Logs debe mostrar una linea parecida a:

```text
POST /invocations HTTP/1.1" 200
```

Si el Playground devuelve:

```json
{
  "body": "Content type is not supported for display.",
  "contentType": "text/html; charset=utf-8",
  "invokedProductionVariant": "AllTraffic"
}
```

significa que Studio no pudo mostrar la respuesta por el `Content-Type` recibido. Esto suele ocurrir cuando el cliente envia un encabezado `Accept` que el `output_fn()` no maneja como JSON. El codigo del laboratorio devuelve `application/json` desde:

```text
src/fraud_lab/aws/sagemaker_inference/output_fn.py
```

Si acabas de actualizar el codigo de inferencia, vuelve a ejecutar `fraud-step 04` para registrar un nuevo `source_dir.tar.gz` y luego redeploya el endpoint con `fraud-step 05`. El endpoint que ya estaba en `InService` no cambia automaticamente hasta recrearlo o actualizarlo.

Para recrear solo los recursos deployables del endpoint sin borrar Feature Store, usa:

```bash
python -m fraud_lab.aws.deploy_endpoint --cleanup
python -m src.lab_runner fraud-step 05
```

No uses `fraud-cleanup` para este caso si quieres conservar los Feature Groups, porque ese cleanup tambien elimina Feature Store del caso de fraude.

Esta prueba valida solo el endpoint. Para validar la arquitectura online completa usa `python -m src.lab_runner fraud-step 05`, porque ese paso ejecuta el Fraud Scoring Service, consulta Online Store, persiste logs en S3, guarda la decision en DynamoDB y emite el evento SQS.

Para ver las trazas en S3:

```bash
set -a
source .env
source .env.cloud
set +a

aws s3 ls "s3://$S3_BUCKET_NAME/$FRAUD_S3_PREFIX/operational/inference-logs/" --recursive --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Para verificar SQS antes de `fraud-step 06`:

```bash
set -a
source .env
source .env.cloud
set +a

aws sqs get-queue-attributes --queue-url "$FRAUD_EVENT_QUEUE_URL" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Para verificar DynamoDB por CLI:

```bash
set -a
source .env
source .env.cloud
set +a

aws dynamodb get-item --table-name "$FRAUD_DECISION_TABLE_NAME" --key '{"transaction_id":{"S":"T001"}}' --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Si el comando de S3 lista todos los buckets en lugar de objetos bajo `operational/inference-logs/`, normalmente `S3_BUCKET_NAME` esta vacio. Si SQS responde `NonExistentQueue`, valida que `FRAUD_EVENT_QUEUE_URL` venga de `.env.cloud`. Si DynamoDB indica `Invalid length for parameter TableName`, `FRAUD_DECISION_TABLE_NAME` esta vacio.
