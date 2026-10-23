# Plataforma local de recomendaciones H&M — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar el mejor recomendador personalizado entre los candidatos evaluados con H&M, operado localmente y reproducible desde una instalación limpia, con evidencia de calidad, latencia y recuperación; conservar los experimentos del curso y explicar la selección.

**Architecture:** El pipeline transforma H&M completo a Parquet; los experimentos PyTorch permanecen reproducibles e historial28d+popularidad7d (H) queda seleccionado tras cerrar Q1–Q4. FastAPI servirá una versión portable verificada y PostgreSQL conservará estado operativo; Airflow coordinará preparación/publicación y MLflow conservará experimentos. Docker Compose separa servicio y trabajos pesados.

**Tech Stack:** Polars, Parquet, PostgreSQL, FastAPI, Streamlit, Airflow LocalExecutor, uv y Docker Compose para datos/servicio; PyTorch CPU y MLflow para experimentos. Qdrant queda disponible para experimentos vectoriales, sin ser dependencia del futuro perfil `serve` del motor seleccionado.

**Spec:** [Diseño local y reproducibilidad](../specs/2026-10-18-despliegue-local-reproducible-design.md). Este plan concreta sus entregables y actualiza el calendario preliminar. Elaborado el 18 de octubre de 2026; revisión del 20 de octubre: [selección por calidad confirmada por el usuario](../../model-selection.md), que prevalece sobre la obligación anterior de desplegar dos torres/Qdrant. Los cambios siguientes son planificación, no servicio implementado.

## Estado y respuesta a «¿ya podemos desplegar?»

**Estado vigente del 21 de octubre:** T1–T4 y Q1–Q4 están cerradas. La [auditoría Q4](../../../evidence/quality-decision-mac24.json) selecciona H bajo criterios predefinidos; T4-A verifica su paquete y la [auditoría T4-B v2](../../../evidence/serving-host-review-mac24.json) confirma HTTP interno y desde el host, activación, reinicio y rollback en mac24. Sigue T5: interfaz, eventos idempotentes y actualización temporal de perfiles. Los contratos de T4–T7 de abajo aplican a H. Latencia bajo carga, test y aceptación final siguen reservados para T8. No repetir entrenamientos ni campañas cerradas.

**Antecedente del 19 de octubre de 2026:** T1 está implementada y revisada, con 10 pruebas satisfactorias y diagnóstico del entorno sin errores. Existe infraestructura desplegable de PostgreSQL y Qdrant, imágenes de aplicación/trabajos, locks y scripts de configuración. Todavía no hay un recomendador entrenado ni una API de recomendaciones. Consultar [la guía de esta entrega](../../bootstrap.md) y su [evidencia](../../../evidence/bootstrap-2026-10-19.json). El calendario siguiente conserva las etapas del proyecto completo.

**Extensión de T1 para la segunda Mac:** instalador, instrucciones para su agente y lector de paquetes implementados. El recorrido completo se verificó en el equipo de desarrollo con 26 pruebas satisfactorias (las 10 originales y 16 de intercambio). La [evidencia del instalador](../../../evidence/second-mac-script-2026-10-19.json) conserva esa distinción: la ejecución física de mac24 ya pasó (35 pruebas, [revisión](../../../evidence/mac24-bootstrap-review-2026-10-19.json)).

**Cambio de transporte confirmado:** la suite alcanza 35 pruebas con el envío por GitHub Releases. Subida, listado, descarga, reintento y lectura del reporte se comprobaron contra el repositorio privado; consultar [la evidencia](../../../evidence/github-handoff-2026-10-19.json). El dataset se descargará desde Kaggle en la segunda Mac.

| Elemento | Estado comprobado | Evidencia / trabajo restante |
| --- | --- | --- |
| H&M completo | Listo | 31,788,324 transacciones, 1,371,980 clientes, 105,542 artículos; 16 controles satisfactorios |
| Docker local | Listo en este Mac ARM64 | Compose, construcción, HTTP, DNS, montajes y persistencia comprobados |
| Recursos | Capacidad medida en mac24 | Host 24 GiB; Colima 14 GiB/4 CPU; job 10 GiB/3 CPU/2 hilos, época completa sin swap ni OOM |
| Arquitectura | Definida | Todos los componentes se ejecutarán localmente; CPU como referencia |
| Repositorio e infraestructura base | Implementados y comprobados | Git local, locks, imágenes, configuración privada y bootstrap repetible comprobados |
| Datos procesados | T2 probada también en mac24 | Parquet completo, cortes y ejemplos semanales conciliados; evidencia mac24-t2-2026-10-19.json |
| Motor y servicios | H seleccionado; paquete T4-A verificado | Dos torres/MLflow, grafos y ranking conservados como experimentos; API, interfaz y DAG pendientes |
| Otra computadora | T1–T3, Q1–Q4 y T4-A probadas en mac24 | Reportes publicados y revisados; transporte del paquete completo y servicio pendientes |

Fuentes locales: [validación de H&M](../../../validation_hm/README.md), [evidencia Docker](../../../evidence/docker-preparation-2026-10-18.json) y [guía de entorno](../../setup.md). Los recursos libres son una fotografía de esa preparación, no una reserva garantizada. No se repitió la lectura integral del ZIP para redactar este plan.

