# Calidad temporal, grafos y ranking H&M — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Se conserva la ejecución nativa usada en este proyecto; no volver a solicitar elección de metodología. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluar referencias temporales, un recuperador de grafo y dos ablaciones de ranking aprendido; seleccionar un motor defendible antes de su despliegue reproducible.

**Architecture:** Reutilizar Parquet T2 en lectura, construir estado por corte y producir rankings de hasta 200 artículos con un contrato común. Un evaluador aislado compara contra etiquetas posteriores; los jobs desacoplados publican sólo reportes. Los modelos, grafos y rankings individuales permanecen locales; T4 se adapta al resultado de la campaña.

**Tech Stack:** Docker CPU, Python 3.12, Polars/Parquet, NumPy y los lanzadores/verificadores existentes. Añadir SciPy explícita para grafos dispersos y LightGBM para ranking cuando su tarea verifique compatibilidad y bloquee versiones; sin TensorFlow, Hopsworks, GPU o nube de pago.

**Spec:** [Diseño y protocolo `quality-v1`](../specs/2026-10-20-calidad-temporal-grafos-ranking-design.md). Sus fechas, parámetros, poblaciones, reglas y límites son normativos. Complementa el [plan de plataforma](2026-10-18-plataforma-local-hm.md), insertando Q1–Q4 antes de T4. Estado: **Q1-A/Q1-B implementados, verificados y publicados; cinco reportes mac24 pendientes. Q2–Q4 pendientes**.

## Global Constraints

- Evaluación V1 = [2020-08-26, 2020-09-02), V2 = [2020-09-02, 2020-09-09), V3 = [2020-09-09, 2020-09-16). Características estrictamente anteriores al inicio. Test [2020-09-16, 2020-09-23) reservado.
- Candidatos P/H/G/RH/RG del diseño, misma población/catálogo/denominadores por corte; máximo 200 candidatos y MAP/NDCG@12. Positivos fuera del catálogo permanecen en denominadores. No excluir compras anteriores.
- Sin checkpoints futuros en folds antiguos. Conservar originales, reportes y perfiles T2/T3. No reentrenar dos torres ni modificar `recent-history-v1`.
- mac24: Colima `hm-project` 14 GiB/4 CPU y job `mac24` 10 GiB/3 CPU/2 hilos. Desarrollo: `base` 5 GiB/3 CPU/2 hilos. Un job pesado a la vez; no aumentar VM automáticamente.
- Máximo 2 h por job full, 24 h acumuladas full y 2 h acumuladas de pilotos H&M; disco mínimo 20 GiB al inicio y 5 GiB durante escritura. Son presupuestos, no mediciones de viabilidad.
- Host Python 3.9+ estándar y `SSL_CERT_FILE=/etc/ssl/cert.pem`; bibliotecas científicas dentro de Docker. Dependencias fijadas antes de cada etapa; ARM64 se prueba físicamente, AMD64 no se declara ejecutado por resolver un lock.
- GitHub privado para código/reportes, Kaggle propio para datos. No publicar IDs, predicciones, pesos, grafos o perfiles en los ZIP; no SSH ni monitor nuevo.
- Métricas peores son resultados válidos; fallos operativos se publican y detienen la secuencia. Test no se abre ni se implementa T4 en estas entregas.

## Review Focus

1. Snapshot o caché de un corte futuro reutilizado en uno pasado: rechazar identidad; fixture cambia eventos futuros y exige mismo ranking. Q1-A.
2. Comprador sin historial, catálogo pequeño o positivos no recuperados: recomendaciones únicas con respaldo; mantener compradores/denominadores en evaluación. Q1-A/Q2/Q3.
3. Unión de candidatos o sampling guiados por etiquetas: generación independiente, positivos ausentes no inyectados, evaluación sin muestreo negativo. Q2/Q3.
4. Timeout, OOM o ZIP incompleto marcado como éxito: estado final de fallo, inputs intactos e inventario/contrato estricto por etapa. Q1-B.
5. Medias entre folds con compradores repetidos y recall alterado por un mero reranking: bootstrap agrupado por cliente; paridad de conjuntos y deltas/denominadores. Q3/Q4.

