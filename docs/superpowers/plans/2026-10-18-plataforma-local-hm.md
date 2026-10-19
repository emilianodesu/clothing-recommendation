# Plataforma local de recomendaciones H&M — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar un recomendador personalizado entrenado con H&M completo, operado localmente y reproducible desde una instalación limpia, con evidencia de calidad, latencia y recuperación.

**Architecture:** Un pipeline por lotes transforma el histórico a Parquet y entrena un único modelo de dos torres. Una versión portable reúne modelo, transformaciones, catálogo, perfiles y vectores; FastAPI sirve recomendaciones mediante Qdrant y registra eventos en PostgreSQL. Airflow y MLflow coordinan y documentan los trabajos; Docker Compose permite ejecutar entrenamiento y servicio por separado según la memoria disponible.

**Tech Stack:** PyTorch CPU, Polars, Parquet, Qdrant, PostgreSQL, FastAPI, Streamlit, MLflow, Airflow LocalExecutor, uv y Docker Compose.

**Spec:** [Diseño local y reproducibilidad](../specs/2026-10-18-despliegue-local-reproducible-design.md). Este plan concreta sus entregables y actualiza el calendario preliminar. Elaborado el 18 de octubre de 2026; semanas relativas al inicio de implementación.

## Estado y respuesta a «¿ya podemos desplegar?»

**Avance del 19 de octubre de 2026:** T1 está implementada y revisada, con 10 pruebas satisfactorias y diagnóstico del entorno sin errores. Existe infraestructura desplegable de PostgreSQL y Qdrant, imágenes de aplicación/trabajos, locks y scripts de configuración. Todavía no hay un recomendador entrenado ni una API de recomendaciones. Consultar [la guía de esta entrega](../../bootstrap.md) y su [evidencia](../../../evidence/bootstrap-2026-10-19.json). El calendario siguiente conserva las etapas del proyecto completo.

| Elemento | Estado comprobado | Evidencia / trabajo restante |
| --- | --- | --- |
| H&M completo | Listo | 31,788,324 transacciones, 1,371,980 clientes, 105,542 artículos; 16 controles satisfactorios |
| Docker local | Listo en este Mac ARM64 | Compose, construcción, HTTP, DNS, montajes y persistencia comprobados |
| Recursos | Punto de partida disponible | Host de 16 GiB; Colima con 4 CPU, 8 GiB de RAM y disco virtual de 100 GiB; capacidad de entrenamiento aún por medir |
| Arquitectura | Definida | Todos los componentes se ejecutarán localmente; CPU como referencia |
| Repositorio e infraestructura base | Implementados y comprobados | Git local, locks, imágenes, configuración privada y bootstrap repetible comprobados |
| Datos procesados, modelo y servicios | Pendiente | No hay entrenamiento personalizado, API, índice, interfaz ni DAG implementados |
| Otra computadora | Pendiente de prueba | Hace falta un equipo prestado o de otro integrante para acreditar reproducción física; no impide comenzar |

Fuentes locales: [validación de H&M](../../../validation_hm/README.md), [evidencia Docker](../../../evidence/docker-preparation-2026-10-18.json) y [guía de entorno](../../setup.md). Los recursos libres son una fotografía de esa preparación, no una reserva garantizada. No se repitió la lectura integral del ZIP para redactar este plan.

## Global Constraints

- Todo local, sin servicios de pago, TensorFlow, Hopsworks ni dependencia del runtime de Codex. No se necesita GPU.
- Un modelo principal de dos torres, vectores iniciales de 64 dimensiones y producto punto. Recuperar hasta 200 candidatos y entregar hasta 12 artículos únicos. Popularidad es respaldo y referencia de evaluación.
- Procesar el histórico completo; excluir de entrenamiento únicamente lo que exijan los cortes y reglas documentadas. La muestra sirve para desarrollo y dimensionamiento, nunca para acreditar entrenamiento masivo.
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
3. **Fallo a mitad de publicación o mezcla de versiones:** cada solicitud usa un conjunto compatible de pesos, esquema, perfiles e índice; la versión anterior continúa disponible. Pruebas en T4 y T6.
4. **Memoria o disco agotado y reintento del trabajo:** conservar entradas, no publicar salidas parciales y permitir reanudar; medir una época completa antes de comprometer capacidad y fechas. Pruebas en T2, T3 y T6.
5. **Otro usuario, otra ruta o paquete corrupto:** instalación sin rutas personales, importación que verifica huellas y rechaza rutas inseguras, restauración independiente de volúmenes originales. Pruebas en T1 y T7.