**Ampliación del 19 de octubre: segunda Mac.** La decisión vigente del usuario es GitHub privado para código y reportes: Git y adjuntos de Releases, respectivamente. H&M se descargará directamente de Kaggle en la segunda Mac; ya no se necesita carpeta compartida ni AirDrop. El [diseño de ejecución entre equipos](../specs/2026-10-19-entrenamiento-dos-macs-design.md) incluye las instrucciones de T1 que ya se pueden ejecutar y propone adelantar la instalación física de T7. T2–T3 deberán incluir registro de ejecuciones, perfiles de recursos y exportación/importación de reportes antes del primer entrenamiento allá. La Mac actual recibirá los reportes para analizarlos; importar y servir un modelo completo continúa dependiendo de T4/T7. `origin` apunta al repositorio privado [clothing-recommendation](https://github.com/emilianodesu/clothing-recommendation), con autenticación y escritura verificadas para la cuenta propietaria. El instalador `scripts/start-second-mac.py`, el transporte `scripts/github-handoff.py`, el lector `scripts/read-handoff.py` y [la guía del operador](../../second-mac.md) automatizan T1 y su reporte. Los comandos `train-job` ya están implementados y probados en mac24. T2 ya permite perfiles de recursos versionados: `base` (5 GiB) y `mac24` (10 GiB, con Colima recomendado de 14 GiB), comprobación de capacidad, límites efectivos en el reporte y conservación del presupuesto al reanudar. El perfil ampliado ya se midió en T2 y T3 de la Mac física de 24 GB.

**T2 implementada:** descarga portable con reanudación, fuente fijada por SHA-256, ingestión íntegra por lotes, Parquet mensual, snapshots y ejemplos semanales. La primera corrida completa del equipo original concilia 31,788,324 transacciones, con 51 pruebas T2 satisfactorias. Ver [guía de datos](../../data.md) y [medición](../../../evidence/data-pipeline.json). Se añade `data-job` para ejecución desacoplada, identidad de imagen/commit y reportes por GitHub. T2 y la época completa T3 ya pasaron físicamente en mac24; ver las evidencias mac24-t2 y mac24-t3 del 19 de octubre. La selección en validación ya se revisó; aceptación final sobre test pendiente.

## Global Constraints

- Todo local, sin servicios de pago, Hopsworks ni dependencia del runtime de Codex. No se necesita GPU. El framework es una decisión técnica subordinada a la evidencia; Q3 probará LightGBM CPU y fijará su dependencia después de verificar compatibilidad.
- Referencia provisional H, `recent_then_popular`: historial de 28 días antes de `data_as_of`, por última compra descendente/frecuencia descendente/article_id ascendente; completar con popularidad de 7 días (frecuencia descendente/article_id ascendente). Hasta 200 candidatos y 12 artículos únicos. Sin historial, popularidad. Q4 decide el motor con el protocolo nuevo; dos torres/64 dimensiones quedan como experimento.
- Procesar el histórico completo; cada algoritmo sólo utiliza datos anteriores a su corte y ventana documentados. Conservar la evidencia de entrenamiento masivo PyTorch ya obtenida; preparar la regla elegida no se denomina entrenamiento neuronal. Los pilotos no sustituyen una evaluación full.
- CPU, lotes acotados y un trabajo pesado a la vez. Evitar cargar todas las transacciones en memoria. Entrenamiento y medición de latencia se ejecutan por separado en la prueba de referencia.
- Perfiles Compose `demo`, `serve` y `pipeline`. La demo funciona con datos sintéticos y sin Kaggle. Servir una versión importada no exige reentrenar ni conectar al MLflow original.
- Objetivos de plataforma: `linux/arm64` y `linux/amd64`. Sólo se marcará como probado lo realmente ejecutado; emulación y segunda ruta no equivalen a otra máquina física.
- Dependencias bloqueadas, imágenes por digest, configuración explícita y rutas portables. Python 3.12 es el candidato inicial para la aplicación; se confirma o reemplaza documentadamente en la prueba de compatibilidad de T1. Los scripts del host conservan Python 3.9+.
- Credenciales propias por instalación, fuera de Git e imágenes. Sólo el descargador recibe el token Kaggle. Publicar API e interfaz en `127.0.0.1`; bases de datos en red interna.
- Originales, perfiles, modelos reales y respaldos fuera del repositorio público. No extraer todas las imágenes: resolver las solicitadas con caché limitada y una imagen de sustitución.
- `data_as_of` explícito para datos de 2020; separar `event_time` de `received_at`. No usar el reloj actual como corte del histórico ni contaminar evaluación con eventos de demostración.
- Conservar volúmenes al detener; instalación repetible, migraciones versionadas y restauración en volúmenes nuevos. No usar `prune`, borrar originales ni reemplazar configuración privada durante bootstrap.
- Internet sólo para aprovisionamiento inicial. Probar operación sin Internet con datos, imágenes y dependencias ya almacenados. Dormir o apagar el equipo suspende el servicio.

## Review Focus

1. **Datos futuros o eventos fuera de orden:** ninguna característica incorpora información posterior al corte; los eventos repetidos no duplican compras. Pruebas en T2 y T5.
2. **Usuario desconocido, atributos ausentes o pocos candidatos:** devolver un resultado válido, sin duplicados y con respaldo declarado; no fallar por los 442 artículos sin imagen. Pruebas en T4 y T5.
3. **Fallo a mitad de publicación o mezcla de versiones:** cada solicitud usa una sola versión compatible de reglas, catálogo, historiales y popularidad; la versión anterior continúa disponible. Pruebas en T4 y T6.
4. **Memoria o disco agotado y reintento del trabajo:** conservar entradas, no publicar salidas parciales y permitir reanudar; medir una época completa antes de comprometer capacidad y fechas. Pruebas en T2, T3 y T6.
5. **Otro usuario, otra ruta o paquete corrupto:** instalación sin rutas personales, importación que verifica huellas y rechaza rutas inseguras, restauración independiente de volúmenes originales. Pruebas en T1 y T7.

---

## Entregables y calendario

Estimación inicial: **8 semanas, 12–15 horas por semana**, sujeta a disponibilidad real y a la medición de CPU. La primera semana prueba dos tamaños de datos; a más tardar al cerrar la segunda se exige una época sobre todo el conjunto de entrenamiento. Si no es viable, se reestima antes de seguir acumulando componentes. La demo completa queda para la semana 4, afinando el calendario esquemático del diseño.

| Etapa | Semanas | Entregable verificable | Condición de salida |
| --- | --- | --- | --- |
| E1 · Base reproducible | 1 | Git, locks, imágenes iniciales, configuración y scripts | Bootstrap repetible; dependencias importan dentro de los contenedores |
| E2 · Datos y factibilidad | 1–2 | Parquet completo, cortes, perfiles y reporte de recursos | Conteos conciliados, ausencia de fuga y una época completa medida |
| E3 · Recomendador | 3 | Experimentos PyTorch y reglas, evaluación temporal y selección explicada | Métricas, memoria y tiempo registrados; selección sólo con validación |
| E4 · Primer despliegue funcional | 4 | API, Streamlit y versión portable del motor seleccionado | Demo de punta a punta y servicio de una versión preparada con H&M |
| E5 · Operación automática | 5 | Eventos, actualización de perfiles y DAG | Eventos idempotentes, reintentos seguros y frescura medida |
| E6 · Recuperación y traslado | 6 | Exportación, importación, backup, restore y manual | Arranque en instalación limpia sin cuenta ni rutas del autor |
| E7 · Rendimiento | 7 | Informe de carga y ajustes | Comparar resultados medidos con las metas; registrar limitaciones |
| E8 · Entrega académica | 8 | Informe final, evidencia, demo y guía del evaluador | Evaluación final aislada y procedimiento completo ejecutado |

T1–T8 siguientes corresponden a estos entregables, con T3 iniciada tempranamente para dimensionar CPU. E4 es el primer despliegue funcional; E8 es la entrega completa, con pruebas operativas. **El calendario anterior es histórico:** la ampliación aprobada Q1–Q4 se inserta antes de E4; estima 6–9 jornadas de ingeniería más ejecución/intercambio, sin prometer mantener fechas originales. Grafos pasa a ser parte de esa campaña; no queda relegado a después de la entrega.

## Estructura objetivo

Este árbol describe **la entrega futura**, no archivos ya implementados. Se conservan la evidencia y los scripts existentes. `data/`, `artifacts/` y `backups/` son directorios de ejecución ignorados por Git; `tests/fixtures/` contiene únicamente datos sintéticos versionables.

```text
FinalProject/
├── README.md                       # Instalación, inicio rápido y comandos
├── pyproject.toml / uv.lock         # Paquete hm_recommender y dependencias
├── .python-version / .env.example
├── .gitignore / .dockerignore
├── compose.yaml                    # Perfiles demo, serve, pipeline y checks de salud
├── docker/
│   ├── app.Dockerfile               # API e interfaz, imagen de servicio
│   ├── jobs.Dockerfile              # Procesamiento, entrenamiento y evaluación
│   ├── airflow.Dockerfile           # Jobs instalados + entorno Airflow separado
│   ├── airflow/                    # requirements.in y requirements.lock
│   ├── images.lock.json            # Tags, digests y plataformas verificadas
│   └── smoke/                      # Prueba de Docker ya existente
├── configs/
│   ├── demo.yaml / hm.yaml          # Semillas, cortes, lotes y modelo
│   └── benchmark.yaml              # Carga y condiciones del experimento
├── src/hm_recommender/
│   ├── cli.py / config.py / contracts.py
│   ├── data/                       # download.py, validate.py, ingest.py, synthetic.py
│   ├── features/                   # build.py: perfiles y variables a un corte
│   ├── model/                      # two_tower.py, train.py, evaluate.py
│   ├── ranking/                    # history.py: núcleo compartido con evaluación
│   ├── releases/                   # package.py, registry.py, contracts.py
│   ├── api/                        # app.py, schemas.py, recommend.py, events.py
│   ├── events/                     # aggregate.py, replay.py
│   └── ui/                         # app.py, images.py
├── migrations/                     # Esquema operativo y versiones
├── dags/                           # build_release.py, refresh_profiles.py
├── scripts/
│   ├── setup-docker-macos.py        # Ya existe
│   ├── doctor.py / docker-smoke.sh  # Ya existen; ampliar diagnóstico para el producto
│   ├── project.py                  # Entrada portable de operación desde el host
│   ├── bootstrap.py / backup.py / restore.py
│   └── benchmark.py
├── tests/
│   ├── fixtures/                   # Casos sintéticos deterministas
│   ├── unit/                       # Cortes, métricas, contratos y modelo
│   └── integration/                # HTTP, publicación, recuperación y portabilidad
├── docs/
│   ├── setup.md / data.md / architecture.md
│   ├── operations.md / evaluation.md / reproducibility.md
│   ├── course-coverage.md / final-report.md / demo.md
│   ├── model-selection.md          # Criterio vigente y explicación de resultados
│   └── superpowers/                # Diseño y este plan
├── evidence/                       # JSON/CSV/Markdown sanitizados de cada prueba
├── notebooks/                      # Exploración; no son el pipeline productivo
├── validation_hm/                  # Evidencia histórica de integridad existente
├── data/                           # ZIP, particiones y caché local, sin versionar
├── artifacts/                      # Checkpoints y versiones portables, sin versionar
└── backups/                        # Respaldos privados, sin versionar
```

## Contratos compartidos

Q4 confirmó H. Los contratos de servicio siguientes quedan vigentes para su implementación, conservando integridad/versionado, publicación atómica y portabilidad. El paquete no requiere modelos o grafos; los experimentos se conservan por separado.

Los contratos existentes se conservan en `src/hm_recommender/contracts.py`; T4-A añadirá los contratos de servicio schema2 en `src/hm_recommender/releases/contracts.py`, importados por API y publicación sin duplicarlos. Los manifiestos usan JSON versionado con rutas relativas y huellas SHA-256. Las tablas usan Parquet. Los identificadores se conservan como cadenas, incluidos ceros iniciales de artículos.

| Contrato | Campos / contenido obligatorio |
| --- | --- |
| `DatasetManifest` | `schema_version`, `dataset_id`, `source_sha256`, `min_date`, `max_date`, conteos y lista de particiones con filas y huellas |
| `FeatureManifest` | `schema_version`, `dataset_id`, `data_as_of`, `feature_schema_version`, archivos de vocabulario, catálogo y perfiles; conteos y huellas |
| `TrainingResult` | `run_id`, `checkpoint_path`, configuración efectiva, semillas, épocas, filas consumidas, dispositivo, tiempo y memoria máxima |
| `EvaluationReport` | Corte y población, MAP@12, NDCG@12, Recall@200 exacto, cobertura, segmentos y referencia de popularidad; denominadores y filas excluidas |
| `HistoryReleaseManifest` (nuevo, schema 2) | `release_id`, commit, hash fuente, `data_as_of`, estrategia `recent_then_popular`, protocolo de reglas `recent-history-v1`, selección `quality-v1` enlazada al ZIP Q4 y su huella, ventanas 28/7, desempates, inventario con tamaños/SHA-256/conteos, evidencia enlazada por hash y versiones de software; sin campos vectoriales obligatorios |
| `HistoryPublicationResult` (nuevo) | `release_id`, conteos de catálogo/historiales, comprobaciones de ranking y estado; activación sólo después de validar |
| `EventInput` | `event_id`, `customer_id`, `article_id`, `event_type`, `event_time`, `origin`, `request_id` opcional; servidor añade `received_at` |
| `HistoryRecommendationResponse` (nuevo) | `request_id`, `release_id`, `strategy`, `features_timestamp`, `fallback`, lista ordenada de `{article_id, rank, reason}` sin repetidos; `reason` es `recent_purchase` o `popular_7d`, no probabilidad de compra |

La versión seleccionada incluirá `catalog.parquet`, `customers.parquet` (todos los compradores anteriores al corte), `histories.parquet`, `popularity.parquet`, `rules.json`, `validation-reference.json` y `manifest.json`. Historias: customer_id/article_id/last_purchase/frequency; popularidad: article_id/frequency/rank; catálogo con IDs y atributos. Guardar el orden completo de artículos disponibles en popularidad y todos los artículos únicos por historial para poder filtrar antes de limitar; sin filtros, top200 debe coincidir exactamente con el diagnóstico congelado. Construir historias para todos los compradores observados antes del corte, sin usar etiquetas de validación para decidir a quién incluir. `validation-reference.json` enlazará el ZIP de decisión Q4 y las referencias de paridad Q1-H V3/diagnóstico histórico, con sus SHA y funciones distintas (selección y métricas de validación). Referenciar el ZIP de validación y su SHA, conservando su corte: un paquete a otro corte no hereda esas métricas como si se hubieran medido de nuevo. Los contratos vectoriales schema1 ya presentes en `src/hm_recommender/contracts.py` se conservan para compatibilidad; los nuevos contratos vivirán en `releases/contracts.py` y no permitirán interpretar schema1 como schema2. Un hash verifica integridad, no autorización de redistribución.

El punto de entrada del host es `python3 scripts/project.py <acción>`, sin depender de un entorno Python científico en el host. Éste invoca Compose; la CLI instalada en las imágenes se llama `hmrec`. Ya funcionan `bootstrap`, `check`, `status`, `down` y `verify --suite bootstrap|unit`. La tabla siguiente conserva el contrato objetivo: las demás acciones se implementarán en etapas posteriores.

| Acción | Resultado |
| --- | --- |
| `bootstrap` | Configuración privada propia, directorios, imágenes y migraciones; repetible |
| `download-data` / `validate-data --mode quick\|full` | Descargar con acceso propio o comprobar ZIP importado; reporte de integridad |
| `demo` | Generar datos sintéticos, preparar y publicar el motor seleccionado; después levantar servicio e interfaz |
| `pipeline --config configs/hm.yaml` | Ejecutar ingestión → estado temporal del motor → controles/evaluación disponible → preparación de versión; entrenamiento experimental separado |
| `up --profile serve\|pipeline` / `down` | Arrancar servicios elegidos o detener conservando volúmenes |
| `verify --suite NOMBRE` | Ejecutar pruebas del grupo y guardar resultados; código distinto de cero ante fallo |
| `export-release --release-id ID --output ARCHIVO` / `import-release --input ARCHIVO` | Trasladar una versión; importación valida y prepara, sin activar automáticamente |
| `activate-release --release-id ID` | Activar una versión verificada; permite volver a la anterior |
| `backup --output DIRECTORIO` / `restore --input DIRECTORIO --project-name NOMBRE` | Respaldar y restaurar en un proyecto Compose nuevo, sin sobrescribir el original |
| `benchmark --config configs/benchmark.yaml` | Carga controlada e informe de tiempos, errores y recursos |

## Task 1 · T1 — Base del repositorio y entorno ejecutable

**Archivos:** crear `pyproject.toml`, `uv.lock`, `.python-version`, `.env.example`, `.dockerignore`, `compose.yaml`, `docker/app.Dockerfile`, `docker/jobs.Dockerfile`, `docker/images.lock.json`, `src/hm_recommender/{__init__,cli,config,contracts}.py`, `scripts/project.py`, `scripts/bootstrap.py`, `tests/integration/test_bootstrap.py`. Modificar `.gitignore`, `README.md`, `docs/setup.md` y `scripts/doctor.py`.

**Interfaces:** `bootstrap(project_root: Path) -> None`; `load_config(path: Path) -> dict`; `project.py` devuelve 0 sólo si la acción termina correctamente. Crear los contratos compartidos anteriores y un esquema de configuración con tipos, límites y campos desconocidos rechazados.

- [x] Inicializar Git local y revisar exclusiones antes del primer commit; conservar el ZIP existente. Incluir código, configuración de ejemplo y evidencia sanitizada; excluir secretos, datos y entornos temporales.
- [x] Resolver y construir el entorno CPU: probar importaciones PyTorch, Polars, clientes PostgreSQL/Qdrant y MLflow; resolver por separado Airflow. Fijar versiones verificadas e imágenes por digest. Registrar qué arquitectura realmente se ejecutó y cuál sólo resolvió dependencias.
- [x] Implementar bootstrap sin rutas del autor: generar secretos una sola vez, crear directorios y manejar permisos de volúmenes en Linux. Separar bases y usuarios PostgreSQL de aplicación, Airflow y MLflow. Añadir puertos configurables y diagnóstico de conflictos.
- [x] Añadir `test_bootstrap_preserves_secrets_and_data` y `test_bootstrap_path_with_spaces`: dos ejecuciones conservan bytes de secretos y un registro existente; funciona con otra ruta y sin variables de Codex. Usar un proyecto Compose de prueba aislado.
- [x] Ejecutar `python3 scripts/project.py verify --suite bootstrap`; exigir salida 0, servicios base sanos y segunda ejecución sin pérdida ni duplicados. Actualizar guía y guardar un commit de E1.

## Task 2 · T2 — Datos completos y features temporales

**Archivos:** crear `src/hm_recommender/data/{download,validate,ingest,synthetic}.py`, `src/hm_recommender/features/build.py`, `configs/demo.yaml`, `configs/hm.yaml`, `docs/data.md`, `tests/fixtures/hm_synthetic/`, `tests/unit/test_temporal_features.py`, `tests/integration/test_ingest.py`. Ampliar `cli.py` y `scripts/project.py`.

**Interfaces:** `ingest(source_zip: Path, output_dir: Path) -> DatasetManifest`; `build_features(dataset: DatasetManifest, data_as_of: date, output_dir: Path) -> FeatureManifest`. La CLI ofrece `hmrec data ingest`, `hmrec data validate` y `hmrec features build` con parámetros de entrada, salida y corte explícitos.

- [x] Convertir el código útil del notebook en funciones del paquete: descarga a temporal con reintentos, importación de ZIP y modos quick/full. Conservar el manifiesto de la fuente; rechazar archivos incompletos, rutas de archivo inseguras y esquemas inesperados antes de publicar resultados.
- [x] Leer por lotes y escribir Parquet particionado por mes. Publicar cada partición completa mediante reemplazo atómico; validar filas y huellas para reanudar. Conservar 31,788,324 transacciones en la capa de datos; documentar cualquier exclusión posterior. No eliminar compras repetidas legítimas del histórico.
- [x] Fijar entrenamiento inicial anterior a `2020-09-09`, validación `[2020-09-09, 2020-09-16)` y prueba `[2020-09-16, 2020-09-23)`. Para cada ejemplo temporal, construir variables sólo con antecedentes; ajustar vocabularios y transformaciones exclusivamente con datos disponibles al corte. Reservar valores OOV y faltantes.
- [x] Definir ejemplos semanales: perfil al inicio de la semana y compras como etiquetas de esa semana. Consolidar relevancia binaria por artículo para métricas, conservando frecuencias como variables separadas. Ante la ausencia de fechas de alta del catálogo, usar la primera compra observada antes del corte como aproximación de disponibilidad y declarar esa limitación; reportar compras de artículos fuera del catálogo evaluado, sin ocultarlas del denominador principal de recuperación.
- [x] Probar `test_future_purchase_does_not_change_past_features`, `test_ids_preserve_leading_zeros` y `test_ingest_resume_after_partial_write`: agregar una compra futura no cambia features anteriores; reintentar no duplica filas; salida parcial no aparece como válida. Simular disco insuficiente con fallo de escritura controlado.
- [x] Ejecutar `python3 scripts/project.py verify --suite data` y la ingestión real; exigir pruebas satisfactorias y conciliación de conteos. Guardar `evidence/data-pipeline.json`, documentación y commit de T2. E2 permanece abierta hasta la época completa de T3.

## Task 3 · T3 — Modelo PyTorch, capacidad y evaluación

**Archivos:** crear `src/hm_recommender/model/{two_tower,train,evaluate}.py`, `docs/evaluation.md`, `tests/unit/test_model.py`, `tests/unit/test_ranking_metrics.py`, `tests/integration/test_training.py`. Modificar `configs/hm.yaml`, `configs/demo.yaml`, `compose.yaml` y `cli.py` para MLflow y entrenamiento.

**Interfaces:** `train(features: FeatureManifest, config: dict, output_dir: Path) -> TrainingResult`; `evaluate(result: TrainingResult, features: FeatureManifest, config: dict) -> EvaluationReport`. `TwoTower.encode_user(batch) -> Tensor[B,64]` y `TwoTower.encode_item(batch) -> Tensor[B,64]`; puntuación por producto punto.

- [x] Implementar torres con ID de usuario más historial agregado y atributos del artículo: ID, categoría, grupo de producto y color. Comenzar con negativos muestreados del catálogo conocido al corte; evitar artículos positivos del mismo ejemplo. Guardar configuración, semillas, optimizador y checkpoints reanudables.
- [x] Medir dos tamaños de datos al comenzar y una época completa como puerta de factibilidad al cerrar la semana 2: filas efectivamente consumidas, ejemplos/s, duración, pico de RAM y disco. Ajustar lotes y trabajadores dentro del modelo acordado. No dar por suficiente la asignación de 8 GiB sin esta medición.
- [x] Registrar ejecuciones en MLflow con metadatos PostgreSQL y artefactos servidos por HTTP. Evitar rutas absolutas del host en referencias portables. En demo se permite registro local dentro del paquete de resultados, sin arrancar todo MLflow.
- [x] Implementar MAP@12 con denominador `min(12, relevantes)`, NDCG@12, Recall@200 exacto, cobertura y segmentos de usuarios/artículos nuevos. Excluir de métricas por usuario a quienes no tengan compras en el periodo y declarar su número. Comparar popularidad con el mismo corte; no convertirla en un segundo sistema de producción.
- [x] Probar métricas con rankings pequeños de resultado conocido y `test_train_save_load_preserves_scores` con tolerancia `atol=1e-6` en el mismo dispositivo. `test_checkpoint_resume_preserves_progress` comprueba que una interrupción no marca el trabajo como terminado ni pierde el contador de progreso.
- [x] Ejecutar `python3 scripts/project.py verify --suite model`, entrenar sobre las particiones completas y guardar métricas de validación. Evidencia histórica en `evidence/cpu-feasibility.json`; ejecución física en `evidence/mac24-t3-2026-10-19.json`.
- [x] Revisar candidatos sólo con validación y seleccionar historial+popularidad; conservar controles y explicación en `docs/model-selection.md`.
- [ ] Al cierre, congelar reglas, reconstruir estado antes del corte de test usando train+validación y evaluar una única vez sobre prueba. Para experimentos aprendidos, reentrenar sólo si forma parte del protocolo final predefinido. No ajustar contra test. Guardar cierre de E3.

**Estado operativo T3:** demo, pilotos y época completa en mac24 aprobados; ver [evidencia física](../../../evidence/mac24-t3-2026-10-19.json) y [evaluación](../../evaluation.md#evidencia-física-de-mac24). Full consumió 22,674,665 filas en 247.391 s de entrenamiento (309.936 s de job), sin swap ni OOM. La medición de capacidad está demostrada; la concentración inicial (cobertura 0.05147%) motivó los diagnósticos que llevaron a la selección actual. La aceptación final sobre test sigue pendiente. Test permanece reservado; el paquete T4-A y el servicio T4-B posteriores ya fueron revisados y cerrados. Los intentos reutilizan `train-job` y reportes privados con experiment_id estable.

El [diagnóstico reproducible](../../diagnostics.md) y sus campañas posteriores ya se ejecutaron sobre los checkpoints cerrados. Sus métricas, concentración y controles explican la selección actual, sin usar test.

El experimento de [tres épocas](../../three-epochs.md) empeoró MAP/NDCG y no superó popularidad7d. La campaña de [historial reciente](../../recent-history.md) selecciona historial+popularidad; añadir el modelo no demuestra mejora de MAP y reduce NDCG/Recall. El usuario confirmó priorizar calidad sobre framework y conservar los experimentos académicos. [Decisión vigente](../../model-selection.md): congelar ese motor para T4, adaptado abajo. Test y aceptación final permanecen pendientes; T4-B ya está cerrado; no se inicia otra campaña automáticamente.

## Task 4 · T4 — Motor seleccionado, versión portable y API

**Dependencia satisfecha:** [Q1–Q4](2026-10-20-calidad-temporal-grafos-ranking.md) cerradas y H seleccionado, con congelación en `evidence/quality-decision-mac24.json`. El detalle siguiente queda confirmado como siguiente implementación; las casillas distinguen implementación local de reproducción mac24.

Se divide en dos entregables independientes después de Q4. T4-A empaqueta H y no requiere entrenamiento. T4-B comienza con su paquete y prueba de paridad aprobados. T4-A está implementada, reproducida y revisada; seguir `docs/releases.md`. T4-B está implementada, reproducida y cerrada tras revisar el seguimiento mac24 v2, incluido acceso HTTP desde el host.

### T4-A · Paquete y paridad offline

**Archivos:** crear `src/hm_recommender/ranking/{__init__,history}.py`, `src/hm_recommender/releases/{__init__,contracts,package,worker}.py`, `scripts/release_job.py`, `tests/unit/test_history_release.py`, `tests/integration/test_release_parity.py`, `tests/host/test_release_handoff.py` y `docs/releases.md`. Modificar `model/recent_history.py`, `model/diagnostics.py`, `cli.py`, `scripts/project.py`, `scripts/data_job.py`, `scripts/handoff.py` y `scripts/github-handoff.py` para compartir el núcleo y transportar **sólo reportes**. Importaciones de `model/` se conservan como wrappers compatibles cuando sea necesario.

**Interfaces:** `build_history_release(dataset: DatasetManifest, data_as_of: date, output_dir: Path, *, evidence_archive: Path | None = None) -> HistoryReleaseManifest`; `verify_history_release(directory: Path) -> HistoryReleaseManifest`; `rank_history(history: list[str], popularity: list[str], eligible: set[str], *, limit: int = 200) -> list[str]`. Archivos y campos: contrato schema2 anterior. El núcleo de ranking no importa Torch y recibe el corte explícito; ningún default usa la fecha de hoy. El worker produce un reporte agregado con identidad de la versión, paridad, recursos y huellas; nunca incluye perfiles o transacciones en el ZIP de intercambio.

- [x] Escribir y ejecutar RED `test_history_rank_matches_fixed_fixture`: historial por fecha/frecuencia/ID, popularidad por frecuencia/ID, deduplicación, usuario sin historial y hasta 200 candidatos. `test_cutoff_excludes_future_events` excluye eventos en el corte; `test_build_includes_non_validation_buyers` exige incluir compradores anteriores al corte que no aparecen en validación.
- [x] Escribir RED `test_unfiltered_parity_with_frozen_diagnostic`: corte 2020-09-09 y ventanas 28/7; fixture con relevantes fuera de catálogo y usuario desconocido. Exigir igualdad de top200 y métricas `atol=1e-10, rtol=0` contra H de Q1-V3 (`quality-mac24-20261020T201805Z-63eb4569`) y su diagnóstico histórico fijado, sin cambiar sus reportes ni reglas. Generalizar helpers existentes en `ranking/history.py`, implementar contratos y constructor por lotes; pasar las pruebas y las regresiones de diagnóstico.
- [x] Escribir RED `test_incomplete_or_corrupt_release_not_publishable`, `test_schema1_not_read_as_schema2` y `test_release_paths_are_relative`: salida temporal más publicación atómica local, sin sobreescribir una versión diferente. Verificar inventario exacto, tamaños, hashes, corte, IDs únicos, orden, conteos y referencias. Si el corte es distinto, registrar la evaluación original como referencia, nunca como métrica del nuevo paquete.
- [x] Añadir `release-job start/status/export` con commit limpio, dataset `synthetic` o `hm`, corte explícito, recursos `base`/`mac24`, un job pesado y reporte final también ante fallo. El protocolo de reglas es `recent-history-v1`; enlazar por separado la selección Q4 `quality-v1` y su evidencia congelada. Reutilizar controles de recursos existentes; no convertir un `train-job` en constructor ni modificar el contrato de sus reportes históricos. Probar lector/ZIP malformado y ausencia de clientes/pesos en el inventario de intercambio.
- [x] Implementar `verify --suite releases` para ejecutar pruebas unitarias, integración de paquetes/paridad y host. Ejecutar desde imagen Docker sin montajes auxiliares; repetir bootstrap si cambia infraestructura. Luego corrida demo local y construcción/paridad H&M en mac24 con recursos actuales. En full, conciliar los 72,019 compradores/102,967 artículos de la validación con el ZIP revisado, pero construir el paquete para todos los compradores anteriores al corte. Reportar memoria, disco, duración y límites; no acreditar capacidad del paquete a partir del diagnóstico anterior.
- [x] Exportar/validar/publicar reportes cerrados por GitHub privado, conservando fallos; datos de servicio quedan locales en cada Mac. Registrar `evidence/releases.json`, guía y commit probado antes de preparar el prompt mac24. Una vez recibida la ejecución, cerrar T4-A sin iniciar test ni cambiar el candidato.

**Estado de entrega: T4-A cerrada** tras [auditar ambos reportes mac24](../../../evidence/releases-review-mac24.json) y sus fuentes Q4/Q1/diagnóstico. Full: paquete de 59,229,801 bytes, 1,351,314 compradores históricos, 949,748 pares, 102,967 artículos, paridad para 72,019 compradores V3, 29.048 s y pico cgroup 2.248 GiB, sin swap/OOM. El paquete y las tablas permanecen en mac24; aquí se verificaron reportes y huellas, sin recalcular predicciones privadas. La suite independiente y preservación de 3,049 originales son declaraciones remotas. `docs/mac24-t4a-prompt.md` es histórico. API y latencia no están acreditadas.

### T4-B · API, activación y rollback

**Archivos:** crear `src/hm_recommender/releases/registry.py`, `src/hm_recommender/api/{__init__,app,schemas,recommend}.py`, `migrations/002_releases.sql`, `tests/integration/test_release_activation.py`, `tests/integration/test_recommendations.py` y `docs/architecture.md`. Modificar `compose.yaml`, `docker/app.Dockerfile`, `cli.py`, `scripts/project.py`, `pyproject.toml`/`uv.lock` sólo si se separan extras de servicio. No eliminar la instalación experimental ni volúmenes Qdrant existentes.

**Interfaces:** `prepare_release(directory: Path) -> HistoryPublicationResult`; `activate_release(release_id: str) -> None`. API `POST /recommend` recibe `{customer_id, k: 1..12, filters}` y devuelve `HistoryRecommendationResponse`; filtros iniciales de igualdad por `product_type_name`, `product_group_name`, `colour_group_name`, rechazando claves desconocidas con 422. `GET /health/live`, `/health/ready` y `/metrics` exponen estado operativo. `fallback=true` indica que no hay historial elegible y se usa sólo popularidad; ausencia de versión válida devuelve 503. `rank` es ordinal, no un score neuronal ni una probabilidad.

- [x] Escribir RED de paridad HTTP sin filtros con T4-A, `test_unknown_user_uses_popularity`, `test_filters_apply_before_candidate_limit`, `test_empty_filter_returns_empty` y rechazo de `k` fuera de 1..12. Aplicar filtros al catálogo antes de seleccionar hasta 200, completar sin duplicados y devolver hasta 12. Si hay menos candidatos válidos, devolver sólo esos; mantener compras previas elegibles. Documentar que calidad bajo filtros todavía no está medida.
- [x] Escribir RED `test_activation_failure_keeps_previous_release`, `test_concurrent_requests_never_mix_releases` y `test_restart_recovers_committed_release`. Implementar un proceso FastAPI, estado precargado de una versión inmutable, puntero persistido y actualización completa; una solicitud conserva una referencia durante toda su ejecución. Conservar la versión anterior para rollback, sin cargar parcialmente la siguiente.
- [x] Implementar el perfil `serve` con PostgreSQL/API y futuro Streamlit, sin exigir Qdrant, MLflow o entrenamiento. Probar `test_history_serving_without_vector_or_training_services` y que el núcleo no importa Torch. No declarar dependencia científica eliminada de la imagen mientras el lock actual siga instalándola; una separación de extras exige sus pruebas de construcción propias.
- [x] Ejecutar `python3 scripts/project.py verify --suite serving` con HTTP real, versión identificable, activación, reinicio, paridad y rollback. Fijar un presupuesto de servicio a partir de la medición de T4-A; no asumir que todos los perfiles caben por haber evaluado 72,019 compradores. Medir las metas de carga en T8; no confundir duración offline con latencia.
- [x] Registrar evidencia de API/versión y commit. Reproducir la construcción con los datos propios de cada Mac; cualquier transporte posterior de paquetes completos usa un contrato separado del ZIP de reportes actual. No publicar IDs de compradores como código o reportes.

**Entrega T4-B cerrada:** API y registro PostgreSQL, paridad filtrada, exclusión de propietarios simultáneos, fallo seguro ante confirmación incierta, reinicio y rollback implementados. `serve` usa API/PostgreSQL. La [auditoría mac24 v2](../../../evidence/serving-host-review-mac24.json) verifica 877 recomendaciones internas y diez desde localhost, cinco etapas, 20.607 s y pico API de 1.015 GiB sin swap/OOM. T4B-01/T4B-02 están resueltos; H&M quedó activo. API 4 GiB/2 CPU/2 hilos; probe 3 GiB/1 CPU/2 hilos. [Guía](../../serving.md); ambos prompts T4-B son históricos. La muestra HTTP no reemplaza paridad offline exhaustiva T4-A ni benchmark T8. Las 175 pruebas y preservación de 3,072 archivos son declaraciones separadas de mac24; aquí se auditaron ZIP, huellas y fuentes. Siguiente desarrollo: T5, conservando originales y test reservado.

## Task 5 · T5 — Interfaz, eventos y actualización de perfiles

**T5/E4 cerradas tras revisión:** [auditoría H&M](../../../evidence/events-operational-review-mac24.json), protocolo `events-operational-v1`, corrida `events-mac24-20261023T030009Z-3f97f2a0`. Doce capturas, continuidad UTC/monotónico y controles funcionales/persistencia correctos; 24 min 42 s. Se conservan presión y pico histórico PostgreSQL, sin acreditar margen de memoria o carga. No repetir T5. T6 es el siguiente desarrollo a solicitud del usuario. Las notas de revisión siguientes son históricas y no reabren esta entrega.

**Seguimiento recibido y revisado:** [diagnóstico de relojes/energía](../../clock-diagnostics.md), [auditoría](../../../evidence/events-clock-review-mac24.json). La consulta alcanzó 16 MiB sin cubrir la ventana histórica y conserva fallo válido; la causa sigue desconocida. El [prompt mac24](../../mac24-t5-clock-prompt.md) es histórico y no se repite. Los controles de continuidad/reposo están probados por separado; sigue integrarlos y comprobar el nuevo protocolo antes de otra ejecución H&M. Los criterios históricos y recursos permanecen intactos.

**Estado de implementación y revisión del 22 de octubre:** código y pruebas locales disponibles; [guía](../../events.md). El [diagnóstico H&M fue auditado](../../../evidence/events-memory-review-mac24.json): doce capturas completas, comprobaciones funcionales y persistencia correctas; sigue fallando por pico histórico PostgreSQL de 512.258 MiB frente a 512 MiB, presente antes de semilla. Hay 164,089 eventos `max` nuevos y cero swap/OOM capturados. **T5/E4 siguen abiertas**. La diferencia entre 15,767.860 s UTC y 1,593.043 s monotónicos impide comparar rendimiento. Sigue resolver relojes/continuidad y definir un contrato operativo versionado antes de otro ensayo; [detalle](../../events-memory.md#revisión-en-la-mac-principal-2026-10-22). Ambos prompts T5 son históricos, sin repetición automática ni aumento de RAM. Los entornos operativos PostgreSQL están separados del paquete H; la demo usa reloj explícito y reconstrucción transaccional de conteos diarios. T6 no se activa automáticamente.

**Decisión Q4 aplicada:** actualizar estado de H con ventanas 28/7 y reglas congeladas. No requiere refit neuronal ni reconstrucción de grafo; caducidad temporal, trazabilidad e intercambio conservan los contratos siguientes.

**Archivos:** crear `src/hm_recommender/api/events.py`, `src/hm_recommender/events/{aggregate,replay}.py`, `src/hm_recommender/ui/{app,images}.py`, `migrations/003_events.sql`, `tests/integration/test_events.py`, `tests/integration/test_demo.py`. Modificar `api/app.py`, `api/schemas.py`, `compose.yaml`, `cli.py` y `scripts/project.py`.

**Interfaces:** `POST /events` recibe `EventInput`; insertar un evento nuevo devuelve 201 y repetir el mismo ID/payload devuelve 200 sin duplicarlo; ID existente con contenido diferente devuelve 409. `aggregate_events(release_id: str, received_before: datetime) -> dict` retorna conteos y última actualización. Los perfiles actualizados conservan el esquema y la versión de la estrategia activa.

- [x] Registrar eventos en PostgreSQL con unicidad de `event_id`; si la base no confirma escritura, devolver 503. Separar eventos reales del dataset, eventos sintéticos y simulaciones. Una compra modifica el historial; vistas/clics se registran para trazabilidad y no se convierten en compras para el ranking seleccionado.
- [x] Actualizar perfiles operativos en PostgreSQL de forma transaccional y reanudable, conservando inmutables los perfiles iniciales del paquete. Usar `received_at` para detectar trabajo nuevo y `event_time` para ubicarlo temporalmente; eventos tardíos recalculan el perfil afectado. Mantener un reloj de simulación explícito y separar sus datos de validación/prueba. Para avanzar ese reloj, conservar conteos diarios por usuario/artículo y popularidad: los agregados last_purchase/frequency de T4 no bastan para caducar contribuciones al salir de las ventanas 28/7. Reconstruir desde las transacciones locales o esos conteos, usando sólo eventos anteriores al nuevo corte, y publicar el estado completo de forma atómica.
- [x] Construir Streamlit con selección de cliente, catálogo y 12 recomendaciones, versión activa, corte de datos y frescura. Emitir eventos con ID estable para reintentos. Mostrar imagen de sustitución cuando falta JPEG; cachear sólo imágenes solicitadas, con límite configurable y extracción segura.
- [x] Añadir `test_duplicate_event_is_counted_once`, `test_late_event_rebuilds_correct_profile`, `test_database_failure_is_not_acknowledged` y prueba de demo HTTP: generar sintéticos → preparar motor → publicar → recomendar → comprar → actualizar → volver a recomendar. Comprobar que la actualización conserva compatibilidad y cambia el perfil; no prometer que toda compra cambie necesariamente el ranking.
- [x] Demo/suite local y flujo H&M operativos comprobados; evidencia local y auditoría mac24 enlazadas. E4 completada bajo el contrato operativo versionado; la programación automática se añade en T6.

## Task 6 · T6 — Orquestación, reintentos y observabilidad

**Estado:** implementación disponible; ver [operación](../../operations.md), [evidencia local](../../../evidence/pipeline-local.json) y [ensayo mac24](../../mac24-t6-prompt.md). **T6/E5 cerradas** tras [auditoría H&M](../../../evidence/pipeline-review-mac24.json): dos ciclos, frescura 30.465/276.191 s, reinicio e idempotencia correctos. Se conserva presión PostgreSQL; no acredita carga sostenida. El prompt mac24 es histórico. La publicación H&M queda deshabilitada para conservar la versión/stream T5; la activación se prueba con sintéticos aislados.

**Archivos:** crear `docker/airflow.Dockerfile`, `docker/airflow/requirements.in`, `docker/airflow/requirements.lock`, `dags/build_release.py`, `dags/refresh_profiles.py`, `tests/integration/test_pipeline.py`, `docs/operations.md`. Modificar `compose.yaml`, `docker/images.lock.json`, `cli.py`, `scripts/project.py` y `api/app.py`.

**Interfaces:** el DAG de preparación/publicación llama a T2/T4 a través de la CLI instalada; transmite rutas de manifiestos, no DataFrames por XCom. Los entrenamientos T3 se mantienen como experimentos manuales separados. El DAG de perfiles llama `aggregate_events` de T5 cada cinco minutos. La CLI y Airflow ejecutan las mismas funciones.

- [x] Construir Airflow LocalExecutor con jobs CPU instalados y versiones resueltas separadamente del servicio web. Incluir los procesos requeridos por la versión elegida. No montar socket Docker ni añadir Celery o Redis. Limitar a un trabajo pesado concurrente.
- [x] Configurar publicación manual al inicio; automatizar una cadencia sólo tras medir su coste y validar el nuevo paquete, con `catchup` desactivado para construcciones pesadas. Reanudar perfiles con cursor de recepción e intervalos acotados después de suspensión, sin perder eventos ni disparar muchos trabajos vencidos.
- [x] Registrar duración, filas, reintentos, errores, versión y recursos. Añadir métricas de API para latencia, solicitudes, errores y uso de respaldo; no etiquetar métricas con ID de cliente ni registrar tokens. Exportar reportes de cada ejecución.
- [x] Probar `test_retry_does_not_duplicate_partition`, `test_failed_build_does_not_publish` y `test_restart_processes_pending_events`: fallar una etapa, reiniciar y verificar que la versión activa y los originales permanecen intactos; procesar exactamente una vez el efecto de eventos pendientes.
- [x] Ejecutar `python3 scripts/project.py verify --suite pipeline`, suite bootstrap y un DAG sintético completo. Ensayo `pipeline-development-20261023T054340Z-360a4df3`: dos ciclos, reinicio y 24 capturas; frescura 3.611/65.850 s. Ver evidencia local para commits y pruebas.
- [x] Flujo H&M auditado bajo pipeline-v2: 24 capturas, dos actualizaciones <600 s, H conservado. T6/E5 cerradas; límites de muestra y presión de memoria documentados.

## Task 7 · T7 — Instalación independiente, transferencia y recuperación

**Archivos:** crear `scripts/backup.py`, `scripts/restore.py`, `docs/reproducibility.md`, `tests/integration/test_release_transfer.py`, `tests/integration/test_backup_restore.py`, `tests/integration/test_offline.py`. Modificar `releases/package.py`, `scripts/project.py`, `.gitignore`, `README.md`, `docs/setup.md` y `docs/operations.md`.

**Interfaces:** `export_release(release_id: str, output: Path) -> Path`; `import_release(archive: Path, destination: Path) -> HistoryReleaseManifest`; `backup(output_dir: Path) -> Path`; `restore(input_dir: Path, project_name: str) -> None`. El manifiesto del respaldo enlaza dumps lógicos PostgreSQL, artefactos y versión activa. Los experimentos vectoriales, si se respaldan, son un conjunto separado; el motor seleccionado no necesita reconstruir Qdrant.

- [ ] Empaquetar versiones con rutas relativas; comprobar estrategia/schema2, corte, orden, huellas, conteos y archivos antes de importar. Rechazar `../`, rutas absolutas y enlaces que escapen del destino. Validar consultas desde el paquete completo autorizado sin MLflow original ni token Kaggle; reconstruir desde cero en otro equipo sí requiere datos propios. El transporte de estos paquetes no reutiliza sin extensión el protocolo de reportes pequeños. Activar por orden explícita después de verificar.
- [ ] Respaldar PostgreSQL de forma lógica y artefactos con manifiestos. Durante la copia, pausar nuevas ejecuciones y escrituras de eventos, esperar a que terminen los trabajos activos y capturar versión y cursor estables; servir recomendaciones puede continuar. Reanudar las escrituras y programación al terminar o ante error. Restaurar en proyecto Compose nuevo con credenciales propias; comprobar perfil, eventos, versión y recomendaciones antes de declararlo utilizable.
- [ ] Probar `test_corrupt_or_unsafe_release_is_rejected`, `test_import_without_original_mlflow`, `test_restore_into_empty_volumes` y funcionamiento sin salida a Internet después de precargar recursos. Mantener intactos los volúmenes originales durante la prueba.
- [ ] Ejecutar `python3 scripts/project.py verify --suite portability` en una instalación limpia, otra ruta con espacios y otro usuario. Repetir en un segundo equipo cuando esté disponible, registrando arquitectura y SO. Transferir imágenes con `docker save/load` si se necesita instalación sin registro; conservar manifiesto de plataforma y digests.
- [ ] Comparar IDs, orden y motivos de consultas fijas de la misma versión importada: igualdad exacta, excluyendo request_id. Para reentrenamientos experimentales, comparar métricas con tolerancias fijadas antes de la prueba; no exigir pesos idénticos entre arquitecturas. Guardar evidencia, límites de soporte y commit de E6.

## Task 8 · T8 — Rendimiento, cobertura académica y entrega

**Archivos:** crear `scripts/benchmark.py`, `configs/benchmark.yaml`, `docs/course-coverage.md`, `docs/final-report.md`, `docs/demo.md`, `tests/integration/test_acceptance.py`. Actualizar `docs/evaluation.md`, `README.md` y el documento académico al cerrar los resultados.

**Interfaces:** `benchmark(config_path: Path, output_dir: Path) -> dict` produce percentiles, tasa ofrecida/efectiva, errores y uso de recursos. `verify --suite acceptance` reúne las comprobaciones críticas sin repetir una descarga o entrenamiento completo si existen artefactos verificados de esa ejecución.

- [ ] Implementar carga con tasa de llegada controlada: 20 solicitudes/s, dos minutos de calentamiento y diez de medición. Registrar p50, p95, p99, tasa efectiva y errores; no ocultar solicitudes omitidas por saturación del generador. Objetivos: p95 < 200 ms y errores < 1%. Separar peticiones frías, uso de fallback y ranking con historial.
- [ ] Medir CPU y RAM de servicios y generador, hardware, tamaños del catálogo y perfiles, versión y estado del entrenamiento. Primero servir con entrenamiento pausado; después medir la degradación con trabajo pesado concurrente. Si no cabe todo, documentar el procedimiento secuencial y su impacto en frescura.
- [ ] Evaluar una única vez el procedimiento H congelado y P en test [2020-09-16, 2020-09-23), con estado reconstruido sólo desde datos anteriores al 2020-09-16; H coincide con elegido/referencia y no se duplica. Conservar población y métricas completas, sin retuning ni cambio de ganador a partir del resultado. Mantener T3 como evidencia histórica; no repetirlo por esta prueba. No sustituir test por una demo sintética ni prometer mejora sin medir.
- [ ] Ejecutar `python3 scripts/project.py verify --suite acceptance` y `python3 scripts/project.py benchmark --config configs/benchmark.yaml`. Guardar `evidence/acceptance.json`, `evidence/benchmark.json`, `evidence/reproducibility.md` y un inventario de versiones y pruebas; sólo marcar cumplidas las metas observadas.
- [ ] Entregar informe, guía de instalación, manual de operación, ficha del motor y experimentos, mapa de subtemas y guion de demo. Añadir capturas o video corto de instalación, recomendación, evento, actualización y recuperación; etiquetar la versión Git entregada. Guardar commit de E7/E8.

## Subtemas del curso y evidencia

| Subtema | Alcance | Evidencia de la entrega |
| --- | --- | --- |
| 4.1 Motivación, retroalimentación y arranque en frío | Central | Compras como señal implícita; eventos observados; tratamiento de usuarios/artículos desconocidos |
| 4.2 Recomendación basada en contenido | Experimento académico implementado | Atributos en la torre de artículos: componente de contenido del modelo híbrido, no motor puro de contenido |
| 4.3 Filtrado colaborativo | Experimento académico implementado | Relaciones usuario–artículo aprendidas de compras; conservar resultados aunque no gane |
| 4.4 Modelo de factor latente | Experimento académico implementado | Embeddings de 64 dimensiones/producto punto, sin atribuir implementación de SVD |
| 4.5 Evaluación de sistemas | Central | Cortes temporales, MAP@12, NDCG@12, Recall@200, cobertura, segmentos y errores |
| 4.6 Desafío Netflix | Contextual | Discusión bibliográfica y diferencias con feedback implícito; no es el dataset del proyecto |
| 1.1 | Herramienta aplicada en Q2 | Iteración y convergencia de PageRank personalizado acotado; no estudio separado de valores propios |
| 1.2–1.5 | Fuera del núcleo | Embeddings por sí solos no acreditan SVD/PCA/NMF/t-SNE |
| 2.1–2.6 | Fuera del núcleo | No se promete Apriori, FP-Growth ni PrefixSpan |
| 3.1, 3.3 y caminatas de 3.4 | Campaña Q2 cerrada y auditada | Grafo ponderado y control PageRank personalizado en subgrafos; declarar límites de escala/aproximación |
| 5.5 | Aplicación implementada en red comercial | Similitud de nodos; no equivale a una red social observada |
| 3.2 y 5.1–5.4 | Fuera de esta campaña | Sin HITS, comunidades ni comunidades traslapadas; no declarar cobertura completa del tema 5 |

La ampliación de grafos se ejecutó en Q2 antes de T4, siguiendo el protocolo y presupuesto propios de la campaña aprobada. No se presenta el grafo interno de Qdrant ni el DAG de Airflow como análisis de redes del curso. La entrega combina tema 4 y parte del tema 3; la infraestructura T4–T8 sigue siendo obligatoria. Comunidades y una red social real no forman parte de esta campaña.

## Pendientes que sí deben cerrarse

| Pendiente | Cuándo / cómo se resuelve | ¿Impide empezar? |
| --- | --- | --- |
| Versiones exactas y compatibilidad de imágenes | T1 reproducida en ambas Macs ARM64: locks, digests y ejecución; AMD64 físico no probado | No; conservar evidencia y probar AMD64 si se requiere ese soporte |
| Memoria y duración de construcción/servicio | T4-A midió paquete full de 56.49 MiB, 29.048 s y pico cgroup 2.248 GiB; RAM y latencia de API pendientes | No; sí condiciona presupuesto y concurrencia del servicio |
| Acceso a H&M desde otro equipo | Token propio y términos de Kaggle, o ZIP local obtenido con acceso autorizado | Sólo para reproducir datos reales; la demo no lo necesita |
| Prueba en el segundo equipo | Mac24 ya reprodujo T1–T3, Q1–Q4 y T4-A; quedan servicio y pruebas de restore en T7 | No; lo medido no acredita todavía portabilidad del futuro paquete de servicio |
| Remoto Git privado | Privado y escritura comprobada en ambas Macs; intercambio Git/Releases operativo | No; preservar autenticaciones propias de cada equipo |
| Calidad y latencia alcanzables | Q4 cerró selección en validación; T8 medirá test, carga y latencia | No; son criterios de validación, no capacidades ya demostradas |

No quedan decisiones obligatorias de nube, presupuesto, cuentas de plataforma de features o servicios administrados. El usuario eligió Git privado para compartir código y la visibilidad y escritura ya están configuradas; el acceso y el intercambio desde mac24 ya se comprobaron. El criterio final es que otra persona pueda partir del código, ejecutar la demo y servir una versión real autorizada siguiendo la documentación, sin usar esta cuenta ni esta computadora; además, el entrenamiento completo debe tener datos, configuración y evidencia trazables.

## Referencias y control del plan

La propuesta académica conserva la motivación y las referencias del proyecto. El [diseño técnico](../specs/2026-10-18-despliegue-local-reproducible-design.md#referencias-técnicas) reúne las fuentes oficiales de Kaggle, MLflow, Airflow, Docker, Colima, PyTorch y uv; T1 ya fijó las versiones verificadas en ARM64. Las instrucciones operativas vigentes están en README y `docs/`, sin depender de este chat.

Revisión del plan: datos y tiempo → T2; modelo y escala → T3; ampliación temporal/grafos/ranking y selección → Q1–Q4; publicación y servicio → T4; interfaz/eventos → T5; operación → T6; independencia y recuperación → T7; medición y curso → T8. Los cinco modos de fallo de Review Focus tienen pruebas asignadas. Este documento es planificación: ninguna casilla pendiente representa funcionalidad ya entregada.