---

## Estructura y contratos entre tareas

Crear módulos pequeños, con una responsabilidad por archivo. Conservar el código histórico como envoltorios cuando se extraiga una función común, y verificar su paridad.

```text
configs/quality/quality-v1.json       # Cortes, métodos, decisiones, recursos y parámetros
src/hm_recommender/quality/
  contracts.py                       # Tipos e identidades
  temporal.py                        # Snapshots y etiquetas separados
  baselines.py                       # P/H con corte explícito
  evaluate.py                        # Métricas individuales locales y agregados
  compare.py                         # Bootstrap por cliente y decisión Q4
  worker.py                          # Etapas; progreso y reporte cerrado
src/hm_recommender/graph/
  build.py                           # Coocurrencia particionada, pesos y poda
  retrieve.py                        # Semillas, vecinos y fusión G
  pagerank.py                        # Control PPR acotado
src/hm_recommender/reranking/
  features.py                        # 20 columnas v1 por corte
  examples.py                        # Grupos, etiquetas y sampling sólo de ajuste
  train.py                           # Early stopping interno y refit
  predict.py                         # Reordenamiento del mismo pool
scripts/quality_job.py               # Host desacoplado y recursos existentes
scripts/quality_handoff.py           # Validador stdlib específico del nuevo reporte
docs/quality-campaign.md             # Guía operativa por entrega, resultados y comandos reales
evidence/quality-*.json              # Resúmenes sanitizados, sólo al recibir resultados
```

Definir en `quality/contracts.py` (Pydantic estricto, campos extra rechazados):

| Tipo | Contrato mínimo |
| --- | --- |
| `QualityConfig` | `protocol='quality-v1'`, folds V1/V2/V3, métodos, ventanas, grafo/PPR/ranker, selección y presupuestos exactos del diseño; huella canónica |
| `FoldSpec` | `fold_id`, `role='evaluation'|'ranker_train'`, `parent_fold`, `cutoff`, `label_end`; siete días exactos. Evaluación sólo V1/V2/V3; entrenamiento sólo las cuatro semanas derivadas del padre según el diseño. Ningún rol de campaña permite etiquetas de test |
| `FoldManifest` | fuente/dataset, cutoff, protocolo, config/hash de código de características, archivos relativos con SHA/tamaño/filas para catálogo y estados previos; jamás etiquetas dentro del estado |
| `RankingArtifact` | fold, method, snapshot/config/hash, archivos Parquet locales ordenados `customer_id, rank, article_id, score, source`; sin duplicados, máximo 200 por comprador |
| `FoldEvaluation` | población/catálogo, métricas/segmentos/exposición y huellas de artefactos de métricas por cliente; sus rutas privadas no se exportan como archivos ZIP |
| `GraphManifest` | identidad de fold, pesos, reglas de poda, conteos de eventos/cestas/nodos/aristas/descartes, inventario CSR/mapa de IDs y estadísticas de construcción |
| `TrainingTable` | cutoff, pool H/G, semanas, esquema, archivos de grupos contiguos/filas y exclusiones; fechas máximas de características/etiquetas |
| `RankerManifest` | fold, pool, hashes de tablas/esquema/lock/config, parámetros, iteraciones elegidas, modelo local, filas/grupos, recursos y pruebas |
| `CampaignDecision` | método elegido, folds/reportes válidos, deltas/intervalos/umbrales y razones de exclusión; `test_evaluated=false`, `final_quality_accepted=false` |

Tipos usan IDs como cadenas con ceros preservados. Artefactos se escriben en directorios nuevos bajo `artifacts/quality/`, con marcador de identidad, archivos temporales y manifiesto final atómico. No sobreescribir un destino de identidad distinta. `DatasetManifest` conserva el contrato existente. Rutas de ejecución del manifiesto no van al control de versiones.

## Q1-A · Cortes explícitos, referencias y evaluación

