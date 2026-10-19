# T2 · Datos y features temporales

T2 procesa H&M completo, prepara perfiles y ejemplos semanales, y produce evidencia por ejecución. Todavía no entrena un modelo. T3 medirá una época completa y la calidad de recomendaciones; E2 sólo se cerrará después de esa medición.

## Comandos reproducibles

Requisitos: T1 instalado, Docker/Colima activo, clon limpio para corridas oficiales y credencial Kaggle propia con las reglas de la competición aceptadas. La Mac usa el prefijo acordado `SSL_CERT_FILE=/etc/ssl/cert.pem`. Para el perfil de mac24, los comandos de Compose usan `DOCKER_CONTEXT=colima-hm-project`; en el equipo original, `DOCKER_CONTEXT=colima`.

```bash
# Descargar sólo una vez; reanuda el .partial si se corta la red.
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py download-data --check-access
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py download-data

# Trabajo oficial: construye desde el commit limpio, verifica y continúa desacoplado.
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job start --machine-id mac24 --profile hm-project --dataset hm
# Usa el run_id exacto que imprimió start:
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job status --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload artifacts/handoff/RUN_ID.zip
```

`start` anuncia su run_id antes de construir y conserva un log acotado de la construcción. Si falla antes de crear el contenedor, se puede exportar el mismo run_id. Un lanzamiento activo no se exporta hasta terminar o interrumpirse.

`start` acepta `--expected-commit SHA_COMPLETO`, `--resources base|mac24` y `--dataset synthetic` para probar sin Kaggle. Sin `--resources` utiliza `base`. El trabajo conserva el contenedor hasta capturar diagnóstico, no necesita PostgreSQL/Qdrant ni tiene acceso a Internet. Cerrar la terminal después de recibir el estado `started` no cancela el cálculo; el primer anuncio del `run_id` todavía puede estar construyendo la imagen. Dormir o apagar el equipo sí puede interrumpirlo. `status` distingue `running`, `passed`, `failed` e `interrupted`; salida 0 sin resultado terminado no se considera éxito.

`data-job resume --run-id RUN_ID` inicia **otra ejecución** del mismo commit, dataset, perfil Colima y perfil de recursos. Verifica que el contenido y SHA-256 del perfil de recursos sigan iguales; no admite sustituirlo durante `resume`. Para un presupuesto diferente usa `start`, que genera un experimento nuevo. Los reportes anteriores a estos perfiles corresponden al presupuesto `base`; sigue siendo obligatorio el commit original. Relee la fuente y verifica las particiones existentes antes de reutilizarlas; reconstruye el estado de los perfiles desde el histórico. No es un checkpoint de entrenamiento. No cambies de código a mitad de trabajo. Si cambia el formato de procesamiento, hay que incrementar su versión y utilizar otro destino.

Después de exportar, se puede quitar **sólo** el contenedor terminado identificado por `container_id` en `artifacts/data-jobs/RUN_ID/environment.json` usando `docker --context CONTEXTO rm ID_EXACTO`. Nunca usar `prune` ni eliminar los originales. El ZIP del reporte contiene logs acotados, configuración, identidad de imagen/commit/lock y resultados agregados; no contiene transacciones, perfiles de clientes, imágenes ni credenciales.

