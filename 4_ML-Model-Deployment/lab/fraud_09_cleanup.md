# Fraud 09 - Cleanup endpoint y Feature Store

## Objetivo

Eliminar de forma explicita el Real-Time Endpoint, Endpoint Configuration, SageMaker Model y Feature Groups creados por el caso de fraude sin borrar recursos externos ni datos de otros laboratorios.

Este documento cubre dos niveles de limpieza:

| Nivel | Comando | Que borra | Cuando usarlo |
| --- | --- | --- | --- |
| Cleanup conservador | `python -m src.lab_runner fraud-cleanup` o `python -m src.lab_runner step 09` | Endpoint, endpoint config, SageMaker Model y Feature Groups de fraude. | Cuando quieres detener costos directos, pero conservar S3, DynamoDB, SQS, Model Registry y evidencias. |
| Cleanup total | `python -m src.lab_runner fraud-full-cleanup` o `python -m src.lab_runner step 10` | Recursos deployables, Feature Groups, Model Registry, prefijos S3 del lab, stack CloudFormation y archivos locales generados. | Cuando terminaste el laboratorio y quieres dejar la cuenta y el workspace limpios. |

## Que vas a construir o validar

Este paso valida:

- Eliminacion del endpoint `FRAUD_ENDPOINT_NAME`.
- Eliminacion de `FRAUD_ENDPOINT_CONFIG_NAME`.
- Eliminacion de `FRAUD_MODEL_NAME`.
- Identificacion de Feature Groups creados con `FRAUD_FEATURE_GROUP_PREFIX`.
- Eliminacion de Feature Groups del caso de fraude.
- Conservacion de Model Registry, DynamoDB, SQS y bucket.
- Conservacion de recursos externos por defecto.

El cleanup total adicional valida:

- Borrado de Model Packages y Model Package Group de fraude.
- Borrado de objetos S3 bajo prefijos del laboratorio.
- Borrado del stack CloudFormation.
- Borrado de DynamoDB, SQS, IAM role y bucket si pertenecen al stack y pueden eliminarse.
- Borrado de archivos locales generados, `.env.cloud`, caches y metadata temporal.

## Input del paso

Variables:

```bash
FRAUD_FEATURE_GROUP_PREFIX=ml-deploy-lab-fraud
FRAUD_MODEL_NAME=ml-deploy-lab-fraud-model
FRAUD_ENDPOINT_CONFIG_NAME=ml-deploy-lab-fraud-realtime-config
FRAUD_ENDPOINT_NAME=ml-deploy-lab-fraud-realtime-endpoint
AWS_REGION=us-east-1
```

## Output esperado del paso

Resumen del cleanup conservador:

```json
{
  "deleted": ["ml-deploy-lab-fraud-user-profile-features"],
  "skipped": []
}
```

Resumen del cleanup total:

```json
{
  "fraud_endpoint_and_feature_groups": {},
  "model_registry": {},
  "s3": {},
  "cloudformation_stack": {},
  "local": {}
}
```

## Conceptos claves

Cleanup debe ser explicito porque los recursos cloud generan costo y porque borrar recursos equivocados puede romper otros flujos. Este paso borra solo recursos deployables y Feature Groups creados por la ruta fraud.

El Real-Time Endpoint es el recurso mas importante de limpiar porque genera costo mientras esta activo. El SageMaker Model y Endpoint Configuration no mantienen capacidad, pero se eliminan para evitar confusion en la consola.

El bucket S3, DynamoDB, SQS y Model Registry se conservan. El Model Registry representa gobierno y versionado del modelo; borrarlo deberia ser una decision separada.

No se borran Model Packages, Feature Groups externos ni recursos del laboratorio 3. La regla general es: el laboratorio solo elimina recursos que creo y que puede identificar por prefijo controlado.

SageMaker Feature Store puede tardar en borrar Feature Groups. Si un grupo queda en estado `Deleting`, espera unos minutos antes de recrearlo.

SageMaker no siempre permite borrar un endpoint mientras esta en `Creating`, `Updating` o `SystemUpdating`. El cleanup espera a que termine la operacion en progreso y luego elimina el endpoint. Si ves mensajes de espera, es normal: evita dejar capacidad activa y costo acumulandose.

