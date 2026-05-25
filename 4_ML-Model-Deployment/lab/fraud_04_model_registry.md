# Fraud 04 - Modelo simple en SageMaker Model Registry

## Objetivo

Crear un modelo simple de fraude, empaquetarlo como artefacto SageMaker, subirlo a S3 y registrarlo en SageMaker Model Registry como un Model Package aprobado.

Este paso no despliega el endpoint todavia. Su responsabilidad es gobierno y versionado del modelo.

## Que vas a construir o validar

Este paso construye:

- Modelo scikit-learn simple para fraude.
- Artefacto `model.tar.gz` con `model.joblib` y metadata del modelo.
- Artefacto `source_dir.tar.gz` con el codigo de inferencia que usara el contenedor.
- Model Package Group en SageMaker Model Registry.
- Model Package con `ModelApprovalStatus=Approved`.
- Metadata local en `artifacts/local_outputs/fraud_model_registry.json`.

## Input del paso

Variables principales:

```bash
S3_BUCKET_NAME=<bucket>
FRAUD_S3_PREFIX=ml-deploy-lab/lab/fraud
FRAUD_MODEL_PACKAGE_GROUP_NAME=ml-deploy-lab-fraud-models
AWS_REGION=us-east-1
```

El modelo se entrena con datos sinteticos deterministas generados desde el contrato de features de fraude.

## Output esperado del paso

S3:

```text
s3://<bucket>/<prefix>/model-registry/artifacts/<timestamp>/model.tar.gz
s3://<bucket>/<prefix>/model-registry/source-dir/<timestamp>/source_dir.tar.gz
```

SageMaker Model Registry:

```text
Model Package Group: ml-deploy-lab-fraud-models
Model Package: arn:aws:sagemaker:<region>:<account>:model-package/...
Approval: Approved
```

Metadata local:

```text
artifacts/local_outputs/fraud_model_registry.json
```

## Archivos que definen el modelo

El modelo queda definido por tres capas de informacion:

| Capa | Donde queda | Que contiene | Para que sirve |
| --- | --- | --- | --- |
| Artefacto del modelo | `model-registry/artifacts/<timestamp>/model.tar.gz` | Modelo serializado y metadata. | Es el `ModelDataUrl` del Model Package y la fuente de `model.joblib`. |
| Source directory | `model-registry/source-dir/<timestamp>/source_dir.tar.gz` | Codigo de serving que el contenedor usa como `SAGEMAKER_SUBMIT_DIRECTORY`. | Hace explicito el entry point que SageMaker debe importar al desplegar. |
| Model Package | SageMaker Model Registry | URI del artefacto, imagen de inferencia, content types, approval status y metadata. | Gobierna que version esta aprobada para despliegue. |

Contenido conceptual de `model.tar.gz`:

| Archivo dentro del tar | Proposito |
| --- | --- |
| `model.joblib` | Pipeline scikit-learn entrenado. Es el objeto que se carga para predecir. |
| `model_metadata.json` | Version del modelo, version de features, `feature_order` y tipo de modelo. |

Contenido conceptual de `source_dir.tar.gz`:

| Archivo dentro del tar | Proposito |
| --- | --- |
| `fraud_entry.py` | Entry point principal para el contenedor SageMaker. |
| `model_fn.py` | Funcion que carga `model.joblib` desde `/opt/ml/model`. |
| `input_fn.py` | Funcion que interpreta payload JSON o CSV enviado al endpoint o Batch Transform. |
| `predict_fn.py` | Funcion que ejecuta inferencia y calcula score/decision. |
| `output_fn.py` | Funcion que serializa la respuesta como JSON. |
| `inference.py` | Modulo alternativo que expone las mismas funciones de serving. |
| `requirements.txt` | Dependencias necesarias dentro del contenedor. |
| `setup.py` | Permite instalar el paquete de inferencia dentro del contenedor. |

El Model Package registra principalmente:

- `Image`: imagen del contenedor Scikit-learn para la region.
- `ModelDataUrl`: S3 URI de `model.tar.gz`.
- `SupportedContentTypes`: `application/json` y `text/csv`.
- `SupportedResponseMIMETypes`: `application/json`.
- `ModelApprovalStatus`: `Approved`.
- Metadata de negocio: caso de uso, version de modelo, version de features y laboratorio.

El `source_dir_s3_uri` queda guardado en la metadata local y se usa en el despliegue del paso 05 para configurar `SAGEMAKER_SUBMIT_DIRECTORY`. En una implementacion productiva tambien podria registrarse como metadata adicional de linaje o asociarse a un pipeline de training.