Para desarrollar en primer plano, después de reconstruir la imagen:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py bootstrap
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite data
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py validate-data --mode quick
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py validate-data --mode full
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py prepare-data --dataset synthetic
```

`prepare-data` sirve para desarrollo, sin acreditación de commit limpio. Las tres interfaces de aplicación son `hmrec data ingest --source ZIP --output DIRECTORIO`, `hmrec data validate --source ZIP --mode quick|full` y `hmrec features build --dataset DIRECTORIO --data-as-of YYYY-MM-DD --output DIRECTORIO [--vocabulary ARCHIVO]`. La CLI exige identidad H&M por defecto; `data ingest --synthetic` permite fixtures explícitas.

## Acceso y certificados

Guarda el token generado en [Kaggle](https://www.kaggle.com/settings/api) en `~/.kaggle/access_token`, con permisos 600. También se admite `KAGGLE_API_TOKEN` y el archivo legado `~/.kaggle/kaggle.json`. No pegues el token en un comando, notebook, log o chat; el descargador es el único componente que lo utiliza. Acepta las [reglas H&M](https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/rules) desde tu cuenta. [Autenticación oficial](https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md).

El descargador usa TLS validado y descarta Authorization al seguir una redirección a otro servidor. Descarga a `.zip.partial`, reintenta errores transitorios y sólo publica el nombre definitivo tras comprobar tamaño y SHA-256. Si el servidor ignora Range, reinicia el temporal; nunca concatena una respuesta completa a un fragmento. Un archivo final o un temporal de tamaño completo con huella incorrecta se conserva para diagnóstico: separarlo manualmente antes de una descarga nueva.

En mac24, el prefijo `SSL_CERT_FILE=/etc/ssl/cert.pem` resolvió el fallo de certificados de T1. En la red del equipo original se comprobó que Kaggle requiere además las CAs ya confiadas por ese host. Sólo en un entorno con esa necesidad se puede añadir `HMREC_EXTRA_CA_FILE=RUTA_A_BUNDLE_CONFIABLE`; se verificó aquí con `.runtime/build-ca.pem` generado previamente por T1. No copiar ese bundle a otra máquina ni desactivar verificación TLS. La descarga usa biblioteca estándar del host: no instala Kaggle, TensorFlow ni paquetes científicos.

## Fuente e integridad

Fuente fijada: `h-and-m-personalized-fashion-recommendations.zip`, **30,810,293,747 bytes**, SHA-256 `e4695ac063fd621a29ba83523aa97be4041a91c45fba331162784f55771fbe9c`.

| Tabla | Filas esperadas |
| --- | ---: |
| transactions_train | 31,788,324 |
| customers | 1,371,980 |
| articles | 105,542 |
| sample_submission | 1,371,980 |

`quick` comprueba tamaño, inventario, rutas, duplicados y cabeceras; **no acredita integridad del contenido**. `full` añade SHA-256 del ZIP completo, incluida la identidad de todas las imágenes respecto de la [validación original](../evidence/dataset-validation-summary.json). No vuelve a decodificar los 105,100 JPEG. La ingestión lee completamente los cuatro CSV (incluyendo CRC ZIP), valida tipos, nulos, claves únicas, referencias y conteos. Conserva las compras repetidas. No extrae imágenes.

Lectura CSV incremental en bloques de 16 MiB con esquema explícito: IDs como texto, fecha como date32, precio float64 y canal int8. Los demás atributos originales se conservan como texto nullable. [Lectura incremental de Arrow](https://arrow.apache.org/docs/python/csv.html#incremental-reading).

Se generan shards Parquet Zstd por mes, con recibos de filas/SHA-256. Archivos temporales y bloqueos exclusivos impiden dos escritores. `manifest.json` aparece al completar y validar todo; `load_dataset` rechaza archivos incompletos o alterados. Las rutas del manifiesto son relativas; al cargarlo se vincula una raíz local que no se serializa. Reanudar no duplica filas. Un fallo de disco controlado forma parte de las pruebas. Esto no simula corrupción física del disco ni garantiza recuperación de sectores dañados.

## Contratos temporales

| Uso | Información disponible | Etiquetas |
| --- | --- | --- |
| Entrenamiento | Perfil al inicio de cada semana | Compras de esa semana, terminando antes de 2020-09-09 |
| Validación | Compras anteriores a 2020-09-09 | [2020-09-09, 2020-09-16) |
| Prueba reservada | Compras anteriores a 2020-09-16 | [2020-09-16, 2020-09-23) |
| Snapshot de servicio | Compras anteriores a 2020-09-23 | Sin evaluación; no usar para validar/probar |

Semanas de miércoles a miércoles, alineadas con validación. La primera observada comienza el 2018-09-19 y tiene historia parcial. `examples/` contiene una fila por usuario/artículo/semana, relevancia binaria implícita y `frequency` separada. Cada fila incluye `purchase_count`, `mean_price`, `online_share`, `last_purchase` y `recency_days` calculados únicamente con compras anteriores a esa semana. Usuario sin historia: contador/precio/proporción 0, fecha nula y recencia -1.

`in_catalog` significa primera compra observada del artículo **anterior** al inicio de la semana. Es una aproximación de disponibilidad, no una fecha real de alta. Se conservan todas las etiquetas fuera del catálogo para el denominador principal de recuperación. `eligible` marca historia del usuario y artículo disponible; no elimina filas. T3 deberá declarar exactamente qué filas utiliza y registrar cualquier exclusión, ponderación de frecuencia o estrategia para arranque en frío. No confundir etiquetas binarias con filas originales.

El catálogo contiene metadatos de tipo, grupo y color. Son un snapshot estático sin fecha de modificación: no podemos reconstruir cambios históricos de esas descripciones. No se usan atributos actuales de clientes para inferir comportamiento pasado. Los vocabularios del catálogo se ajustan con artículos observados antes de 2020-09-09 y se congelan para prueba/servicio; índice **0 OOV**, **1 faltante**, observados desde 2. Los ejemplos semanales conservan IDs sin codificar y sus indicadores de disponibilidad; T3 debe respetarlos al construir candidatos. No hay normalizador ajustado con datos de prueba.

La configuración resuelta registra cortes; el reloj de la computadora sólo fecha el reporte. Generar las particiones de prueba no equivale a evaluar ni permite utilizarlas para seleccionar el modelo.

## Salidas y recursos

```text
data/kaggle_hm/                         # ZIP fuente, ignorado por Git
data/processed/hm-v1/
  source.json                          # Huella de fuente y versión del layout
  transactions/month=YYYY-MM/*.parquet
  articles/ customers/ sample_submission/
  manifest.json                        # Publicado sólo al completar validación
artifacts/features/hm-v1/
  train/                               # Corte 2020-09-09, vocabulario de entrenamiento
  test/                                # Corte 2020-09-16, vocabulario congelado
  serving/                             # Corte 2020-09-23; nunca para evaluar
  examples/{train,validation,test}/week=YYYY-MM-DD/
artifacts/data-jobs/RUN_ID/             # Entorno, configuración y resultado
artifacts/handoff/RUN_ID.zip            # Paquete pequeño para GitHub
```

T2 exige 20 GiB libres adicionales al ZIP como margen conservador. Los recursos se versionan en `configs/resources/*.json`, separados del dataset y de los futuros hiperparámetros de entrenamiento:

| `--resources` | Colima recomendado | Límite del job | Polars / OMP / MKL |
| --- | --- | --- | --- |
| `base` (predeterminado) | 8 GiB, 4 CPU | 5 GiB, 3 CPU | 2 hilos cada uno |
| `mac24` | 14 GiB, 4 CPU | 10 GiB, 3 CPU | 2 hilos cada uno |

`--profile hm-project` elige la VM; `--resources mac24` elige el presupuesto del job. El lanzador usa `docker create` y aplica memoria, CPU y hilos desde el JSON; cambiar sólo `compose.yaml` no lo modifica. Los comandos de desarrollo `prepare-data` y `verify` siguen usando los límites de Compose (5 GiB/3 CPU).

La sección `colima` del JSON documenta la asignación recomendada: **el lanzador no redimensiona ni reinicia la VM**. En la Mac de 24 GB, comprueba que no haya trabajos activos, detén sólo los servicios propios que no necesites y amplía el perfil existente conservando los volúmenes:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem docker --context colima-hm-project ps
# Sólo cuando no exista un trabajo activo que deba continuar:
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py down
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/setup-docker-macos.py --profile hm-project --cpus 4 --memory 14 --disk 100
SSL_CERT_FILE=/etc/ssl/cert.pem docker --context colima-hm-project info --format 'CPU={{.NCPU}} MemoryBytes={{.MemTotal}} Arch={{.Architecture}}'
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job start --machine-id mac24 --profile hm-project --resources mac24 --dataset synthetic --expected-commit SHA_COMPLETO
```

El preparador rechaza reiniciar Colima si quedan contenedores activos; no detengas servicios ajenos para eludirlo. El job requiere que el motor tenga al menos su límite de RAM más 1 GiB de margen, y CPU suficientes. Este control utiliza la capacidad total del motor, no estima memoria libre ni promete ausencia de competencia con otras aplicaciones. Docker ve algo menos que la RAM nominal de Colima; por eso no se exige igualdad con el valor nominal. No ejecutar trabajos pesados simultáneos. 14 GiB deja aproximadamente 10 GiB fuera de la VM en mac24; más memoria no garantiza menor duración.

Antes de `docker start` se comprueba con `docker inspect` que memoria, cuota CPU y los tres límites de hilos coincidan con el perfil. Si no coinciden, el job no arranca y su diagnóstico permanece exportable. `environment.json`, `status` y el reporte exportado incluyen `resources` (nombre, contenido y SHA-256), `limits` solicitados y `effective_limits` verificados. La RAM y CPU reales del motor quedan en `environment.json`. Se registra también `memory_swap_bytes` tal como lo devuelve Docker; el perfil no modifica la política de swap del motor.

Para añadir otro presupuesto, versiona un JSON con el mismo esquema, enteros positivos, hilos no mayores que CPU del job y al menos 1 GiB de diferencia entre VM y job. Los nombres admiten letras minúsculas, números, `_` y `-`, empezando por letra. Haz commit antes de una corrida oficial; no edites perfiles para reanudar un experimento existente.

El pico RSS del proceso y el pico de cgroup se reportan por separado: el segundo incluye memoria contabilizada por el contenedor, como caché de archivos. Disco libre antes/después puede variar por otros procesos; `output_bytes` mide la huella de los directorios procesados, incluyendo salidas conservadas de ejecuciones anteriores. Al comparar presupuestos, conserva commit, dataset, hilos y condiciones de reutilización de particiones: una primera ingestión y una repetición con particiones verificadas no son tiempos directamente comparables.

La primera medición completa en el equipo original procesó 31,788,324 filas y 28,245,922 etiquetas semanales: 31,292,772 compras train, 255,241 validación y 240,311 prueba. Hay 22,674,665 etiquetas train marcadas elegibles, 11,385 etiquetas de validación fuera de catálogo y 8,334 de prueba fuera de catálogo. Las cifras finales, identidad del código y límites de verificación quedan en [data-pipeline.json](../evidence/data-pipeline.json). Esta medición no acredita T2 en mac24 ni factibilidad del entrenamiento T3.

El lanzador final se probó desde el commit limpio `867eb20d697ce9f1eb354008f822a61af5d106d7`: corrida completa `data-dev-arm64-20261019T175036Z-2348601b`, salida 0 y 90.747 segundos reutilizando particiones verificadas. La primera ingestión sin particiones previas tardó 124.179 segundos. Las 58 pruebas únicas (51 de T2 y 7 de bootstrap Docker) pasaron; los tres hallazgos de la revisión independiente tienen regresiones RED→GREEN. Ambos reportes están publicados como Releases privadas enlazadas desde la evidencia.
