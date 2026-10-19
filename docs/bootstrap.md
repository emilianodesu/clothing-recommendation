# Primera entrega: base local reproducible

Esta entrega implementa T1 del plan. Todavía no entrena modelos ni ofrece recomendaciones. La [preparación de Docker](setup.md) debe funcionar antes de ejecutar el bootstrap.

## Instalación y comprobación

```bash
python3 scripts/project.py bootstrap
python3 scripts/project.py verify --suite bootstrap
python3 scripts/doctor.py --application
python3 scripts/project.py status
```

El código puede estar bajo otro usuario y en una ruta con espacios. Se resuelve la raíz desde el script, sin depender del directorio de la terminal. En macOS, el proyecto debe estar compartido con Colima; `scripts/setup-docker-macos.py` prepara ese montaje. En Linux basta un Docker Engine accesible al usuario. No hay dependencia de Codex, Homebrew Python científico, una cuenta cloud ni una cuenta de Kaggle para esta entrega.

Se construyen `hmrec-app:0.1.0` y `hmrec-jobs:0.1.0` para la arquitectura nativa. Las bases se descargan por digest del índice de imágenes. Los tags locales identifican imágenes de desarrollo; al modificar código, ejecutar bootstrap para reconstruirlas. No equivalen a versiones portables de un modelo.

PostgreSQL y Qdrant quedan en ejecución. Las comprobaciones de aplicación y jobs son contenedores efímeros que terminan después de verificar bibliotecas, una operación PyTorch CPU, tres conexiones PostgreSQL y consultas Qdrant. No se publican puertos en el host en esta etapa. `API_PORT`, `UI_PORT`, `MLFLOW_PORT` y `AIRFLOW_PORT` reservan configuración para componentes posteriores, sin afirmar que esos servicios existan.

## Configuración y conservación de datos

Bootstrap crea `.env` con permisos `0600`, cuatro contraseñas aleatorias distintas, UID/GID del usuario, rutas de datos y nombre Compose derivado de la ubicación. El archivo `.env.example` explica las variables; no contiene valores utilizables como credenciales. Una segunda ejecución conserva `.env`. Si un archivo existente está incompleto, el script falla sin reemplazarlo.

Las rutas predeterminadas son `./data` y `./artifacts`; se pueden configurar en `.env`. Los datos originales existentes se conservan. Las bases `hm_app`, `hm_airflow` y `hm_mlflow` tienen usuarios diferentes y no permiten conexiones cruzadas entre esos usuarios. Sólo están preparadas las bases; Airflow y MLflow como servicios se incorporan en etapas siguientes.

Las migraciones de aplicación se ejecutan en una transacción, protegidas por bloqueo y huella del archivo. Una migración ya aplicada no se repite ni se puede editar silenciosamente. La base PostgreSQL y el almacenamiento Qdrant usan volúmenes independientes de los contenedores.

```bash
python3 scripts/project.py down
python3 scripts/project.py bootstrap --skip-build
```

La parada conserva los volúmenes. No usar `docker compose down -v` ni `docker system prune` como procedimiento habitual. Conservar de manera privada `.env`: si se pierde y aún existen volúmenes del proyecto, bootstrap se niega a inventar nuevas credenciales incompatibles. El respaldo/restauración completo del producto corresponde a T7.

Para otra instalación, transferir sólo código y documentación; ejecutar bootstrap para generar su configuración propia. No copiar `.env`, `.runtime`, volúmenes internos ni la configuración Docker del autor. H&M y los modelos reales se gestionarán por separado con acceso autorizado.

## Certificados y descargas

Bootstrap exporta las CA que Python ya confía en el host a `.runtime/build-ca.pem`. El archivo está excluido de Git y del contexto de construcción; Docker lo monta como secreto sólo durante la descarga e instalación de dependencias. Las imágenes finales no contienen ese archivo ni necesitan esa ruta del host.

Esto permite construir tanto desde redes públicas como desde una red con inspección TLS ya configurada en el equipo. No se desactiva la verificación de certificados ni se modifica la confianza global del equipo o de Colima. Si Python no dispone de CA válidas, configurar `SSL_CERT_FILE` con el bundle autorizado de ese entorno y repetir bootstrap. Una CA no autorizada no es una solución al error.

uv se obtiene de `astral/uv` en Docker Hub, con el mismo digest verificado que la imagen correspondiente de GHCR. En el equipo inicial, Docker Hub funciona mientras GHCR no reconoce la CA de la red dentro de Colima. [Configuración TLS de uv](https://docs.astral.sh/uv/concepts/authentication/certificates/) y [uv con Docker](https://docs.astral.sh/uv/guides/integration/docker/).

## Versiones y mantenimiento

| Componente | Versión fijada |
| --- | --- |
| Python dentro de Docker | 3.12.15 |
| uv de construcción | 0.13.1 |
| PyTorch | 2.14.1, variante CPU en Linux |
| Polars / PyArrow / NumPy | 1.44.2 / 25.0.1 / 2.5.3 |
| FastAPI / Uvicorn | 0.136.3 / 0.52.4 |
| Qdrant servidor / cliente | 1.19.1 / 1.19.0 |
| PostgreSQL / Psycopg | 17.11 / 3.3.5 |
| MLflow | 3.17.1 |
| Airflow, resolución separada | 3.3.2 |

`uv.lock` fija dependencias de aplicación, interfaz, tracking, pruebas y construcción. Los Dockerfiles usan `uv sync --locked`; la construcción del paquete utiliza el backend instalado desde ese lock, sin resolver dependencias adicionales fuera del entorno bloqueado. El índice CPU de PyTorch es explícito y no sustituye PyPI para los demás paquetes. [Guía oficial de uv y PyTorch](https://docs.astral.sh/uv/guides/integration/pytorch/).

`docker/airflow/requirements.lock` resuelve por separado Airflow y los trabajos, con hashes y las [restricciones oficiales de Airflow 3.3.2 para Python 3.12](https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt). Resolver ese archivo no demuestra que un DAG ya funcione. La instalación futura debe usar `uv pip sync --require-hashes --torch-backend cpu` en su entorno separado; T6 construirá y probará ese servicio.

Para cambiar dependencias de forma deliberada:

```bash
# Editar primero pyproject.toml y, si corresponde, los inputs de Airflow.
python3 scripts/lock-dependencies.py
python3 scripts/project.py bootstrap
python3 scripts/project.py verify --suite bootstrap
```

El script de locks utiliza Linux/Python/uv fijados en `docker/lock.Dockerfile`. No instala herramientas globales. Conserva el lock previo como punto de partida y actualiza ambos archivos sólo después de resolver los dos entornos. Revisar los cambios y verificar antes de aceptarlos. Las imágenes base se actualizan de manera explícita en Dockerfiles, Compose y `docker/images.lock.json`.

## Evidencia y límites

La prueba de bootstrap usa una copia del código en otra ruta con espacios dentro del montaje compartido, sin copiar `.env` ni el dataset. Comprueba importaciones, validación de configuración, repetición de instalación, aislamiento de bases, persistencia después de detener y volver a arrancar, y eliminación de sus recursos temporales. Los datos del proyecto principal permanecen intactos.

La ejecución nativa disponible es Linux ARM64 dentro de Colima en macOS. Los manifiestos de imágenes y resolución incluyen AMD64; falta ejecutar en un equipo AMD64 y en una segunda computadora física. No confundir esta prueba con entrenamiento masivo, evaluación del recomendador, API disponible, latencia medida o instalación del producto completo.
