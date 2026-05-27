# Fraud 06 - Async update con SQS, S3 y Feature Store

## Objetivo

Procesar eventos posteriores a la prediccion online para actualizar el Data Lake y publicar features que serviran a predicciones futuras.

Este paso demuestra por que la arquitectura online no debe cargar con todo el trabajo analitico. La respuesta al cliente ocurre en el paso 05; el mantenimiento del estado historico ocurre despues, desacoplado por SQS.

## Que vas a construir o validar

Este paso valida:

- Lectura de mensajes desde SQS.
- Escritura asincrona en S3 raw, cleaned y curated.
- Actualizacion de Feature Store Online Store.
- Actualizacion de Offline Store/export S3.
- Eliminacion segura del mensaje procesado.

## Input del paso

Mensaje SQS producido por `fraud-step 05`:

```json
{
  "event_type": "fraud_prediction_completed",
  "raw_event": {},
  "cleaned_event": {},
  "prediction_event": {},
  "trace_uris": {}
}
```

El mensaje representa que la prediccion online ya termino. Incluye la transaccion original, la version limpia, la decision y las rutas de trazabilidad en S3.

## Output esperado del paso

Resumen:

```json
{
  "processed_events": 1
}
```

Objetos S3:

```text
lake/raw/async-transactions/
lake/cleaned/async-transactions/
lake/curated/async-transactions/
events/async_update_summary.json
```

Feature Groups actualizados:

- `user_behavior_features`
- `card_velocity_features`
- `last_transaction_features`

## Flujo de la cola SQS

El flujo completo es:

```text
fraud-step 05
  -> predice online
  -> guarda trazas en S3
  -> guarda decision en DynamoDB
  -> envia mensaje a SQS

fraud-step 06
  -> lee mensaje desde SQS
  -> procesa evento
  -> escribe raw/cleaned/curated asincrono en S3
  -> actualiza Feature Store Online y Offline
  -> elimina mensaje de SQS solo si termino correctamente
```

En este laboratorio, el consumidor de SQS es el script:

```text
src/fraud_lab/aws/pipelines/async_update_online_features_aws.py
```

Ese script se ejecuta cuando corres:

```bash
python -m src.lab_runner fraud-step 06
```

No hay una Lambda ni un servicio ECS escuchando la cola de forma continua. El consumo ocurre bajo demanda, desde tu terminal. Esto hace que el flujo sea mas facil de inspeccionar en clase: primero puedes ver el mensaje pendiente en SQS despues del paso 05, y luego ejecutar el paso 06 para procesarlo.

SQS cumple cuatro funciones arquitectonicas:

| Funcion | Que significa en el laboratorio |
| --- | --- |
| Desacoplamiento | El endpoint online no espera a que se recalculen features ni a que se escriban todas las capas analiticas. |
| Buffer | Si el pipeline asincrono esta detenido, los eventos quedan pendientes en la cola. |
| Reintento | Si el procesamiento falla antes de borrar el mensaje, SQS puede volver a exponerlo despues del visibility timeout. |
| Control operacional | Permite observar mensajes disponibles, mensajes en vuelo y atrasos del procesamiento. |

SQS no reemplaza al Data Lake. La cola transporta eventos pendientes de procesamiento; S3 conserva la historia auditable.

## Relacion entre DynamoDB y SQS

El paso 05 escribe en DynamoDB y SQS casi al final del scoring, pero con objetivos diferentes:

| Recurso | Se escribe en step 05 | Se usa en step 06 | Rol |
| --- | --- | --- | --- |
| DynamoDB | Si | No como fuente principal | Estado operacional de la decision. Sirve para consultar que decision se tomo para una transaccion. |
| SQS | Si | Si | Evento pendiente de procesamiento asincrono. Sirve para continuar el flujo despues de responder al cliente. |

El pipeline asincrono no necesita consultar DynamoDB para procesar el evento porque el mensaje SQS ya incluye `raw_event`, `cleaned_event`, `prediction_event` y `trace_uris`. DynamoDB queda como store operacional para consulta humana o aplicativa.

Este diseno evita acoplar el procesamiento asincrono a la tabla de decisiones. Si DynamoDB se usa para dashboards o APIs operacionales, SQS sigue siendo el mecanismo correcto para orquestar trabajo pendiente.

## Conceptos claves

La prediccion online debe ser rapida. No conviene que el cliente espere recargas de Feature Store, recalculo de ventanas o escrituras analiticas completas. Por eso el paso 05 emite un evento y el paso 06 lo procesa despues.

El pipeline asincrono actualiza features para futuras predicciones. Por ejemplo, despues de puntuar `T001`, puede incrementar `user_txn_count_1h`, `card_txn_count_5m` y registrar `last_transaction_country`. Esa actualizacion no afecta la prediccion de `T001`, pero si puede afectar una transaccion `T002` que llegue despues.

