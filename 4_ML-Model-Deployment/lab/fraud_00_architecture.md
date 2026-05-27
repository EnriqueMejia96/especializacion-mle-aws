# Fraud 00 - Arquitectura de inferencia para fraude

## Objetivo

Entender la arquitectura completa de inferencia para deteccion de fraude con tarjetas de credito y como se mapea cada componente del laboratorio a un servicio AWS real.

Este paso no crea recursos. Define el mapa mental del laboratorio: que ocurre en el camino online, que ocurre de forma asincrona, que se usa para batch prediction y que se conserva para retraining.

## Que vas a construir o validar

Vas a validar la separacion entre:

- Transaccion actual recibida por una API.
- Current transaction features calculadas en memoria.
- Historical/entity features leidas desde SageMaker Feature Store Online Store.
- Data Lake en S3 con capas raw, cleaned y curated.
- Offline Store para batch prediction y retraining.
- SageMaker Model Registry para gobierno del modelo.
- SageMaker Real-Time Endpoint para inferencia online.
- SageMaker Batch Transform Job para inferencia batch cuando exista cuota disponible.
- DynamoDB como tabla operacional de decisiones.
- SQS como mecanismo asincrono para actualizar datos y features futuras.

## Input del paso

No crea recursos. El input conceptual es una transaccion como:

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

## Output esperado del paso

Comprender el flujo online principal:

```text
API / Fraud Scoring Service
  -> validar request
  -> limpiar y canonizar la transaccion
  -> calcular current transaction features en memoria
  -> consultar Online Store para features historicas/de entidad
  -> ensamblar feature vector segun feature_order.json
  -> invocar SageMaker Real-Time Endpoint
  -> guardar decision operacional en DynamoDB
  -> guardar trazas en S3
  -> emitir evento a SQS para procesamiento asincrono
```

## Flujo arquitectonico del laboratorio

| Paso | Capa arquitectonica | Servicio AWS principal | Que valida |
| --- | --- | --- | --- |
| 00 | Arquitectura | N/A | Define los limites entre Data Lake, Feature Store, Model Registry, endpoint, batch y eventos. |
| 01 | Infraestructura base | CloudFormation, S3, IAM, DynamoDB, SQS | Crea bucket, rol de SageMaker, tabla de decisiones y cola de eventos. |
| 02 | Data Lake | S3 | Genera raw, cleaned, curated, labels y artefactos de contrato de features. |
| 03 | Feature Store | SageMaker Feature Store | Crea Feature Groups con Online Store y Offline Store. |
| 04 | Gobierno del modelo | SageMaker Model Registry | Empaqueta modelo + codigo de inferencia y registra un Model Package aprobado. |
| 05 | Inferencia online | SageMaker Real-Time Endpoint, Feature Store Runtime, DynamoDB, SQS, S3 | Consulta Online Store, invoca endpoint, persiste trazas y emite evento. |
| 06 | Actualizacion asincrona | SQS, S3, Feature Store | Procesa eventos posteriores al scoring y actualiza features futuras. |
| 07 | Inferencia batch | S3, Offline Store export, SageMaker Batch Transform Job | Usa Offline Store y point-in-time joins para scoring batch. |
| 08 | Retraining dataset | S3, Offline Store export | Une transacciones historicas, features historicas y labels tardios. |
| 09 | Cleanup | SageMaker, Feature Store | Elimina endpoint/model/Feature Groups del caso de fraude. |

## Conceptos claves

La transaccion actual trae proto-features: `amount`, `currency`, `timestamp`, `location`, `category`, `channel` y llaves de entidad como `user_id`, `card_id`, `merchant_id` y `device_id`. Algunas features se pueden calcular inmediatamente desde ese payload: `amount_normalized`, `hour_of_day`, `is_weekend`, `category_electronics`, `channel_mobile` o `is_cross_border`.

Las features historicas no deberian recalcularse en el camino online. En una arquitectura real, agregaciones como `user_txn_count_1h`, `card_txn_count_5m`, `merchant_risk_score` o `device_trust_score` ya deben existir en Online Store. El servicio de scoring solo las busca con una llave de entidad y ensambla el vector final.

El Online Store no debe usarse como paso temporal para guardar y leer la misma transaccion. Si el servicio calcula `hour_of_day=14`, lo usa directamente en memoria. Guardarlo en Online Store para leerlo inmediatamente agregaria latencia, dependencia operacional y riesgo de fallo sin aportar valor a la prediccion actual.

El Data Lake y Feature Store no son lo mismo. El Data Lake conserva eventos y tablas de negocio en S3. Feature Store publica datos ML-ready, versionados por `event_time` y organizados por Feature Group. Curated es business-ready; Feature Store es model-ready.

El Model Registry tampoco es un endpoint. El Registry gobierna versiones aprobadas del modelo. Desplegar requiere crear un SageMaker Model deployable y luego usarlo en un Real-Time Endpoint o en un Batch Transform Job.