**Archivos:** crear `quality/{__init__,contracts,temporal,baselines,evaluate}.py`, `configs/quality/quality-v1.json`, `tests/unit/test_quality_temporal.py`, `tests/unit/test_quality_baselines.py`, `tests/integration/test_quality_folds.py`. Modificar `model/recent_history.py` y `model/diagnostics.py` sólo para delegar reglas comunes conservando sus firmas y resultados; reutilizar métricas existentes sin arrastrar Torch a los módulos de reglas.

**Interfaces:**
- `build_snapshot(dataset: DatasetManifest, fold: FoldSpec, output_dir: Path, config: QualityConfig) -> FoldManifest`.
- `read_labels(dataset: DatasetManifest, fold: FoldSpec) -> pl.LazyFrame`, sólo invocada por evaluación o construcción de tablas de entrenamiento, nunca por recuperación.
- `rank_baseline(snapshot: FoldManifest, customers: list[str], method: Literal['P','H'], output_dir: Path) -> RankingArtifact`.
- `evaluate_ranking(ranking: RankingArtifact, labels: pl.LazyFrame, snapshot: FoldManifest, output_dir: Path) -> FoldEvaluation`.

- [x] Escribir pruebas RED `test_future_changes_do_not_change_snapshot_or_ranking`, `test_event_at_cutoff_is_label_only`, `test_snapshot_identity_rejects_other_cutoff`, `test_test_fold_is_rejected`, `test_training_windows_derived_from_parent`. En V3, insertar una compra el 2020-09-09: nunca modifica características; rechazar corte 2020-09-16 en cualquier rol de campaña. Aceptar 2020-07-29 como inicio de entrenamiento de V1, sin tratarlo como cuarto fold externo.
- [x] Ejecutar esas pruebas dentro de la imagen jobs y comprobar que fallan por comportamiento ausente. No instalar librerías científicas en el host.
- [x] Implementar contratos/snapshots por corte; disponer catálogo y perfiles para todos los compradores anteriores, sin usar la población de evaluación para definir estado. Obtener segmentos 28/90/antiguo/nuevo desde pasado, con regla disjunta.
- [x] Escribir RED `test_history_dates_frequency_and_id_ties`, `test_empty_history_matches_popularity`, `test_small_catalog_and_outside_catalog_truths`, `test_legacy_v3_exact_ranking`. Fixture de 24 artículos: recuperar 24 únicos; relevante fuera del catálogo cuenta en recall; empate resuelto por ID; mismo orden que funciones históricas en V3.
- [x] Implementar P/H y evaluación; guardar métricas individuales sólo localmente y agregados transportables. No confundir filas de transacciones, artículos distintos y compradores. Mantener cálculo MAP/NDCG/Recall existente.
- [x] Ejecutar tests nuevos y `tests/integration/test_recent_history.py`, `test_diagnostics.py`, `test_epoch_comparison.py`: todo PASS. Probar que importar `quality.baselines` no importa Torch y que los reportes históricos siguen leyéndose.
- [x] Guardar commit Q1-A. La paridad numérica con full mac24 queda pendiente de Q1-B; un fixture no la acredita.

## Q1-B · Job, intercambio y primera campaña mac24

**Archivos:** crear `quality/worker.py`, `scripts/quality_job.py`, `scripts/quality_handoff.py`, `tests/host/test_quality_handoff.py`, `tests/host/test_quality_job.py`, `tests/integration/test_quality_worker.py`, `docs/quality-campaign.md`. Modificar `scripts/{data_job,handoff,github-handoff,project}.py`, `src/hm_recommender/cli.py`, `docker/jobs.Dockerfile`, `.dockerignore` según el código realmente empaquetado; no cambiar contratos históricos.