El orden temporal importa. Si `T001` llega a las 14:20, el score de `T001` usa las features disponibles antes o hasta ese momento. Luego, a las 14:20:05, el pipeline actualiza Feature Store. Si `T002` llega a las 14:21, ya puede ver esas features actualizadas.

No todas las features deben actualizarse en este proceso simple. Features de ventana compleja como `device_users_count_7d`, `countries_count_24h` o agregaciones multi-entidad normalmente se calculan con streaming, batch incremental o jobs especializados. El laboratorio usa agregaciones simples para mostrar el patron sin esconder la arquitectura.

El borrado del mensaje debe ocurrir al final. Si se elimina de SQS antes de escribir S3 o Feature Store, un fallo podria perder el evento. Por eso el flujo correcto es procesar, persistir y luego borrar.

La idempotencia es una preocupacion real. En produccion, el mismo `transaction_id` y `request_id` deberian permitir detectar duplicados si SQS reentrega un mensaje. El laboratorio conserva esos identificadores en trazas y decisiones para mostrar ese principio.

Una DLQ no esta implementada por defecto en el flujo didactico, pero en produccion se agregaria una dead-letter queue para mensajes que fallan repetidamente.

## Consumidores SQS en produccion

En produccion, el consumidor puede ser Lambda, ECS, EKS, un worker en EC2 o un job especializado. La diferencia principal es como se entera de que hay mensajes pendientes.

| Patron | Como detecta mensajes | Quien borra el mensaje | Cuando usarlo |
| --- | --- | --- | --- |
| SQS + Lambda | AWS Lambda crea un Event Source Mapping y sondea la cola automaticamente. | Lambda borra el mensaje si la funcion termina correctamente. | Volumen moderado, procesamiento corto y simple. |
| SQS + ECS/EKS/EC2 worker | La aplicacion ejecuta un loop con `ReceiveMessage` y long polling. | El worker llama `DeleteMessage` despues de persistir correctamente. | Procesos con mas dependencias, mayor duracion o control fino. |
| SQS + Glue Job | Un scheduler o trigger lanza Glue; el job lee mensajes o lee un lote ya persistido en S3. | El job o componente coordinador borra mensajes solo despues de persistir. | ETL batch o micro-batch donde la latencia no es critica. |

Ejemplo conceptual de worker ECS:

```python
while True:
    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=20,
    )
    for message in response.get("Messages", []):
        process(message)
        sqs.delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=message["ReceiptHandle"],
        )
```

`WaitTimeSeconds=20` activa long polling. Eso evita hacer consultas vacias a SQS de forma agresiva.

En todos los casos, la regla es:

```text
procesamiento correcto -> DeleteMessage
fallo -> no borrar el mensaje
```

Si el consumidor falla antes de borrar el mensaje, SQS lo vuelve a mostrar despues del Visibility Timeout para permitir reintento. Para mensajes que fallan muchas veces, la buena practica es configurar una dead-letter queue.

## Patron streaming con Kinesis o MSK

En arquitecturas de streaming de alto volumen, SQS puede no estar en el camino principal. En su lugar se usa Kinesis Data Streams o Amazon MSK/Kafka.

Ejemplo:

```text
Sistema transaccional
  -> Kinesis o MSK
  -> Flink, Spark Structured Streaming, Lambda o ECS consumer
  -> S3 raw
  -> Online Store para features rapidas
  -> curated/offline datasets para batch y retraining
```

En este patron, el consumidor no espera mensajes SQS. Lee continuamente shards de Kinesis o particiones de Kafka:

| Tecnologia | Como consume |
| --- | --- |
| Kinesis + Lambda | Event Source Mapping de Lambda lee shards. |
| Kinesis + Flink/Spark | El job streaming lee continuamente el stream. |
| MSK/Kafka | El consumer se suscribe a topics y particiones. |
| ECS/EKS app | Usa Kinesis Client Library o cliente Kafka. |

SQS puede seguir existiendo como DLQ, cola de tareas secundarias o mecanismo de reintento, pero no necesariamente como canal principal de eventos.

Regla practica:

- Usa SQS + Lambda si el volumen es moderado y el procesamiento es simple.
- Usa SQS + ECS/EKS si necesitas mas control, dependencias pesadas o runtimes largos.
- Usa Kinesis/MSK + Flink/Spark si necesitas alto volumen, baja latencia y agregaciones con estado como ventanas de fraude.

## Ejemplo temporal

```text
14:20:00 - T001 llega al Fraud Scoring Service.
14:20:00 - Se calculan current features en memoria.
14:20:00 - Se consultan historical/entity features desde Online Store.
14:20:01 - Se devuelve decision al cliente.
14:20:01 - Se envia evento a SQS.
14:20:05 - Async update procesa el mensaje.
14:20:05 - Se actualizan Data Lake y Feature Store.
14:21:00 - T002 llega y puede ver features actualizadas por T001.
```