---

## Entregables y calendario

Estimación inicial: **8 semanas, 12–15 horas por semana**, sujeta a disponibilidad real y a la medición de CPU. La primera semana prueba dos tamaños de datos; a más tardar al cerrar la segunda se exige una época sobre todo el conjunto de entrenamiento. Si no es viable, se reestima antes de seguir acumulando componentes. La demo completa queda para la semana 4, afinando el calendario esquemático del diseño.

| Etapa | Semanas | Entregable verificable | Condición de salida |
| --- | --- | --- | --- |
| E1 · Base reproducible | 1 | Git, locks, imágenes iniciales, configuración y scripts | Bootstrap repetible; dependencias importan dentro de los contenedores |
| E2 · Datos y factibilidad | 1–2 | Parquet completo, cortes, perfiles y reporte de recursos | Conteos conciliados, ausencia de fuga y una época completa medida |
| E3 · Recomendador | 3 | Modelo PyTorch, evaluación temporal y registro MLflow | Métricas, memoria y tiempo registrados; selección sólo con validación |
| E4 · Primer despliegue funcional | 4 | API, Qdrant, Streamlit y versión portable | Demo de punta a punta y servicio de una versión entrenada con H&M |
| E5 · Operación automática | 5 | Eventos, actualización de perfiles y DAG | Eventos idempotentes, reintentos seguros y frescura medida |
| E6 · Recuperación y traslado | 6 | Exportación, importación, backup, restore y manual | Arranque en instalación limpia sin cuenta ni rutas del autor |
| E7 · Rendimiento | 7 | Informe de carga y ajustes | Comparar resultados medidos con las metas; registrar limitaciones |
| E8 · Entrega académica | 8 | Informe final, evidencia, demo y guía del evaluador | Evaluación final aislada y procedimiento completo ejecutado |