**Interfaces:** `run_quality(dataset: DatasetManifest, config: QualityConfig, stage: str, fold: FoldSpec, size: str, run_dir: Path) -> dict`; `validate_quality_result(result: dict) -> None` (stdlib, error ante reporte inválido). Host futuro `project.py quality-job start/status/export`; `start` requiere `--stage baselines|graph|ranker`, `--fold V1|V2|V3`, `--size demo|pilot|full`, `--config quality-v1`, `--expected-commit SHA`, `--machine-id`, `--profile`, `--resources`; `--pool H|G` obligatorio sólo en etapa ranker. Antes de Q2/Q3 rechazar explícitamente sus etapas no implementadas.

- [x] Escribir RED de reporte aprobado mutilado (fold faltante, métricas no finitas, test verdadero, población distinta por método, límites ausentes, fecha futura en features, archivo de clientes añadido) y de timeout/disco/worker muerto. Rechazar con salida 2 archivos inválidos; aceptar con salida 1 un reporte válido de fallo.
- [x] Implementar lanzador con controles reutilizados de commit, imagen, exclusión de trabajos pesados, memoria/cgroup, red y montajes. Inputs sólo lectura; salida propia RW. Timeout mata el trabajo y conserva fallo; `status` detecta contenedor muerto aun sin cierre del worker. No añadir resume en v1: un reintento usa nuevo run_id y no reutiliza parciales no verificados.
- [x] Implementar `quality-handoff`, run_id `quality-...`, marcador GitHub propio y lista blanca de archivos de reporte/log/config/recursos; validar por etapa/método. Mantener ≤50 MiB e integridad del transporte existente. Copiar el validador auxiliar a la imagen si las pruebas lo importan; verificar sin montajes de código que oculten omisiones.
- [x] Implementar presupuesto acumulado con identificador de campaña y registro local de intentos, incluidos fallidos. No permitir reiniciar el contador con un nuevo run_id. Reservar 30 minutos de las 24 h para Q4 (Q1–Q3 hasta 23.5 h). Incluir horas acumuladas y espacio libre en los reportes; preservar error si no se puede validar el registro.
- [x] Añadir `verify --suite quality` (unitarias, integración de la etapa y host). Ejecutar `SSL_CERT_FILE=/etc/ssl/cert.pem python3 -m unittest discover -s tests/host -v`; reconstruir imagen y ejecutar suite quality, diagnóstico histórico y `verify --suite bootstrap` con `DOCKER_CONTEXT=colima`. Esperado: salida 0; no declarar mac24 probada aquí.
- [x] Ejecutar demo oficial desacoplada aquí; exportar/validar/publicar incluso el intento fallido. Ejecutar piloto real local sólo si necesario para verificar IO; no hacer full ni nuevos entrenamientos sin el código de sus etapas.
- [x] Publicar commit probado y guía Q1. Preparar prompt mac24 con secuencia fija **demo → piloto H&M V3 → full V1 → full V2 → full V3**, recursos actuales y hashes de originales antes/después. La demo genera fechas sintéticas que cubren las cuatro semanas de ajuste y folds; sus métricas no acreditan H&M. Los comandos exactos van en la guía sólo al estar implementados.
- [ ] Recibir y validar los cinco ZIP, comparar V3 H/P contra ZIP original completo y registrar `evidence/quality-q1-mac24.json`. Cerrar Q1 si todos son válidos; pérdida de calidad entre semanas se documenta sin modificar el protocolo. Siguiente entrega es Q2, no T4.

## Q2 · Grafo a escala completa y PageRank académico

**Archivos:** crear `graph/{__init__,build,retrieve,pagerank}.py`, `tests/unit/test_graph_weights.py`, `tests/unit/test_graph_retrieval.py`, `tests/unit/test_personalized_pagerank.py`, `tests/integration/test_graph_quality.py`. Modificar `quality/worker.py`, `scripts/quality_handoff.py`, `pyproject.toml`, `uv.lock`, guía y pruebas de host. SciPy será dependencia explícita fijada, no una dependencia transitiva asumida; verificar construcción de imágenes.