## `ModelDataUrl` vs `SAGEMAKER_SUBMIT_DIRECTORY`

SageMaker separa el artefacto del modelo del codigo de serving:

| Campo | Apunta a | Rol |
| --- | --- | --- |
| `ModelDataUrl` | `s3://.../model.tar.gz` | Descargar y extraer el modelo en `/opt/ml/model`. |
| `source_dir_s3_uri` | `s3://.../source_dir.tar.gz` | Guardar en metadata local la URI del codigo de inferencia. |
| `SAGEMAKER_SUBMIT_DIRECTORY` | mismo valor que `source_dir_s3_uri` | Indicar al contenedor SageMaker donde descargar el codigo. |
| `SAGEMAKER_PROGRAM` | `fraud_entry.py` | Indicar que archivo del source dir debe importar como entry point. |

El flujo de despliegue queda asi:

```text
SageMaker crea el contenedor
  -> descarga ModelDataUrl
  -> extrae model.tar.gz en /opt/ml/model
  -> descarga SAGEMAKER_SUBMIT_DIRECTORY
  -> importa SAGEMAKER_PROGRAM = fraud_entry.py
  -> model_fn() carga /opt/ml/model/model.joblib
  -> input_fn(), predict_fn() y output_fn() atienden inferencia
```

Por esta separacion, los scripts `fraud_entry.py`, `model_fn.py`, `input_fn.py`, `predict_fn.py` y `output_fn.py` no necesitan estar dentro de `model.tar.gz`. Deben estar en `source_dir.tar.gz`. El artefacto del modelo queda mas limpio y portable: contiene el modelo y su metadata; el source dir contiene la logica de serving.

## Conceptos claves

SageMaker Model Registry no es un endpoint. Es un catalogo gobernado de versiones de modelo. Permite registrar artefactos, imagen de inferencia, estado de aprobacion, metadata y linaje. Desplegar un modelo requiere un paso posterior: crear un SageMaker Model desde el Model Package y luego un Endpoint o Batch Transform Job.

El artefacto del modelo debe incluir el objeto entrenado y la metadata necesaria para auditarlo. El codigo necesario para cargarlo y servirlo vive en `source_dir.tar.gz`. Si `model.joblib` existe pero el source dir no contiene `input_fn`, `model_fn` o el entry point, el endpoint puede crearse pero fallar al arrancar el contenedor.

El `feature_order.json` del paso 02 y la metadata del modelo deben estar alineados. El modelo no recibe nombres de columnas de forma libre; recibe un vector con orden estable. Este punto evita training-serving skew.

El approval status separa registro de despliegue. Un modelo puede estar registrado pero no aprobado. En este laboratorio se usa `Approved` para que el siguiente paso pueda desplegarlo por defecto.

La imagen de inferencia no es el modelo. La imagen define el runtime; el artefacto S3 define el contenido del modelo; el Model Package une ambos en una version gobernada.

## Flujo detallado del paso

| Orden | Script | Input local | Input S3/AWS | Output local | Output S3/AWS | Proposito |
|---:|---|---|---|---|---|---|
| 1 | `fraud_lab.aws.model_registry` | `.env`, `.env.cloud`, codigo en `src/fraud_lab/aws/sagemaker_inference/` | S3 y SageMaker Model Registry | `data/local_cache/fraud_model.tar.gz`, `data/local_cache/fraud_source_dir.tar.gz` | Artefactos en S3 y Model Package | Orquestar packaging y registro. |
| 2 | `create_fraud_model_artifact` | Contrato de features, sklearn/joblib | Ninguno | `fraud_model.tar.gz` | Ninguno directo | Entrenar modelo simple y empaquetar `model.joblib` + metadata. |
| 3 | `upload_fraud_model_artifact` | Tarball local | S3 | Ninguno | `model-registry/artifacts/<timestamp>/model.tar.gz` | Publicar artefacto de modelo. |
| 4 | `upload_fraud_source_dir_artifact` | Source dir local | S3 | Ninguno | `model-registry/source-dir/<timestamp>/source_dir.tar.gz` | Publicar codigo de serving usado por el contenedor. |
| 5 | `register_fraud_model_package` | Metadata local y S3 URIs | SageMaker Model Registry | `artifacts/local_outputs/fraud_model_registry.json` | Model Package Group y Model Package `Approved` | Crear version gobernada del modelo. |

## Paths principales