T1–T8 siguientes corresponden a estos entregables, con T3 iniciada tempranamente para dimensionar CPU. E4 es el primer despliegue funcional; E8 es la entrega completa, con pruebas operativas. Grafos es una ampliación posterior al núcleo y no condiciona este calendario.

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
│   ├── releases/                   # package.py, registry.py, index.py
│   ├── api/                        # app.py, schemas.py, recommend.py, events.py
│   ├── events/                     # aggregate.py, replay.py
│   └── ui/                         # app.py, images.py
├── migrations/                     # Esquema operativo y versiones
├── dags/                           # train_release.py, refresh_profiles.py
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
│   └── superpowers/                # Diseño y este plan
├── evidence/                       # JSON/CSV/Markdown sanitizados de cada prueba
├── notebooks/                      # Exploración; no son el pipeline productivo
├── validation_hm/                  # Evidencia histórica de integridad existente
├── data/                           # ZIP, particiones y caché local, sin versionar
├── artifacts/                      # Checkpoints y versiones portables, sin versionar
└── backups/                        # Respaldos privados, sin versionar
```

## Contratos compartidos

Los tipos se definen una vez en `src/hm_recommender/contracts.py`; los módulos siguientes los importan. Los manifiestos usan JSON versionado con rutas relativas y huellas SHA-256. Las tablas usan Parquet. Los identificadores se conservan como cadenas, incluidos ceros iniciales de artículos.

| Contrato | Campos / contenido obligatorio |
| --- | --- |
| `DatasetManifest` | `schema_version`, `dataset_id`, `source_sha256`, `min_date`, `max_date`, conteos y lista de particiones con filas y huellas |
| `FeatureManifest` | `schema_version`, `dataset_id`, `data_as_of`, `feature_schema_version`, archivos de vocabulario, catálogo y perfiles; conteos y huellas |
| `TrainingResult` | `run_id`, `checkpoint_path`, configuración efectiva, semillas, épocas, filas consumidas, dispositivo, tiempo y memoria máxima |
| `EvaluationReport` | Corte y población, MAP@12, NDCG@12, Recall@200 exacto, cobertura, segmentos y referencia de popularidad; denominadores y filas excluidas |
| `ReleaseManifest` | `release_id`, commit, hash fuente, corte, contratos de features, dimensión 64, métrica `dot`, archivos con huellas, resultados y versiones de software |
| `PublicationResult` | `release_id`, colección, total de vectores, pruebas de consulta y estado; activación sólo después de validar |
| `EventInput` | `event_id`, `customer_id`, `article_id`, `event_type`, `event_time`, `origin`, `request_id` opcional; servidor añade `received_at` |
| `RecommendationResponse` | `request_id`, `release_id`, `features_timestamp`, `fallback`, lista de `{article_id, score}` sin repetidos |

La versión incluye `model.pt` con `state_dict`, `model_config.json`, `vocabularies.json`, `feature_schema.json`, `catalog.parquet`, `profiles.parquet`, `item_vectors.parquet`, `popularity.parquet`, `evaluation.json` y `manifest.json`. La tabla de vectores contiene `article_id` y un vector de 64 componentes. Cargar pesos con la opción segura de pesos, sin deserializar objetos de código arbitrarios. Un hash comprueba integridad, no otorga autorización para redistribuir datos.

El punto de entrada del host es `python3 scripts/project.py <acción>`, sin depender de un entorno Python científico en el host. Éste invoca Compose; la CLI instalada en las imágenes se llama `hmrec`. Ya funcionan `bootstrap`, `check`, `status`, `down` y `verify --suite bootstrap|unit`. La tabla siguiente conserva el contrato objetivo: las demás acciones se implementarán en etapas posteriores.

| Acción | Resultado |
| --- | --- |
| `bootstrap` | Configuración privada propia, directorios, imágenes y migraciones; repetible |
| `download-data` / `validate-data --mode quick\|full` | Descargar con acceso propio o comprobar ZIP importado; reporte de integridad |
| `demo` | Generar, entrenar y publicar datos sintéticos; después levantar servicio e interfaz |
| `pipeline --config configs/hm.yaml` | Ejecutar manualmente ingestión → features → entrenamiento → evaluación → publicación |
| `up --profile serve\|pipeline` / `down` | Arrancar servicios elegidos o detener conservando volúmenes |
| `verify --suite NOMBRE` | Ejecutar pruebas del grupo y guardar resultados; código distinto de cero ante fallo |
| `export-release --release-id ID --output ARCHIVO` / `import-release --input ARCHIVO` | Trasladar una versión; importación valida y prepara, sin activar automáticamente |
| `activate-release --release-id ID` | Activar una versión verificada; permite volver a la anterior |
| `backup --output DIRECTORIO` / `restore --input DIRECTORIO --project-name NOMBRE` | Respaldar y restaurar en un proyecto Compose nuevo, sin sobrescribir el original |
| `benchmark --config configs/benchmark.yaml` | Carga controlada e informe de tiempos, errores y recursos |

## T1 · Base del repositorio y entorno ejecutable

**Archivos:** crear `pyproject.toml`, `uv.lock`, `.python-version`, `.env.example`, `.dockerignore`, `compose.yaml`, `docker/app.Dockerfile`, `docker/jobs.Dockerfile`, `docker/images.lock.json`, `src/hm_recommender/{__init__,cli,config,contracts}.py`, `scripts/project.py`, `scripts/bootstrap.py`, `tests/integration/test_bootstrap.py`. Modificar `.gitignore`, `README.md`, `docs/setup.md` y `scripts/doctor.py`.

**Interfaces:** `bootstrap(project_root: Path) -> None`; `load_config(path: Path) -> dict`; `project.py` devuelve 0 sólo si la acción termina correctamente. Crear los contratos compartidos anteriores y un esquema de configuración con tipos, límites y campos desconocidos rechazados.

- [x] Inicializar Git local y revisar exclusiones antes del primer commit; conservar el ZIP existente. Incluir código, configuración de ejemplo y evidencia sanitizada; excluir secretos, datos y entornos temporales.
- [x] Resolver y construir el entorno CPU: probar importaciones PyTorch, Polars, clientes PostgreSQL/Qdrant y MLflow; resolver por separado Airflow. Fijar versiones verificadas e imágenes por digest. Registrar qué arquitectura realmente se ejecutó y cuál sólo resolvió dependencias.
- [x] Implementar bootstrap sin rutas del autor: generar secretos una sola vez, crear directorios y manejar permisos de volúmenes en Linux. Separar bases y usuarios PostgreSQL de aplicación, Airflow y MLflow. Añadir puertos configurables y diagnóstico de conflictos.
- [x] Añadir `test_bootstrap_preserves_secrets_and_data` y `test_bootstrap_path_with_spaces`: dos ejecuciones conservan bytes de secretos y un registro existente; funciona con otra ruta y sin variables de Codex. Usar un proyecto Compose de prueba aislado.
- [x] Ejecutar `python3 scripts/project.py verify --suite bootstrap`; exigir salida 0, servicios base sanos y segunda ejecución sin pérdida ni duplicados. Actualizar guía y guardar un commit de E1.

## T2 · Datos completos y features temporales

**Archivos:** crear `src/hm_recommender/data/{download,validate,ingest,synthetic}.py`, `src/hm_recommender/features/build.py`, `configs/demo.yaml`, `configs/hm.yaml`, `docs/data.md`, `tests/fixtures/hm_synthetic/`, `tests/unit/test_temporal_features.py`, `tests/integration/test_ingest.py`. Ampliar `cli.py` y `scripts/project.py`.

**Interfaces:** `ingest(source_zip: Path, output_dir: Path) -> DatasetManifest`; `build_features(dataset: DatasetManifest, data_as_of: date, output_dir: Path) -> FeatureManifest`. La CLI ofrece `hmrec data ingest`, `hmrec data validate` y `hmrec features build` con parámetros de entrada, salida y corte explícitos.

- [ ] Convertir el código útil del notebook en funciones del paquete: descarga a temporal con reintentos, importación de ZIP y modos quick/full. Conservar el manifiesto de la fuente; rechazar archivos incompletos, rutas de archivo inseguras y esquemas inesperados antes de publicar resultados.
- [ ] Leer por lotes y escribir Parquet particionado por mes. Publicar cada partición completa mediante reemplazo atómico; validar filas y huellas para reanudar. Conservar 31,788,324 transacciones en la capa de datos; documentar cualquier exclusión posterior. No eliminar compras repetidas legítimas del histórico.
- [ ] Fijar entrenamiento inicial anterior a `2020-09-09`, validación `[2020-09-09, 2020-09-16)` y prueba `[2020-09-16, 2020-09-23)`. Para cada ejemplo temporal, construir variables sólo con antecedentes; ajustar vocabularios y transformaciones exclusivamente con datos disponibles al corte. Reservar valores OOV y faltantes.
- [ ] Definir ejemplos semanales: perfil al inicio de la semana y compras como etiquetas de esa semana. Consolidar relevancia binaria por artículo para métricas, conservando frecuencias como variables separadas. Ante la ausencia de fechas de alta del catálogo, usar la primera compra observada antes del corte como aproximación de disponibilidad y declarar esa limitación; reportar compras de artículos fuera del catálogo evaluado, sin ocultarlas del denominador principal de recuperación.
- [ ] Probar `test_future_purchase_does_not_change_past_features`, `test_ids_preserve_leading_zeros` y `test_ingest_resume_after_partial_write`: agregar una compra futura no cambia features anteriores; reintentar no duplica filas; salida parcial no aparece como válida. Simular disco insuficiente con fallo de escritura controlado.
- [ ] Ejecutar `python3 scripts/project.py verify --suite data` y la ingestión real; exigir pruebas satisfactorias y conciliación de conteos. Guardar `evidence/data-pipeline.json`, documentación y commit de E2.

## T3 · Modelo PyTorch, capacidad y evaluación

**Archivos:** crear `src/hm_recommender/model/{two_tower,train,evaluate}.py`, `docs/evaluation.md`, `tests/unit/test_model.py`, `tests/unit/test_ranking_metrics.py`, `tests/integration/test_training.py`. Modificar `configs/hm.yaml`, `configs/demo.yaml`, `compose.yaml` y `cli.py` para MLflow y entrenamiento.

**Interfaces:** `train(features: FeatureManifest, config: dict, output_dir: Path) -> TrainingResult`; `evaluate(result: TrainingResult, features: FeatureManifest, config: dict) -> EvaluationReport`. `TwoTower.encode_user(batch) -> Tensor[B,64]` y `TwoTower.encode_item(batch) -> Tensor[B,64]`; puntuación por producto punto.

- [ ] Implementar torres con ID de usuario más historial agregado y atributos del artículo: ID, categoría, grupo de producto y color. Comenzar con negativos muestreados del catálogo conocido al corte; evitar artículos positivos del mismo ejemplo. Guardar configuración, semillas, optimizador y checkpoints reanudables.
- [ ] Medir dos tamaños de datos al comenzar y una época completa como puerta de factibilidad al cerrar la semana 2: filas efectivamente consumidas, ejemplos/s, duración, pico de RAM y disco. Ajustar lotes y trabajadores dentro del modelo acordado. No dar por suficiente la asignación de 8 GiB sin esta medición.
- [ ] Registrar ejecuciones en MLflow con metadatos PostgreSQL y artefactos servidos por HTTP. Evitar rutas absolutas del host en referencias portables. En demo se permite registro local dentro del paquete de resultados, sin arrancar todo MLflow.
- [ ] Implementar MAP@12 con denominador `min(12, relevantes)`, NDCG@12, Recall@200 exacto, cobertura y segmentos de usuarios/artículos nuevos. Excluir de métricas por usuario a quienes no tengan compras en el periodo y declarar su número. Comparar popularidad con el mismo corte; no convertirla en un segundo sistema de producción.
- [ ] Probar métricas con rankings pequeños de resultado conocido y `test_train_save_load_preserves_scores` con tolerancia `atol=1e-6` en el mismo dispositivo. `test_checkpoint_resume_preserves_progress` comprueba que una interrupción no marca el trabajo como terminado ni pierde el contador de progreso.
- [ ] Ejecutar `python3 scripts/project.py verify --suite model`; entrenar sobre las particiones completas y guardar `evidence/cpu-feasibility.json` y métricas de validación. Seleccionar configuración sólo con validación; al final, reentrenar con train+validación y evaluar una única vez sobre prueba, sin ajustar contra ella. Guardar commit de E3.

## T4 · Versión portable, índice y API de recomendaciones

**Archivos:** crear `src/hm_recommender/releases/{package,registry,index}.py`, `src/hm_recommender/api/{app,schemas,recommend}.py`, `migrations/002_releases.sql`, `tests/integration/test_release_activation.py`, `tests/integration/test_recommendations.py`, `docs/architecture.md`. Modificar `compose.yaml`, `docker/app.Dockerfile`, `cli.py` y `scripts/project.py`. T1 ya incorporó `001_bootstrap.sql`.

**Interfaces:** `build_release(result: TrainingResult, features: FeatureManifest, report: EvaluationReport, output_dir: Path) -> ReleaseManifest`; `prepare_release(manifest: ReleaseManifest) -> PublicationResult`; `activate_release(release_id: str) -> None`. API `POST /recommend` recibe `{customer_id, k: 1..12, filters}` y devuelve `RecommendationResponse`; `GET /health/live`, `/health/ready` y `/metrics` exponen estado operativo.

- [ ] Generar los archivos del contrato de versión, verificando huellas y compatibilidad. Crear una colección Qdrant por `release_id`, distancia producto punto y dimensión 64. Comprobar cantidad de vectores y consultas antes de considerar publicable la versión.
- [ ] Precargar modelo, transformaciones y respaldo al iniciar FastAPI, con `eval()` y `inference_mode()`. Comenzar con un proceso de servicio; la solicitud conserva una referencia a la versión completa durante toda su ejecución. Una activación prepara y comprueba el conjunto nuevo antes de intercambiarlo; conservar anterior para rollback y registrar versión activa para recuperación al reiniciar.
- [ ] Resolver filtros dentro de la búsqueda para evitar perder candidatos por filtrado tardío. Devolver hasta 12 artículos únicos de hasta 200 candidatos; no excluir automáticamente compras anteriores. Usuario sin historial usa popularidad compatible con filtros; Qdrant no disponible usa respaldo precargado y declara `fallback`. Si no hay una versión válida, readiness falla y la API devuelve 503.
- [ ] Probar `test_activation_failure_keeps_previous_release` y `test_concurrent_requests_never_mix_releases`, incluyendo caída/reinicio entre preparación y activación. Probar usuario desconocido, catálogo menor de 12, filtro sin resultados, `k` inválido y timeout de Qdrant. Consultas vacías válidas devuelven lista vacía; entrada inválida devuelve 422.
- [ ] Ejecutar `python3 scripts/project.py verify --suite serving`; exigir respuestas HTTP reales con versión identificable, índice íntegro y rollback satisfactorio. Medir Recall@200 del índice aproximado frente a búsqueda exacta, separándolo de Recall@200 de compras relevantes. Guardar evidencia y commit de API/versión.

## T5 · Interfaz, eventos y actualización de perfiles

**Archivos:** crear `src/hm_recommender/api/events.py`, `src/hm_recommender/events/{aggregate,replay}.py`, `src/hm_recommender/ui/{app,images}.py`, `migrations/003_events.sql`, `tests/integration/test_events.py`, `tests/integration/test_demo.py`. Modificar `api/app.py`, `api/schemas.py`, `compose.yaml`, `cli.py` y `scripts/project.py`.

**Interfaces:** `POST /events` recibe `EventInput`; insertar un evento nuevo devuelve 201 y repetir el mismo ID/payload devuelve 200 sin duplicarlo; ID existente con contenido diferente devuelve 409. `aggregate_events(release_id: str, received_before: datetime) -> dict` retorna conteos y última actualización. Los perfiles actualizados conservan el esquema y la versión del modelo activo.

- [ ] Registrar eventos en PostgreSQL con unicidad de `event_id`; si la base no confirma escritura, devolver 503. Separar eventos reales del dataset, eventos sintéticos y simulaciones. Una compra modifica las variables de historial; vistas/clics se registran para trazabilidad, sin introducir señales que el modelo no haya aprendido a interpretar.
- [ ] Actualizar perfiles operativos en PostgreSQL de forma transaccional y reanudable, conservando inmutables los perfiles iniciales del paquete. Usar `received_at` para detectar trabajo nuevo y `event_time` para ubicarlo temporalmente; eventos tardíos recalculan el perfil afectado. Mantener un reloj de simulación explícito y separar sus datos de validación/prueba.
- [ ] Construir Streamlit con selección de cliente, catálogo y 12 recomendaciones, versión activa, corte de datos y frescura. Emitir eventos con ID estable para reintentos. Mostrar imagen de sustitución cuando falta JPEG; cachear sólo imágenes solicitadas, con límite configurable y extracción segura.
- [ ] Añadir `test_duplicate_event_is_counted_once`, `test_late_event_rebuilds_correct_profile`, `test_database_failure_is_not_acknowledged` y prueba de demo HTTP: generar sintéticos → entrenar → publicar → recomendar → comprar → actualizar → volver a recomendar. Comprobar que la actualización conserva compatibilidad y cambia el perfil; no prometer que toda compra cambie necesariamente el ranking.
- [ ] Ejecutar `python3 scripts/project.py demo` y `python3 scripts/project.py verify --suite events`. Probar el mismo flujo con una versión H&M. Guardar evidencia y commit que completa E4; la programación automática se añade en T6.

## T6 · Orquestación, reintentos y observabilidad

**Archivos:** crear `docker/airflow.Dockerfile`, `docker/airflow/requirements.in`, `docker/airflow/requirements.lock`, `dags/train_release.py`, `dags/refresh_profiles.py`, `tests/integration/test_pipeline.py`, `docs/operations.md`. Modificar `compose.yaml`, `docker/images.lock.json`, `cli.py`, `scripts/project.py` y `api/app.py`.

**Interfaces:** el DAG de entrenamiento llama a las funciones T2–T4 a través de la CLI instalada; transmite rutas de manifiestos, no DataFrames por XCom. El DAG de perfiles llama `aggregate_events` de T5 cada cinco minutos. La CLI y Airflow ejecutan las mismas funciones.

- [ ] Construir Airflow LocalExecutor con jobs CPU instalados y versiones resueltas separadamente del servicio web. Incluir los procesos requeridos por la versión elegida. No montar socket Docker ni añadir Celery o Redis. Limitar a un trabajo pesado concurrente.
- [ ] Configurar entrenamiento manual al inicio, semanal sólo si la duración medida lo permite; `catchup` desactivado para entrenamientos. Reanudar perfiles con cursor de recepción e intervalos acotados después de suspensión, sin perder eventos ni disparar muchos trabajos vencidos.
- [ ] Registrar duración, filas, reintentos, errores, versión y recursos. Añadir métricas de API para latencia, solicitudes, errores y uso de respaldo; no etiquetar métricas con ID de cliente ni registrar tokens. Exportar reportes de cada ejecución.
- [ ] Probar `test_retry_does_not_duplicate_partition`, `test_failed_training_does_not_publish` y `test_restart_processes_pending_events`: fallar una etapa, reiniciar y verificar que el modelo activo y los originales permanecen intactos; procesar exactamente una vez el efecto de eventos pendientes.
- [ ] Ejecutar `python3 scripts/project.py verify --suite pipeline` y un DAG sintético completo. Ejecutar el flujo H&M con recursos medidos y demostrar frescura menor de diez minutos mientras el pipeline esté activo, o registrar el incumplimiento. Guardar commit de E5.

## T7 · Instalación independiente, transferencia y recuperación

**Archivos:** crear `scripts/backup.py`, `scripts/restore.py`, `docs/reproducibility.md`, `tests/integration/test_release_transfer.py`, `tests/integration/test_backup_restore.py`, `tests/integration/test_offline.py`. Modificar `releases/package.py`, `scripts/project.py`, `.gitignore`, `README.md`, `docs/setup.md` y `docs/operations.md`.

**Interfaces:** `export_release(release_id: str, output: Path) -> Path`; `import_release(archive: Path, destination: Path) -> ReleaseManifest`; `backup(output_dir: Path) -> Path`; `restore(input_dir: Path, project_name: str) -> None`. El manifiesto del respaldo enlaza dumps lógicos PostgreSQL, artefactos y versión activa; Qdrant puede reconstruirse desde vectores.

- [ ] Empaquetar versiones con rutas relativas; comprobar esquema, dimensiones, huellas y archivos antes de importar. Rechazar `../`, rutas absolutas y enlaces que escapen del destino. Reconstruir Qdrant y validar consultas sin MLflow original ni token Kaggle. Activar por orden explícita después de verificar.
- [ ] Respaldar PostgreSQL de forma lógica y artefactos con manifiestos. Durante la copia, pausar nuevas ejecuciones y escrituras de eventos, esperar a que terminen los trabajos activos y capturar versión y cursor estables; servir recomendaciones puede continuar. Reanudar las escrituras y programación al terminar o ante error. Restaurar en proyecto Compose nuevo con credenciales propias; comprobar perfil, eventos, versión y recomendaciones antes de declararlo utilizable.
- [ ] Probar `test_corrupt_or_unsafe_release_is_rejected`, `test_import_without_original_mlflow`, `test_restore_into_empty_volumes` y funcionamiento sin salida a Internet después de precargar recursos. Mantener intactos los volúmenes originales durante la prueba.
- [ ] Ejecutar `python3 scripts/project.py verify --suite portability` en una instalación limpia, otra ruta con espacios y otro usuario. Repetir en un segundo equipo cuando esté disponible, registrando arquitectura y SO. Transferir imágenes con `docker save/load` si se necesita instalación sin registro; conservar manifiesto de plataforma y digests.
- [ ] Comparar consultas fijas y puntuaciones de una versión importada con tolerancias registradas antes de la prueba; validar integridad de vectores y separar diferencias de ANN de errores de modelo. Para reentrenamiento, comparar métricas con tolerancias fijadas a partir de repeticiones de referencia; no exigir pesos idénticos entre arquitecturas. Guardar evidencia, límites de soporte y commit de E6.

## T8 · Rendimiento, cobertura académica y entrega

**Archivos:** crear `scripts/benchmark.py`, `configs/benchmark.yaml`, `docs/course-coverage.md`, `docs/final-report.md`, `docs/demo.md`, `tests/integration/test_acceptance.py`. Actualizar `docs/evaluation.md`, `README.md` y el documento académico al cerrar los resultados.

**Interfaces:** `benchmark(config_path: Path, output_dir: Path) -> dict` produce percentiles, tasa ofrecida/efectiva, errores y uso de recursos. `verify --suite acceptance` reúne las comprobaciones críticas sin repetir una descarga o entrenamiento completo si existen artefactos verificados de esa ejecución.

- [ ] Implementar carga con tasa de llegada controlada: 20 solicitudes/s, dos minutos de calentamiento y diez de medición. Registrar p50, p95, p99, tasa efectiva y errores; no ocultar solicitudes omitidas por saturación del generador. Objetivos: p95 < 200 ms y errores < 1%. Separar peticiones frías, uso de fallback e inferencia personalizada.
- [ ] Medir CPU y RAM de servicios y generador, hardware, tamaños del catálogo y perfiles, versión y estado del entrenamiento. Primero servir con entrenamiento pausado; después medir la degradación con trabajo pesado concurrente. Si no cabe todo, documentar el procedimiento secuencial y su impacto en frescura.
- [ ] Ejecutar evaluación final de T3 una vez cerradas las decisiones, conservar la población y métricas completas y registrar honestamente si no mejora a popularidad. No sustituir esa evaluación por la demo sintética ni prometer de antemano un umbral de calidad sin medición.
- [ ] Ejecutar `python3 scripts/project.py verify --suite acceptance` y `python3 scripts/project.py benchmark --config configs/benchmark.yaml`. Guardar `evidence/acceptance.json`, `evidence/benchmark.json`, `evidence/reproducibility.md` y un inventario de versiones y pruebas; sólo marcar cumplidas las metas observadas.
- [ ] Entregar informe, guía de instalación, manual de operación, ficha del modelo, mapa de subtemas y guion de demo. Añadir capturas o video corto de instalación, recomendación, evento, actualización y recuperación; etiquetar la versión Git entregada. Guardar commit de E7/E8.

## Subtemas del curso y evidencia

| Subtema | Alcance | Evidencia de la entrega |
| --- | --- | --- |
| 4.1 Motivación, retroalimentación y arranque en frío | Central | Compras como señal implícita; eventos observados; tratamiento de usuarios/artículos desconocidos |
| 4.2 Recomendación basada en contenido | Central | Categoría, grupo y color en la torre de artículos; explicar límites ante artículos nuevos |
| 4.3 Filtrado colaborativo | Central | Relaciones usuario–artículo aprendidas de compras históricas |
| 4.4 Modelo de factor latente | Central | Representaciones de 64 dimensiones y puntuación por producto punto |
| 4.5 Evaluación de sistemas | Central | Cortes temporales, MAP@12, NDCG@12, Recall@200, cobertura, segmentos y errores |
| 4.6 Desafío Netflix | Contextual | Discusión bibliográfica y diferencias con feedback implícito; no es el dataset del proyecto |
| 1.1–1.5 | Fuera del núcleo | Embeddings por sí solos no acreditan SVD/PCA/NMF/t-SNE ni método de potencias |
| 2.1–2.6 | Fuera del núcleo | No se promete Apriori, FP-Growth ni PrefixSpan |
| 3.3 y 5.1–5.3, 5.5 | Ampliación opcional de grafos | Grafo de coocurrencia de artículos con pesos normalizados, comunidades y similitud; explicar que es una red de interacción comercial |
| 3.1, 3.2, 3.4 y 5.4 | No comprometidos | PageRank, HITS, análisis espectral/caminatas y comunidades traslapadas no quedan cubiertos automáticamente |

La ampliación de grafos sólo comienza con E1–E7 terminados y tiempo disponible: construir aristas de compras de usuarios dentro de ventanas temporales, limitar grado para controlar memoria, obtener comunidades y usarlas para explicar diversidad de las recomendaciones. Requiere plan y evaluación propios; no se presenta el grafo interno de Qdrant ni el DAG de Airflow como análisis de redes del curso. La entrega principal cubre el tema 4; el deseo de combinar recomendaciones y grafos queda explícitamente como extensión, sin desplazar la infraestructura priorizada.

## Pendientes que sí deben cerrarse

| Pendiente | Cuándo / cómo se resuelve | ¿Impide empezar? |
| --- | --- | --- |
| Versiones exactas y compatibilidad de imágenes | T1: construir y ejecutar; locks y digests verificados | No; es el primer entregable técnico |
| Memoria y duración reales del entrenamiento completo | T2–T3: lectura por lotes y una época completa | No; sí condiciona el calendario y concurrencia |
| Acceso a H&M desde otro equipo | Token propio y términos de Kaggle, o ZIP local obtenido con acceso autorizado | Sólo para reproducir datos reales; la demo no lo necesita |
| Disponibilidad de un segundo equipo | Prueba con integrante o equipo prestado durante T7 | No; sí impide afirmar reproducción física comprobada mientras falte |
| Calidad y latencia alcanzables | T3 y T8: medir antes de afirmar resultados | No; son criterios de validación, no capacidades ya demostradas |

No quedan decisiones obligatorias de nube, presupuesto, cuentas de plataforma de features o servicios administrados. Elegir remoto Git es opcional. El criterio final es que otra persona pueda partir del código, ejecutar la demo y servir una versión real autorizada siguiendo la documentación, sin usar esta cuenta ni esta computadora; además, el entrenamiento completo debe tener datos, configuración y evidencia trazables.

## Referencias y control del plan

La propuesta académica conserva la motivación y las referencias del proyecto. El [diseño técnico](../specs/2026-10-18-despliegue-local-reproducible-design.md#referencias-técnicas) reúne las fuentes oficiales de Kaggle, MLflow, Airflow, Docker, Colima, PyTorch y uv; T1 verificará las versiones concretas antes de fijarlas. Las instrucciones operativas vigentes estarán en README y `docs/`, sin depender de este chat.

Revisión del plan: datos y tiempo → T2; modelo y escala → T3; publicación y servicio → T4; interfaz/eventos → T5; operación → T6; independencia y recuperación → T7; medición y curso → T8. Los cinco modos de fallo de Review Focus tienen pruebas asignadas. Este documento es planificación: ninguna casilla pendiente representa funcionalidad ya entregada.
