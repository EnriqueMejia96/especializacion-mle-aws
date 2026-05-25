# Fraud 01 - Infraestructura AWS: S3, IAM, DynamoDB y SQS

## Objetivo

Crear la infraestructura base que permite ejecutar el caso de fraude sobre AWS sin hardcodear credenciales ni recursos.

## Que vas a construir o validar

Este paso despliega o actualiza un stack CloudFormation con:

- Bucket S3 privado para Data Lake, logs, exports offline, batch outputs y retraining.
- IAM role para ejecucion de SageMaker y acceso a S3/Feature Store.
- Tabla DynamoDB para decisiones operacionales.
- Cola SQS para eventos asincronos despues del scoring online.
- Archivo `.env.cloud` con outputs reutilizables por los siguientes pasos.

## Input del paso

Variables esperadas en `.env`:

```bash
AWS_PROFILE=mlops-2-data-prep-lab
AWS_REGION=us-east-1
RESOURCE_PREFIX=ml-deploy-lab
STACK_NAME=ml-deploy-lab
S3_BUCKET_NAME=
CREATE_BUCKET=
```

Si `S3_BUCKET_NAME` esta vacio, CloudFormation crea un bucket del laboratorio. Si ya tienes bucket, define `CREATE_BUCKET=false` y `S3_BUCKET_NAME=<bucket>`.

## Output esperado del paso

Archivo `.env.cloud` con valores como:

```bash
S3_BUCKET_NAME=ml-deploy-lab-<account>-us-east-1
SAGEMAKER_EXECUTION_ROLE_ARN=arn:aws:iam::<account>:role/ml-deploy-lab-sagemaker-execution-role-us-east-1
FRAUD_S3_PREFIX=ml-deploy-lab/lab/fraud
FRAUD_DECISION_TABLE_NAME=ml-deploy-lab-fraud-decisions-us-east-1
FRAUD_EVENT_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<account>/ml-deploy-lab-fraud-events-us-east-1
FRAUD_EVENT_QUEUE_NAME=ml-deploy-lab-fraud-events-us-east-1
```

## Conceptos claves

CloudFormation es el control plane de infraestructura. En lugar de crear recursos manualmente en consola, el laboratorio define recursos versionables y reproducibles. Esto tambien evita copiar ARNs manualmente: los outputs del stack se escriben en `.env.cloud`.

S3 cumple dos roles. Primero, actua como Data Lake con capas `raw`, `cleaned` y `curated`. Segundo, actua como storage operacional para logs de inferencia, exports del Offline Store y datasets model-ready para batch y retraining.

DynamoDB no reemplaza el Data Lake. Su funcion es consulta operacional de baja latencia por `transaction_id`, por ejemplo para que una aplicacion vea rapidamente si una transaccion fue `approve`, `manual_review` o `reject`.

SQS representa la frontera asincrona. La prediccion online no debe esperar a que se recalculen ventanas historicas ni a que se completen escrituras analiticas. En su lugar, emite un evento que otro proceso consume despues.

El IAM role debe aplicar minimo privilegio razonable para el laboratorio: acceso al bucket/prefijos del lab, Feature Store, DynamoDB, SQS y SageMaker. En produccion, se restringen ARNs y acciones por entorno.

## Rol de DynamoDB

DynamoDB es la tabla operacional del laboratorio. En el paso 05, despues de invocar el modelo, el Fraud Scoring Service escribe un item con esta informacion:

| Campo | Uso |
| --- | --- |
| `transaction_id` | Llave de consulta principal para buscar rapidamente una decision. |
| `request_id` | Identificador de la ejecucion de scoring. Ayuda a correlacionar S3, logs y eventos. |
| `decision` | Resultado de negocio: `approve`, `manual_review` o `reject`. |
| `fraud_score` | Score numerico producido por el modelo. |
| `model_version` | Version logica del modelo usado. |
| `feature_version` | Version del contrato de features usado. |
| `latency_ms` | Tiempo de respuesta del scoring. |
| `payload` | Respuesta completa para auditoria operacional. |

Esta tabla sirve para consultas transaccionales de baja latencia. Por ejemplo, una aplicacion interna podria buscar `transaction_id=T001` y saber que decision se tomo sin escanear archivos S3 ni consultar logs.