**Interfaces:** `build_graph(dataset: DatasetManifest, snapshot: FoldManifest, config: QualityConfig, output_dir: Path) -> GraphManifest`; `rank_graph(snapshot: FoldManifest, graph: GraphManifest, customers: list[str], output_dir: Path) -> RankingArtifact`; `pagerank_control(snapshot: FoldManifest, graph: GraphManifest, customers: list[str], output_dir: Path, config: QualityConfig) -> dict` (sólo agregados exportables; rankings locales separados).

- [ ] Escribir RED de pesos calculados a mano en cestas pequeñas, deduplicación, ausencia de self-loops, cesta de 51 artículos limitada a 50, máximo 50 aristas por origen y conservación de nodos aislados. `test_graph_ignores_future_baskets` debe producir idénticos datos CSR al añadir eventos posteriores.
- [ ] Implementar agregación por particiones, apoyo/coocurrencia y normalización del diseño; escribir IDs/CSR ordenados con conteos y hashes. Medir espacio temporal además de tamaño final; nunca `toarray()` sobre el grafo completo.
- [ ] Escribir RED `test_graph_fallback_equals_H`, `test_old_history_can_supply_seeds`, `test_candidate_union_is_label_independent` y `test_graph_prefix_deduplicates_and_caps_200`. Usuario sin historia90 tiene G=H; historia de 29–90d puede aportar vecinos; variar etiquetas no altera candidatos.
- [ ] Implementar semillas, vecinos y fusión fija G; reportar composición del pool y aciertos exclusivos de cada fuente, sin atribuirlos causalmente. Ejecutar full sobre todos los compradores, sin ampliar k para favorecer al grafo.
- [ ] Escribir RED de PageRank contra solución lineal en fixture de 4 nodos (`atol=1e-6`), dangling, residuo global, truncación 512 y fallback declarado si no converge. Probar permutaciones sin propietarios fijos y exclusión explícita de usuarios sin semillas del contraste personalizado.
- [ ] Implementar PPR/control de hasta 5,000 compradores y diez permutaciones; comparar H/G/PPR en esa cohorte. Registrar scope `sample`, sin permitir promoción ni mezcla con métricas full.
- [ ] Ejecutar suite quality y host, bootstrap si cambia imagen/dependencias, demo oficial; validar reportes y publicar código. Prompt mac24 Q2: demo → piloto V3 → full V1/V2/V3 (cada full incluye G y control acotado; referencias Q1 verificadas). Los resultados peores siguen la secuencia; fallo operativo la detiene.
- [ ] Recibir reportes, registrar `evidence/quality-q2-mac24.json`, verificar insumos/candidatos/cohortes/recursos. Cerrar Q2 operativa aunque G no supere H; no elegir modelo con la muestra PPR.

## Q3 · Ordenamiento aprendido y ablación de recuperación

**Archivos:** crear `reranking/{__init__,features,examples,train,predict}.py`, `tests/unit/test_ranker_temporal_features.py`, `tests/unit/test_ranker_groups.py`, `tests/integration/test_ranker_quality.py`. Modificar contratos/worker/validador/guía, `pyproject.toml`, `uv.lock` y Docker sólo si la instalación lo exige.

**Interfaces:** `build_ranking_examples(dataset: DatasetManifest, fold: FoldSpec, pool: Literal['H','G'], config: QualityConfig, output_dir: Path) -> TrainingTable`; `fit_ranker(table: TrainingTable, config: QualityConfig, output_dir: Path) -> RankerManifest`; `rerank(model: RankerManifest, candidates: RankingArtifact, snapshot: FoldManifest, graph: GraphManifest, output_dir: Path) -> RankingArtifact`.