## Flujo detallado del paso

| Orden | Script o componente | Input principal | Recurso AWS usado | Output principal | Proposito |
| --- | --- | --- | --- | --- | --- |
| 1 | `fraud_lab.aws.pipelines.async_update_online_features_aws` | Mensajes en `FRAUD_EVENT_QUEUE_URL` | SQS | Eventos recibidos en memoria | Consumir eventos generados por el scoring online. |
| 2 | Pipeline asincrono de features | `raw_event`, `cleaned_event`, `prediction_event` | S3 | Archivos bajo `lake/raw`, `lake/cleaned`, `lake/curated` | Persistir el evento en capas del Data Lake. |
| 3 | `AwsFeatureStore.put_record` | Registros derivados del evento | SageMaker Feature Store Runtime | Online Store actualizado | Hacer que futuras predicciones real-time puedan usar el nuevo estado. |
| 4 | Export offline controlado | Registros derivados del evento | S3 | CSVs bajo `feature-store/offline-export/` | Mantener datos disponibles para batch prediction y retraining didactico. |
| 5 | `SqsPredictionEventBus.delete` | Receipt handle del mensaje procesado | SQS | Mensaje eliminado | Confirmar que el evento termino correctamente y no debe reprocesarse. |

Este paso no vuelve a invocar el endpoint. Su responsabilidad es actualizar estado y trazabilidad despues de que la decision online ya fue entregada.

## Paths principales

| Tipo | Ruta o recurso | Contenido esperado |
| --- | --- | --- |
| SQS | `FRAUD_EVENT_QUEUE_URL` | Mensajes `fraud_prediction_completed` antes de procesar. |
| S3 | `<FRAUD_S3_PREFIX>/lake/raw/async-transactions/` | Evento asincrono en formato raw. |
| S3 | `<FRAUD_S3_PREFIX>/lake/cleaned/async-transactions/` | Evento asincrono normalizado. |
| S3 | `<FRAUD_S3_PREFIX>/lake/curated/async-transactions/` | Evento curado para consumo posterior. |
| S3 | `<FRAUD_S3_PREFIX>/events/async_update_summary.json` | Resumen del procesamiento asincrono. |
| S3 | `<FRAUD_S3_PREFIX>/feature-store/offline-export/<feature-group>/features.csv` | Export controlado de features para batch y retraining. |
| SageMaker Feature Store | Feature Groups de fraude | Registros nuevos o actualizados con `event_time` reciente. |

## Ficha tecnica del paso

| Necesidad | Archivo donde revisar o cambiar |
| --- | --- |
| Cambiar consumo de SQS y orquestacion asincrona AWS | `src/fraud_lab/aws/pipelines/async_update_online_features_aws.py` |
| Cambiar transformacion asincrona general | `src/fraud_lab/pipelines/async_update_online_features.py` |
| Cambiar lectura o borrado de mensajes SQS | `src/fraud_lab/aws/event_bus.py` |
| Cambiar escrituras raw, cleaned y curated en S3 | `src/fraud_lab/aws/s3_data_lake.py` |
| Cambiar actualizacion de Feature Store | `src/fraud_lab/aws/feature_store.py` |
| Cambiar features derivadas de transacciones | `src/fraud_lab/pipelines/cleaned_to_curated.py` y `src/fraud_lab/features/current_transaction_features.py` |

## Prerrequisitos

- Haber ejecutado `fraud-step 05`.
- Cola SQS con mensaje pendiente.
- Feature Groups creados y cargados.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 06
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.async_update_online_features_aws
```

## Resultado esperado

El mensaje SQS se procesa y se elimina. Online Store queda actualizado con nuevas features simples para usuario/tarjeta y `last_transaction_features`. El export offline en S3 queda actualizado para batch/retraining.

## Validacion local

El stdout debe mostrar `processed_events` mayor o igual a 1 si habia mensajes pendientes.

Si muestra `0`, normalmente significa que:

- No se ejecuto `fraud-step 05`.
- El mensaje ya fue procesado.
- La cola configurada en `.env.cloud` no corresponde al stack actual.

## Validacion en consola AWS

Revisa:

- SQS: la cola queda sin mensajes visibles despues del procesamiento.
- S3: existe `events/async_update_summary.json`.
- S3: existen objetos bajo `lake/raw/async-transactions/`, `lake/cleaned/async-transactions/` y `lake/curated/async-transactions/`.
- SageMaker Feature Store: `user_behavior_features`, `card_velocity_features` o `last_transaction_features` tienen registros recientes con `event_time` de la transaccion.

Para corroborar SQS en consola:

1. Abre Amazon SQS.
2. Busca la cola `FRAUD_EVENT_QUEUE_NAME`.
3. Antes del paso 06, revisa `Messages available`.
4. Ejecuta el paso 06.
5. Refresca la cola y confirma que `Messages available` vuelve a 0 si el mensaje fue procesado.