DynamoDB no se usa como dataset de entrenamiento. Para entrenamiento, batch y retraining se usan S3, Offline Store y archivos curados, porque esos flujos necesitan historico completo y procesamiento analitico.

## Rol de SQS

SQS es la cola de eventos posteriores a la prediccion. En el paso 05, el Fraud Scoring Service envia un mensaje con:

```json
{
  "event_type": "fraud_prediction_completed",
  "raw_event": {},
  "cleaned_event": {},
  "prediction_event": {},
  "trace_uris": {}
}
```

Ese mensaje no es la prediccion para el cliente. La prediccion ya fue devuelta. El mensaje representa trabajo pendiente para procesos posteriores: guardar eventos asincronos en el Data Lake, actualizar Feature Store para futuras transacciones y dejar evidencia de procesamiento.

SQS permite que el endpoint responda rapido aunque el pipeline asincrono este detenido, lento o fallando. Si el consumidor no esta corriendo, el mensaje queda pendiente en la cola. Si el procesamiento falla antes de borrar el mensaje, SQS puede volver a entregarlo despues del visibility timeout.

## Flujo detallado del paso

| Orden | Script | Input local | Input S3/AWS | Output local | Output S3/AWS | Proposito |
|---:|---|---|---|---|---|---|
| 1 | `src.lab_runner` | Argumento `step 01` | Ninguno | Mensaje con documento del paso | Ninguno | Ejecutar la secuencia oficial de infraestructura. |
| 2 | `src.deploy_infra` | `.env`, `infra/cloudformation/template.yaml` | CloudFormation, IAM, S3, DynamoDB, SQS | `.env.cloud` mediante `src.fetch_stack_outputs` | Stack `ml-deploy-lab`, bucket, role, tabla DynamoDB y cola SQS | Crear o actualizar infraestructura base. |
| 3 | `src.config --check-aws` | `.env`, `.env.cloud` | Ninguno directo | Readiness en stdout | Ninguno | Confirmar que bucket, role, tabla y cola quedaron disponibles para los siguientes pasos. |

## Paths principales

| Tipo | Path | Contenido | Quien lo consume |
|---|---|---|---|
| Configuracion local | `.env` | `AWS_PROFILE`, `AWS_REGION`, `RESOURCE_PREFIX`, `STACK_NAME`, `S3_BUCKET_NAME`. | `src.config`, `src.deploy_infra`. |
| Outputs generados | `.env.cloud` | Bucket, role de SageMaker, tabla DynamoDB, URL/nombre de SQS y prefijo fraud. | Pasos 02-09. |
| Infraestructura | `infra/cloudformation/template.yaml` | Recursos base: S3, IAM, DynamoDB, SQS. | `src.deploy_infra`. |
| Metadata local | `artifacts/local_outputs/` | Evidencias generadas por pasos posteriores. | Validacion y cleanup. |
| Stack AWS | CloudFormation `ml-deploy-lab` | Agrupa infraestructura base del laboratorio. | Operacion y cleanup total. |

## Prerrequisitos