- [ ] Instalar una versión LightGBM CPU compatible con Python/NumPy actuales en imagen Linux ARM64; fijar versión y dependencias en lock antes del piloto. Verificar resolución AMD64 por separado. Si necesita OpenMP, fijar/registrar dependencia del sistema; no cambiar locks históricos ni instalar en host. Verificación de importación/ajuste pequeño/save-load precede a datos reales.
- [ ] Escribir RED de esquema exacto 20 columnas, cutoff semanal, posición ausente=201, recencia ausente=91, afinidad sin historia=0 y `test_future_label_never_enters_features`. Cada grupo tiene una semana y usuario; suma de tamaños de grupos igual a filas.
- [ ] Implementar características y candidatos por cada inicio semanal; reutilizar Q1/Q2 bajo identidad de caché fuente/corte/protocolo, nunca el grafo del fold externo para semanas antiguas. Los cuatro grafos/snapshots internos pueden compartirse entre RH/RG con hashes verificados.
- [ ] Escribir RED `test_missing_positive_not_injected`, `test_training_sampling_is_deterministic`, `test_zero_positive_groups_counted_not_evaluated_away`. Ajuste conserva todos los positivos recuperados y hasta 20 negativos; evaluación interna/externa conserva pools completos y todos los compradores. En piloto limitar por hash hasta 2,000 grupos por semana, dejando constancia; full no muestrea grupos.
- [ ] Implementar tablas, sampling y contigüidad de grupos; registrar transacciones, semanas, compradores, grupos/filas antes/después de filtros, positivos fuera del pool y uso máximo de disco/RAM. Un binario LightGBM no sustituye esas conciliaciones.
- [ ] Escribir RED `test_outer_fold_not_used_for_early_stopping`, `test_inner_ndcg_keeps_missing_relevant_denominator`, `test_refit_uses_four_weeks_and_selected_iterations`, `test_reranking_preserves_candidate_set_and_recall` y save/load con orden igual en fixture. Un relevante recuperado de cuatro no obtiene NDCG=1; un grupo sin aciertos vale cero y permanece en la media. Probar empates por ID y schema incompatible rechazado.
- [ ] Implementar ajuste interno con tres semanas, early stopping en cuarta y refit con cuatro según parámetros del diseño; `metric='None'` y evaluador personalizado conservan la definición de NDCG del proyecto. Si no hay grupos entrenables, producir fallo explícito; la demo debe incluir suficientes grupos para ejercer el contrato, sin alterar hiperparámetros del protocolo H&M.
- [ ] Ejecutar nuevas pruebas, suite quality/host, bootstrap tras cambio de imagen y demo real sin montajes auxiliares; publicar commit. Prompt mac24 Q3: demo y piloto de H, luego demo y piloto de G; full RH V1 → V2 → V3, luego RG V1 → V2 → V3. Comprobar presupuesto antes de cada uno y conservar todos los reportes.
- [ ] Recibir/validar reportes y registrar `evidence/quality-q3-mac24.json`. Comparar RH con H, RG con G y RG con RH para explicar ordenamiento y recuperación; selección formal sólo en Q4 contra H y con full de tres folds.

## Q4 · Selección, explicación y enlace con despliegue

**Archivos:** crear `quality/compare.py`, `tests/unit/test_quality_selection.py`, `tests/integration/test_quality_campaign.py`, `docs/course-coverage.md`. Modificar `docs/{model-selection,evaluation,quality-campaign}.md`, README/AGENTS, plan principal y `quality/worker.py`/lanzador/validador para una acción `summarize` de sólo lectura. Crear evidencia de decisión después de recibir los resultados.

**Interfaces:** `compare_campaign(evaluations: list[FoldEvaluation], config: QualityConfig, output_dir: Path) -> CampaignDecision`. Requiere artefactos locales por comprador para bootstrap; no es posible reconstruirlo desde medias de ZIP. Se ejecuta en mac24; la Mac principal recibe y audita agregados/identidades, sin pretender recalcular intervalos sin esos datos.