| Tipo | Path | Quien lo crea | Quien lo consume |
|---|---|---|---|
| Modelo local | `data/local_cache/fraud_model.tar.gz` | `create_fraud_model_artifact` | Upload a S3 y auditoria local. |
| Source dir local | `data/local_cache/fraud_source_dir.tar.gz` | `create_fraud_source_dir_artifact` | Upload a S3 y despliegue. |
| Codigo de inferencia | `src/fraud_lab/aws/sagemaker_inference/` | Codigo fuente | Packaging del modelo y endpoint. |
| Artefacto S3 | `s3://<bucket>/<FRAUD_S3_PREFIX>/model-registry/artifacts/<timestamp>/model.tar.gz` | `upload_fraud_model_artifact` | Model Package y SageMaker Model. |
| Source dir S3 | `s3://<bucket>/<FRAUD_S3_PREFIX>/model-registry/source-dir/<timestamp>/source_dir.tar.gz` | `upload_fraud_source_dir_artifact` | Variable `SAGEMAKER_SUBMIT_DIRECTORY` en paso 05. |
| Metadata local | `artifacts/local_outputs/fraud_model_registry.json` | `register_fraud_model_package` | Paso 05 y validacion. |

## Prerrequisitos

- Haber ejecutado `fraud-step 01`.
- Dependencias instaladas: `scikit-learn`, `joblib`, `boto3`, `sagemaker`.
- Permisos para `sagemaker:CreateModelPackageGroup`, `sagemaker:CreateModelPackage` y S3 upload.

## Pasos de ejecucion

Ejecutar:

```bash
python -m src.lab_runner fraud-step 04
```

Comando directo equivalente:

```bash
python -m fraud_lab.aws.model_registry
```

## Resultado esperado

El comando imprime:

- `model_package_group_name`
- `model_package_arn`
- `model_artifact_s3_uri`
- `source_dir_s3_uri`
- `image_uri`
- `approval_status`

Tambien sugiere variables que puedes copiar a `.env` si quieres fijar esa version:

```bash
FRAUD_MODEL_PACKAGE_GROUP_NAME=...
FRAUD_MODEL_PACKAGE_ARN=...
FRAUD_MODEL_ARTIFACT_S3_URI=...
```

## Validacion local

Revisa:

```bash
type artifacts\local_outputs\fraud_model_registry.json
```

En Git Bash:

```bash
cat artifacts/local_outputs/fraud_model_registry.json
```

El JSON debe mostrar `model_artifact_s3_uri`, `source_dir_s3_uri`, `model_package_arn`, `image_uri` y `artifact_packaging_version`.

## Validacion en consola AWS

En SageMaker Studio o consola SageMaker:

- Ir a Model Registry.
- Buscar el Model Package Group `ml-deploy-lab-fraud-models`.
- Confirmar que existe una version con estado `Approved`.
- Abrir la version y revisar que el contenedor apunta al S3 URI bajo `model-registry/artifacts/`.
- En S3, revisar tambien el prefijo `model-registry/source-dir/`.

Model Registry no muestra necesariamente cada archivo interno del tarball. Para auditar contenido interno, descarga `model.tar.gz` desde S3 o revisa la copia local generada en `data/local_cache/fraud_model.tar.gz`.

## Ficha tecnica del paso

| Componente | Ruta | Responsabilidad | Entradas | Salidas |
|---|---|---|---|---|
| Registro de modelo | `src/fraud_lab/aws/model_registry.py` | Entrenar modelo simple, empaquetar artefactos, subirlos y registrar Model Package. | Config, contrato de features, codigo de inferencia. | `fraud_model_registry.json`, tarballs locales, artefactos S3, Model Package. |
| Codigo de serving | `src/fraud_lab/aws/sagemaker_inference/` | Implementar `model_fn`, `input_fn`, `predict_fn`, `output_fn` y entry point. | Payload JSON/CSV y `model.joblib`. | Respuesta de inferencia JSON. |
| Contrato de features | `src/fraud_lab/features/feature_contract.py` | Mantener `feature_order`, `MODEL_VERSION` y `FEATURE_VERSION`. | Ninguno. | Metadata del modelo y orden esperado. |
| Imagen de inferencia | `src/aws_clients.py` | Resolver la imagen Scikit-learn por region. | `AWS_REGION`. | ECR image URI. |

Para cambiar el algoritmo, edita `_fraud_training_rows` y `create_fraud_model_artifact` en `src/fraud_lab/aws/model_registry.py`. Para cambiar como el endpoint interpreta payloads, edita `src/fraud_lab/aws/sagemaker_inference/input_fn.py` y `predict_fn.py`.