- AWS CLI configurado o credenciales disponibles por rol.
- Permisos para CloudFormation, IAM, S3, DynamoDB, SQS y SageMaker.
- `.env` creado desde `.env.example`.
- Dependencias instaladas.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 01
```

Comando directo equivalente:

```bash
python -m src.deploy_infra
python -m src.config --check-aws
```

## Resultado esperado

El stack queda en `CREATE_COMPLETE` o `UPDATE_COMPLETE`. El archivo `.env.cloud` queda actualizado y los siguientes pasos pueden leer automaticamente bucket, rol, tabla y cola.

## Validacion local

Revisa:

```bash
type .env.cloud
python -m src.config --check-aws
```

En Git Bash:

```bash
cat .env.cloud
python -m src.config --check-aws
```

## Validacion en consola AWS

Revisa:

- CloudFormation: stack `ml-deploy-lab`.
- S3: bucket privado creado o bucket existente referenciado.
- IAM: role de SageMaker creado por el stack.
- DynamoDB: tabla `*-fraud-decisions-*`. En este paso debe existir, aunque todavia no tendra decisiones hasta ejecutar `fraud-step 05`.
- SQS: cola `*-fraud-events-*`. En este paso debe existir, aunque normalmente tendra `0` mensajes hasta ejecutar `fraud-step 05`.

## Ficha tecnica del paso

| Componente | Ruta | Responsabilidad | Entradas | Salidas |
|---|---|---|---|---|
| Loader de configuracion | `src/config.py` | Cargar `.env` y `.env.cloud`, construir defaults y validar readiness. | Variables de entorno, `.env`, `.env.cloud`. | Objeto `LabConfig`, salida de `--check-aws`. |
| Despliegue IaC | `src/deploy_infra.py` | Crear/actualizar stack CloudFormation y esperar estado estable. | `infra/cloudformation/template.yaml`, parametros derivados de `.env`. | Stack AWS y llamada a `write_stack_outputs`. |
| Outputs del stack | `src/fetch_stack_outputs.py` | Escribir outputs de CloudFormation en `.env.cloud`. | Stack `ml-deploy-lab`. | `.env.cloud`, valores reutilizables por otros scripts. |
| Clientes AWS | `src/aws_clients.py` | Crear sesiones boto3 usando `AWS_PROFILE` y `AWS_REGION`. | Configuracion local. | Clientes para CloudFormation, S3, SageMaker, DynamoDB y SQS. |

Configuraciones que mas cambian este paso:

- `AWS_PROFILE`: profile usado por boto3. Debe coincidir con el profile donde hiciste `aws sso login`.
- `AWS_REGION`: region donde se crean todos los recursos.
- `CREATE_BUCKET`: si queda vacio, el script decide crear bucket cuando `S3_BUCKET_NAME` esta vacio.
- `S3_BUCKET_NAME`: si lo defines, el stack puede usar un bucket existente.
- `RESOURCE_PREFIX` y `STACK_NAME`: cambian nombres fisicos de recursos.
- `FRAUD_DECISION_TABLE_NAME`, `FRAUD_EVENT_QUEUE_URL`, `FRAUD_EVENT_QUEUE_NAME`: se escriben en `.env.cloud` despues de crear el stack. No deberias inventarlos manualmente.

## Carga correcta de `.env` y `.env.cloud`

El archivo `.env` contiene configuracion base. Algunas variables quedan vacias a proposito antes de crear infraestructura:

```text
S3_BUCKET_NAME=
FRAUD_DECISION_TABLE_NAME=
FRAUD_EVENT_QUEUE_URL=
```

Despues de ejecutar `fraud-step 01`, el laboratorio genera `.env.cloud` con los valores reales creados por CloudFormation:

```text
S3_BUCKET_NAME=ml-deploy-lab-<account>-us-east-1
FRAUD_DECISION_TABLE_NAME=ml-deploy-lab-fraud-decisions-us-east-1
FRAUD_EVENT_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<account>/ml-deploy-lab-fraud-events-us-east-1
```

En Git Bash, carga primero `.env` y luego `.env.cloud`:

```bash
set -a
source .env
source .env.cloud
set +a
```

El orden importa. Si cargas `.env.cloud` primero y luego `.env`, los valores vacios de `.env` pueden pisar los valores reales.

Valida que las variables criticas no esten vacias:

```bash
echo "$AWS_PROFILE"
echo "$AWS_REGION"
echo "$S3_BUCKET_NAME"
echo "$FRAUD_S3_PREFIX"
echo "$FRAUD_DECISION_TABLE_NAME"
echo "$FRAUD_EVENT_QUEUE_URL"
```

Si `AWS_REGION` esta vacio, puedes ver:

```text
Invalid endpoint: https://s3..amazonaws.com
```

Si `S3_BUCKET_NAME` esta vacio, `aws s3 ls "s3://$S3_BUCKET_NAME/..."` puede listar todos los buckets o construir una URI invalida. Si `FRAUD_EVENT_QUEUE_URL` o `FRAUD_DECISION_TABLE_NAME` estan vacios, los comandos de SQS o DynamoDB fallaran aunque los recursos existan.

Validacion profunda:

```bash
python -m src.deploy_infra
python -m src.config --check-aws
cat .env.cloud
```