SQS separa el tiempo de respuesta online del mantenimiento de datos. La prediccion debe responder rapido; la actualizacion del Data Lake y de features para futuras transacciones puede ocurrir segundos despues.

## Rol de DynamoDB y SQS en la arquitectura

DynamoDB y SQS aparecen juntos en el camino online, pero resuelven problemas distintos.

| Servicio | Rol en el laboratorio | Por que no usar solo S3 o Feature Store |
| --- | --- | --- |
| DynamoDB | Guarda la decision operacional por `transaction_id`: score, decision, version de modelo/features, latencia y payload de respuesta. | S3 es durable pero no esta optimizado para consultas puntuales de baja latencia por transaccion. Feature Store guarda features, no decisiones finales del negocio. |
| SQS | Recibe un evento `fraud_prediction_completed` despues de la prediccion online. Ese evento activa procesamiento asincrono en el paso 06. | El endpoint no debe esperar a que se actualicen raw/cleaned/curated ni Feature Store. La cola desacopla la respuesta online del mantenimiento posterior. |

La diferencia practica es:

1. DynamoDB responde preguntas operacionales: "Que decision tuvo la transaccion `T001`?".
2. SQS responde al patron de integracion: "Hay trabajo pendiente despues de puntuar `T001`?".
3. S3 conserva evidencia y datasets historicos.
4. Feature Store conserva features ML-ready para predicciones futuras.

En una arquitectura productiva, una aplicacion de pagos podria consultar DynamoDB inmediatamente despues del scoring para mostrar o auditar la decision. En paralelo, consumidores asincronos leerian SQS para actualizar Data Lake, recalcular features, disparar alertas, enviar eventos a monitoreo o alimentar procesos antifraude posteriores.

## Flujo detallado del paso

| Orden | Script | Input local | Input S3/AWS | Output local | Output S3/AWS | Proposito |
|---:|---|---|---|---|---|---|
| 1 | `src.lab_runner` | Argumento `step 00` | Ninguno | Mensaje con la ruta del documento | Ninguno | Confirmar que la ruta fraud esta disponible. |
| 2 | Lectura de `lab/fraud_00_architecture.md` | Documentacion del laboratorio | Ninguno | Comprension del flujo online, async, batch y retraining | Ninguno | Alinear el mapa mental antes de crear recursos. |

## Paths principales

| Tipo | Path | Contenido | Uso posterior |
|---|---|---|---|
| Documento actual | `lab/fraud_00_architecture.md` | Arquitectura y responsabilidades por servicio. | Referencia durante todos los pasos. |
| Configuracion editable | `.env` | Profile, region, nombres y flags del laboratorio. | Pasos 01-09. |
| Template de configuracion | `.env.example` | Defaults seguros para copiar a `.env`. | Setup inicial. |
| Runner | `src/lab_runner.py` | Secuencia oficial de pasos fraud. | Ejecucion paso a paso y `all`. |
| Codigo de dominio | `src/fraud_lab/` | Limpieza, features, scoring y pipelines del caso de fraude. | Pasos cloud posteriores. |

## Prerrequisitos

- Haber instalado dependencias con `pip install -r requirements.txt`.
- Revisar `.env.example`.
- Tener un AWS profile o credenciales del entorno configuradas.

## Pasos de ejecucion

Listar la ruta fraud:

```bash
python -m src.lab_runner list
```

Ejecutar este paso:

```bash
python -m src.lab_runner step 00
```

## Resultado esperado

Este paso imprime una referencia a la documentacion y no crea recursos. Despues de leerlo, deberias poder explicar por que online scoring usa Online Store para historia y por que batch/retraining usan Offline Store.

## Validacion local

Ejecuta:

```bash
python -m src.lab_runner list
```

Debes ver los pasos `00` a `09` de la ruta fraud.

## Validacion en consola AWS

No aplica para este paso. Todavia no se crea infraestructura.

## Ficha tecnica del paso

| Componente | Ruta | Responsabilidad | Entradas | Salidas |
|---|---|---|---|---|
| Runner del lab | `src/lab_runner.py` | Registrar el paso `00-fraud-architecture` y mostrar la referencia documental. | Comando `python -m src.lab_runner step 00`. | Mensaje en terminal. |
| Documento de arquitectura | `lab/fraud_00_architecture.md` | Explicar limites entre Data Lake, Feature Store, Model Registry, endpoint, SQS y DynamoDB. | Conceptos del caso de fraude. | Criterios para interpretar los pasos 01-09. |
| Configuracion base | `.env.example` | Mostrar variables que controlan cuenta, region, recursos y comportamiento del endpoint/batch. | Ninguno. | Plantilla para `.env`. |

Para modificar comportamiento posterior, no cambies este documento como fuente de verdad tecnica. Cambia `.env` para configuracion, `src/lab_runner.py` para la secuencia de ejecucion y `src/fraud_lab/` para logica de negocio.