- [ ] Escribir RED de bootstrap que conserva juntos registros del mismo comprador en distintos folds, pesos normalizados por fold, media de semanas sin ponderar por tamaño, y tres contrastes con nivel `1-0.05/3`. Comparar un fixture con cálculo independiente y semillas fijas.
- [ ] Escribir RED `test_incomplete_candidate_never_promoted`, `test_threshold_boundaries_and_zero_reference`, `test_segment_regression_blocks_promotion`, `test_tie_break_uses_cost_not_test`, `test_no_candidate_keeps_H`. Probar cada umbral del diseño (2%, 1%, 5%, 1,000 usuarios, diferencia 0.0001) y no leer test.
- [ ] Implementar comparación/decisión y reportes completos, separando ejecución, elegibilidad y aceptación final. Preservar métricas locales por comprador/fold/método en Q1–Q3 y comprobar sus huellas; Q4 dispone de 30 minutos incluidos en 24 h acumuladas. Si una etapa no pudo ejecutarse, cerrar investigación con limitación explícita, sin llamarla comparación completa. Una corrección de bug invalida resultados afectados y requiere repetirlos con identidad nueva, sin borrar evidencia.
- [ ] Publicar código probado y ejecutar resumen mac24 sobre artefactos cerrados; exportar/validar/publicar `quality-handoff` de etapa `summary`, con huellas de todos los reportes fuente. Recibir `evidence/quality-decision-mac24.json`; revisar coherencia de deltas, intervalos, poblaciones, costes y motivos de selección.
- [ ] Redactar explicación con ablaciones, segmentos, incertidumbre y casos negativos; crear mapa de cobertura implementada frente a pendiente. No atribuir resultado al framework, no llamar causal a una permutación ni red social a coocurrencia comercial.
- [ ] Congelar ganador, configuración y procedimiento de preparación/refit. Actualizar contratos de T4-A/T4-B y actualización T5 según artefactos necesarios; conservar publicación/rollback/portabilidad. Si gana H, reutilizar el diseño ya preparado. Finalizar Q4 y preparar el siguiente desarrollo T4-A.
- [ ] Planificar en T8 el test único del procedimiento congelado y P/H; no ejecutarlo como parte del resumen ni cambiar el ganador a partir de test. Mantener objetivos de servicio: 20 solicitudes/s, p95 < 200 ms, errores < 1%, medidos después de implementar API.

## Cómo continuar desde este plan

1. **Aquí:** implementar Q1-A y Q1-B, validar imagen/host/demo, publicar un commit ejecutable y su guía. El primer prompt remoto evaluará P/H; no hará grafos ni entrenará.
2. **En mac24, cuando llegue ese commit:** seguir la secuencia Q1-B; exportar y publicar cada reporte antes de continuar. Mantener equipo sin suspensión y recursos actuales. GitHub no despierta al otro agente: el usuario avisa al terminar.
3. **Aquí:** descargar y auditar Q1; preparar Q2 y después Q3, cada una con su propio commit y prompt. Q4 se calcula donde estén los artefactos individuales y devuelve sólo agregados.
4. **Después de selección:** completar T4–T8. Un modelo más complejo sólo llega al servicio si cumple la regla; los experimentos del curso se entregan aunque pierdan.

No hay comando nuevo para ejecutar hoy en mac24. No lanzar órdenes de diagnóstico histórico para simular Q1. La publicación de este documento sincroniza el plan, no acredita implementación.

## Presupuesto de trabajo y dependencias

Estimación de ingeniería, no compromiso de calendario: Q1 1–2 jornadas; Q2 2–3; Q3 2–3; Q4 1. Total 6–9 jornadas de desarrollo/revisión, más ejecución medida y disponibilidad para intercambiar reportes. T4–T8 conservan su trabajo pendiente y se reestiman al cerrar Q4; no prometer mantener el calendario original sin absorber esta ampliación.

No hacen falta nuevas cuentas, servicios de pago o dataset. Pendientes técnicos: compatibilidad SciPy/LightGBM en la imagen, espacio local medido, coste del grafo y de tablas por semana, y permanencia de artefactos locales en mac24 para el resumen. Se verifican en sus tareas antes de full; son incertidumbres de implementación, no requisitos para contratar infraestructura.

Auto-revisión prevista: cubrir cada requisito del diseño con una tarea/prueba, conservar tipos entre tareas, revisar referencias de fechas y estado de comandos, y verificar que límites/selección no puedan cambiarse después de medir sin nuevo protocolo. Los números de pruebas y resultados se escribirán únicamente después de ejecutar.