## Flujo detallado del paso

| Orden | Script o componente | Input principal | Recurso AWS usado | Output principal | Proposito |
| --- | --- | --- | --- | --- | --- |
| 1 | `fraud_lab.aws.pipelines.cleanup_feature_store_aws` | `.env`, `.env.cloud` | SageMaker Endpoints, Endpoint Configurations, Models | Endpoint y recursos deployables eliminados o reportados como inexistentes | Detener capacidad activa y evitar costo de endpoint. |
| 2 | `cleanup_feature_store_aws` | Lista de Feature Groups del contrato | SageMaker Feature Store | Feature Groups eliminados o en `Deleting` | Limpiar Online Store/Offline Store administrados por SageMaker. |
| 3 | `fraud_lab.aws.pipelines.full_cleanup_aws` opcional | Flags explicitos | S3, Model Registry, CloudFormation, filesystem local | Teardown parcial o total | Borrar recursos de gobierno, infraestructura, objetos S3 o outputs locales si el estudiante lo decide. |

El comando `fraud-cleanup` es conservador. Detiene lo que genera costo operativo directo o bloquea recreaciones del Feature Store, pero mantiene evidencia y recursos base.

El comando `fraud-full-cleanup` ejecuta `full_cleanup_aws --all`. Ese teardown borra recursos de gobierno, objetos S3 bajo los prefijos del laboratorio, el stack CloudFormation y archivos locales generados. Usalo solo cuando ya no necesites consultar evidencias del laboratorio.

## Paths principales

| Tipo | Ruta o recurso | Contenido esperado durante cleanup |
| --- | --- | --- |
| Metadata local | `artifacts/local_outputs/fraud_full_cleanup.json` | Resultado del cleanup total si ejecutas `full_cleanup_aws` sin `--delete-local`. El comando `fraud-full-cleanup` no deja este archivo porque limpia outputs locales. |
| Metadata local | `artifacts/local_outputs/fraud_realtime_endpoint.json` | Referencia al endpoint creado previamente. |
| Metadata local | `artifacts/local_outputs/fraud_sagemaker_model.json` | Referencia al SageMaker Model deployable. |
| Archivo local | `.env.cloud` | Outputs de CloudFormation; puede borrarse con `--delete-local`. |
| Directorio local | `data/` | Datos locales generados; puede limpiarse con `--delete-local`. |
| Directorio local | `artifacts/local_outputs/` | Metadata local generada; puede limpiarse con `--delete-local`. |
| S3 | `<FRAUD_S3_PREFIX>/` | Se conserva en cleanup normal; se borra solo con `--delete-s3`. |
| CloudFormation | Stack del laboratorio | Se conserva en cleanup normal; se borra solo con `--delete-stack`. |

## Ficha tecnica del paso

| Necesidad | Archivo donde revisar o cambiar |
| --- | --- |
| Cambiar cleanup conservador de endpoint y Feature Store | `src/fraud_lab/aws/pipelines/cleanup_feature_store_aws.py` |
| Cambiar borrado de endpoint, endpoint config y SageMaker Model | `src/fraud_lab/aws/deploy_endpoint.py` |
| Cambiar borrado de Feature Groups | `src/fraud_lab/aws/feature_store.py` |
| Cambiar cleanup total con flags | `src/fraud_lab/aws/pipelines/full_cleanup_aws.py` |
| Cambiar borrado de prefijos S3 | `src/cleanup_batch_resources.py` |
| Cambiar directorios locales limpiados | `src/fraud_lab/aws/pipelines/full_cleanup_aws.py` |

## Prerrequisitos

- Haber ejecutado `fraud-step 03`.
- Permiso `sagemaker:DeleteFeatureGroup`.

## Pasos de ejecucion

### Cleanup conservador

Ejecutar:

```bash
python -m src.lab_runner fraud-cleanup
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.cleanup_feature_store_aws
```

## Resultado esperado

El endpoint, endpoint config, SageMaker Model y Feature Groups del caso de fraude se eliminan o se reportan como inexistentes. DynamoDB, SQS, S3 y Model Registry se conservan.

### Cleanup total

Si quieres borrar tambien los recursos restantes del caso de fraude y los archivos locales generados, ejecuta:

```bash
python -m src.lab_runner fraud-full-cleanup
```

Tambien puedes ejecutarlo como paso numerado:

```bash
python -m src.lab_runner step 10
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.pipelines.full_cleanup_aws --all
```

Este comando solicita:

- Borrar endpoint, endpoint config, SageMaker Model y Feature Groups si todavia existen.
- Borrar Model Packages y Model Package Group de fraude.
- Borrar objetos bajo los prefijos S3 del laboratorio.
- Borrar el stack CloudFormation, lo que elimina DynamoDB, SQS, IAM role y bucket si el stack lo creo y el bucket queda vacio.
- Borrar archivos locales generados en `data/`, `artifacts/local_outputs/`, `.env.cloud`, `__pycache__/` y `.pytest_cache/`.
- Imprimir el resumen en terminal. Cuando `--delete-local` esta activo, no se guarda `fraud_full_cleanup.json` para no recrear outputs locales despues de limpiarlos.

Despues de `fraud-full-cleanup`, si quieres ejecutar el laboratorio otra vez debes recrear infraestructura con:

```bash
python -m src.lab_runner step 01
```

Si el bucket fue creado exclusivamente para este laboratorio y CloudFormation falla porque quedan objetos, puedes vaciar completamente el bucket antes de borrar el stack:

```bash
python -m fraud_lab.aws.pipelines.full_cleanup_aws --all --empty-stack-bucket
```

Usa `--empty-stack-bucket` solo si el bucket pertenece a este laboratorio. Si el bucket es compartido, este flag puede borrar datos que no pertenecen al caso de fraude.

`--all` no activa `--empty-stack-bucket` para evitar borrar accidentalmente buckets compartidos. Tambien puedes ejecutar por partes:

```bash
python -m fraud_lab.aws.pipelines.full_cleanup_aws --delete-model-registry
python -m fraud_lab.aws.pipelines.full_cleanup_aws --delete-s3
python -m fraud_lab.aws.pipelines.full_cleanup_aws --delete-stack
python -m fraud_lab.aws.pipelines.full_cleanup_aws --delete-local
```

En Windows, algunos caches locales como `.pytest_cache/` o `__pycache__/` pueden quedar bloqueados temporalmente por la terminal, pytest o el editor. El cleanup local no debe fallar por eso: los paths bloqueados se reportan en `skipped` y se pueden borrar luego cerrando el proceso que los tenga abiertos o reejecutando:

```bash
python -m fraud_lab.aws.pipelines.full_cleanup_aws --delete-local
```

El flag `--delete-local` puede ejecutarse solo, incluso si `.env.cloud` ya fue eliminado en un intento anterior.

## Validacion local

En cleanup conservador, el stdout muestra listas `deleted` y `skipped`.

En cleanup total, revisa que `.env.cloud` haya sido eliminado y que los directorios generados queden vacios o solo con `.gitkeep`:

```bash
ls artifacts/local_outputs
ls data/local_cache
```

## Validacion en consola AWS

Para cleanup conservador, revisa:

- SageMaker Feature Store: Feature Groups en estado `Deleting` o ya ausentes.
- SageMaker Endpoints: endpoint de fraude ausente.
- SageMaker Models: modelo deployable de fraude ausente.
- SageMaker Model Registry: Model Package Group de fraude sigue existiendo.
- DynamoDB: tabla de decisiones sigue existiendo.
- SQS: cola de eventos sigue existiendo.
- S3: datos del Data Lake siguen disponibles.

Para cleanup total, revisa:

- CloudFormation: stack `ml-deploy-lab` ausente o en proceso de borrado.
- SageMaker Model Registry: Model Package Group de fraude ausente.
- SageMaker Feature Store: Feature Groups de fraude ausentes.
- SageMaker Endpoints y Models: recursos deployables de fraude ausentes.
- DynamoDB: tabla `*-fraud-decisions-*` ausente si pertenecia al stack.
- SQS: cola `*-fraud-events-*` ausente si pertenecia al stack.
- S3: prefijos del laboratorio borrados. Si el bucket fue creado por el stack, el bucket tambien deberia desaparecer cuando CloudFormation termine correctamente.
