# Proyecto final de Datos Masivos

**Siguiente paso T8-B: inventario de capacidad, sin carga.** Ejecutar `scripts/mac24_t8_capacity.py --expected-commit SHA_COMPLETO` siguiendo [la guía](docs/mac24-t8-capacity.md). Cuenta todos los proyectos activos, valida T8-A y publica un reporte capacity-v1 agregado. Evalúa un candidato aislado de 4 GiB/1 CPU/2 hilos más margen VM de 1 GiB; no lo inicia. No detener servicios, aumentar recursos, repetir baseline/alineación ni abrir test. `passed` acredita inventario, no concurrencia. Revisar reporte antes de decidir ensayo/política secuencial; T8-B sigue abierto. Prevalece sobre los comandos históricos siguientes.

**T8-A cerrado tras revisión:** `benchmark-mac24-20261023T213836Z-a7a170b5` aceptado bajo load-baseline-v1; ver [auditoría](evidence/benchmark-review-mac24.json). Medición: 12,000/12,000 respuestas correctas, cero errores/omisiones, p95 14.099 ms, p99 15.417 ms, 20 req/s durante 600 s. 222 capturas, sin swap/nuevos OOM ni cambios de generación. PostgreSQL conserva identidad T7 y registra dos eventos max nuevos; no ocultarlos ni interpretar picos históricos como picos exclusivos de carga. No repetir baseline ni alineación API. Próximo desarrollo T8-B: concurrencia acotada con preparación de estado H; requiere protocolo propio antes de ejecución. T8-C/test y T8-D siguen pendientes. Esta sección prevalece sobre comandos históricos siguientes.

**Paso vigente: alinear código API antes de T8.** mac24 confirmó HTTP200 con huella T6 `5e0ba69395ee17158d4b823d63776dadc072a5559204b3101df00b348765d442` y checkout T7 `b1f69269be9960d1df0f2693e0f13b53ce0cbaabcd98ea1f6ad68703f5a3a7b3`. Sin intento de carga reservado. Ejecutar una vez `scripts/mac24_t8_align_api.py --expected-commit SHA_COMPLETO` y después únicamente preflight. Este paso separado permite recrear sólo API usando la imagen T7 ya auditada; no construir, migrar, activar H, tocar PostgreSQL/UI ni iniciar carga. Conserva identidad/cgroup/métricas anteriores, estado agregado y reporte privado en `.runtime/t8-api-alignment`; ante fallo conservar todo y no repetir. Es una nueva generación API por actualización de código, no un reinicio para ocultar memoria. Ver [protocolo](docs/mac24-t8-baseline.md). Prevalece sobre instrucciones históricas de no reiniciar API y de ejecutar carga.

**Paso vigente T8-A:** generador baseline y [protocolo mac24](docs/mac24-t8-baseline.md) preparados. Ejecutar una sola vez `scripts/mac24_t8_baseline.py --expected-commit SHA_COMPLETO` cuando se entregue el SHA, con AC, tapa abierta y clon limpio.20req/s,120s de calentamiento+600s de medición; p95<200ms y errores+omisiones<1%. No reiniciar ni activar servicios, no entrenar ni evaluar test. Conservar originales y reserva incluso ante fallo; revisar el ZIP antes de T8-B/C. Cohortes/respuestas/logs quedan privados; sólo agregados y contadores viajan por GitHub. No crear otra cuenta. Esta sección prevalece sobre procedimientos históricos.

**T7/E6 cerradas (23 de octubre):** recuperación H&M auditada y aceptada. Por decisión explícita del usuario se omite la prueba con otra cuenta de macOS; queda como validación opcional no ejecutada, no como requisito pendiente. No crear cuentas, perfiles ni ejecutar `os_user_check.py`. Se conservan scripts y evidencia histórica. Sigue T8 (rendimiento, evaluación final y cobertura académica), sin iniciarlo automáticamente por este cambio de alcance. Este estado prevalece sobre las instrucciones históricas siguientes.

**Revisión T7 mac24:** recuperación `portability-mac24-20261023T204855Z-579f1842` aceptada bajo portability-v1; ver [auditoría](evidence/portability-review-mac24.json). Duración 96.531 s, respaldo privado 918,506,419 bytes, seis consultas de paridad y seis sin red externa. Se verificaron ZIP, código/runner y estado/identidad original reportados. Conservación de 3,410 archivos y suite son declaraciones remotas separadas. T7/E6 cerradas bajo el alcance acordado: otra cuenta de SO no fue probada y el usuario retiró ese requisito. No repetir el ensayo ni borrar reservas/destinos. No iniciar T8/test. Este estado prevalece sobre los prompts históricos siguientes.

**T7 implementada, pendiente de ensayo H&M:** exportación/importación de paquetes, respaldo lógico, restauración en instalación nueva y paridad offline. [Guía reproducible](docs/reproducibility.md) y [procedimiento mac24](docs/mac24-t7-prompt.md). No repetir T6; T8/test siguen reservados.

**Seguridad de recuperación:** se rechazan rutas/enlaces e inventarios inválidos, se verifican huellas y código instalado y se generan credenciales nuevas. Los respaldos con perfiles permanecen privados; GitHub recibe sólo reportes agregados.

**Seguimiento T7 mac24:** el intento `portability-mac24-20261023T203910Z-ed135689` falló al invocar la pausa de Airflow, antes de completar el respaldo. La llamada directa omitía el entrypoint que configura PostgreSQL y autenticación. Se corrigió para usar el mismo lanzador de T6, sin poner secretos en argumentos ni logs. La corrección tiene prueba host; falta comprobación remota. Conservar el intento y su reserva: no repetir T7 automáticamente ni iniciar T8.

El diagnóstico posterior de mac24 confirmó conexión y ambos DAG pausados. El [seguimiento único](docs/mac24-t7-airflow-followup.md) usa `--airflow-followup`, valida el SHA-256 del antecedente y conserva archivos/reservas/evidencia originales. El nuevo ensayo mantiene los criterios de T7 y aún requiere ejecución y revisión principal.

**T6/E5 cerradas tras revisión:** [auditoría mac24](evidence/pipeline-review-mac24.json), protocolo `pipeline-v2`. Dos ciclos programados y recuperación del scheduler; frescura 30.465/276.191 s (<600 s), duración 327.475 s y 24 capturas sin swap/OOM capturados. H vigente se conserva. PostgreSQL registró 73,327 eventos `max` nuevos y pico 8 KiB superior a su límite: no acredita margen de memoria ni carga sostenida. [Operación y límites](docs/operations.md). El prompt T6 es histórico; no repetirlo. Siguen T7 (portabilidad/respaldo/restauración) y T8 (carga, test reservado e informe), sin iniciarlos automáticamente.

Las notas anteriores de implementación conservadas abajo son históricas; prevalece este estado de cierre.

**T5/E4 cerradas:** [auditoría operativa H&M](evidence/events-operational-review-mac24.json), corrida `events-mac24-20261023T030009Z-3f97f2a0`. Duró 24 min 42 s, con controles funcionales/persistencia correctos y relojes coherentes; cero swap/OOM capturados. Conserva pico/presión históricos PostgreSQL: no acredita margen de memoria ni carga T8. [Detalle](docs/events-operational.md#revisión-principal-de-mac24). No repetir el adaptador ni los prompts T5. Sigue T6 cuando se solicite continuar; T7/T8 y test pendientes. Las notas siguientes describen entregas históricas y no reabren T5.

**Entrega previa T5:** [interfaz Streamlit, eventos idempotentes y perfiles operativos](docs/events.md). Ejecuta `python3 scripts/project.py demo` desde un clon limpio con Docker preparado para generar sintéticos, abrir el servicio local y producir un reporte; `verify --suite events` prueba el flujo. El [diagnóstico H&M ya fue auditado](evidence/events-memory-review-mac24.json): doce capturas completas, controles funcionales y persistencia correctos, cero swap/OOM. Conserva **failed** por el pico histórico PostgreSQL (512.258 MiB frente a 512 MiB), presente antes de semilla y sin crecimiento. Hubo actividad nueva junto al límite. **T5/E4 siguen abiertas**; además, las marcas UTC abarcan 4 h 22 min 48 s frente a 26.55 min del reloj monotónico. Esa discrepancia impide comparar rendimiento. Sigue corregir la medición temporal y definir una aceptación operativa versionada, según [la revisión](docs/events-memory.md#revisión-en-la-mac-principal-2026-10-22). Ambos prompts T5 son históricos y no se repiten. Q1–Q4 y T4-A/T4-B siguen cerradas; T6–T8 y test pendientes. Las secciones históricas siguientes conservan el alcance de cada entrega.

Plataforma local de recomendaciones para comercio electrónico con H&M y Docker Compose, con selección del motor por calidad medida. La ejecución utiliza recursos propios, sin nube ni servicios de pago. PyTorch se conserva como experimento académico reproducible.

**Seguimiento de relojes revisado:** el [reporte mac24](evidence/events-clock-review-mac24.json) es un fallo válido por captura incompleta: alcanzó 16 MiB y no cubrió la ventana del ensayo. La causa de la discrepancia temporal sigue desconocida; cero eventos exportados no prueba ausencia de reposo. El [prompt de recolección](docs/mac24-t5-clock-prompt.md) es histórico y no se repite. Sigue integrar y comprobar los controles temporales y el contrato operativo propuesto en [la guía](docs/clock-diagnostics.md), antes de preparar otro ensayo H&M. T5/E4 siguen abiertas; todavía no hay ejecución mac24 pendiente.

Repositorio privado: [emilianodesu/clothing-recommendation](https://github.com/emilianodesu/clothing-recommendation). Cada equipo necesita autenticación Git con acceso al repositorio; sus credenciales de ejecución se generan localmente mediante bootstrap.

**Implementación iniciada:** repositorio Git local, paquete Python, dependencias bloqueadas, imágenes de aplicación/trabajos y bootstrap de PostgreSQL y Qdrant. T3 incorpora el entrenamiento de dos torres PyTorch, checkpoints, evaluación exacta y MLflow local. T4-A añade paquetes H schema2 y paridad offline, reproducidos y revisados en mac24. T4-B implementa API, activación y rollback con PostgreSQL; la reproducción H&M v2, incluyendo acceso desde localhost, ya pasó en mac24 y está cerrada tras revisión. T5 añade Streamlit y eventos; su flujo H&M está comprobado, pero memoria y medición temporal mantienen abierto el cierre. Los DAG T6 siguen pendientes. T2 ya incluye descarga verificable, ingestión completa a Parquet, perfiles temporales y trabajos desacoplados con reportes por GitHub. Consulta [datos y ejecución T2](docs/data.md).

**Decisión vigente (20 de octubre):** el usuario aprobó ampliar la validación antes de cerrar el motor. Q4 cerró la selección: historial28d+popularidad7d (H) es el motor elegido bajo los criterios predefinidos; test y aceptación final siguen pendientes. El [plan Q1–Q4](docs/superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md) añade tres cortes temporales, recuperación por grafo y ordenamiento LightGBM, con presupuesto y criterios de selección fijados antes de medir. [Selección y explicación de resultados](docs/model-selection.md) distingue evidencia, hipótesis y límites. Q1 ya dispone de evaluador y `quality-job`; sigue la [guía de campaña](docs/quality-campaign.md). Los cinco reportes de mac24 ya se descargaron y [revisaron](evidence/quality-q1-review.json): Q1 está cerrada. T4-A y T4-B también están cerradas; siguen interfaz/eventos, operación, recuperación y aceptación final T5–T8.

## Campaña de calidad temporal, grafos y ranking

Secuencia: Q1 referencias temporales → Q2 grafo y control PageRank → Q3 ordenamiento aprendido → Q4 selección → T4–T8 despliegue y aceptación. El [diseño](docs/superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md) fija fechas, candidatos, límites, cobertura del curso y cierre de experimentos. Los experimentos PyTorch existentes se conservan sin repetirlos. PageRank se estudia en una cohorte acotada; no se promueve un motor por resultados de esa muestra.

Q1, Q2 y Q3 están cerradas operativamente: se descargaron y verificaron cinco, cinco y diez reportes mac24 respectivamente. H supera a P en las tres semanas. La [revisión Q2](evidence/quality-q2-review.json) confirma que G mejora el MAP medio +6.47%, pero pierde Recall@200 −4.39%. La [revisión Q3](evidence/quality-q3-review.json) confirma que RH mejora MAP medio +4.03% conservando Recall, pero incumple el límite de pérdida por segmento; RG mejora MAP +14.48%, pero incumple Recall y segmentos. **Q4 está cerrada y H queda seleccionado** tras [auditar el reporte y sus doce fuentes](evidence/quality-decision-mac24.json). Los intervalos de mejora de MAP son positivos para G/RH/RG, pero sus pérdidas de Recall o segmentos impiden promoverlos. **T4-A también está cerrada** tras [revisar demo y H&M en mac24](evidence/releases-review-mac24.json): el [paquete portable H](docs/releases.md) conserva top200 y métricas de Q1-V3. La [revisión T4-B v2](evidence/serving-host-review-mac24.json) valida el ensayo H&M con acceso desde el host, reinicio y rollback: **T4-B cerrada**. Sus prompts v1/v2 son históricos; el siguiente desarrollo del plan es T5, sin ejecutar trabajos automáticamente al recibir evidencia. La evidencia local y remota está en [evidence/releases.json](evidence/releases.json); el prompt T4-A es histórico. La [guía Q4](docs/quality-summary.md) conserva resultados y reproducción; su prompt es histórico. No repetir las campañas ni ajustar umbrales. La [guía Q3](docs/ranker-quality.md) conserva resultados, recursos y reproducción; su prompt es histórico. La [verificación local Q3](evidence/local-quality-q3.json) acredita 145 pruebas distintas y dos demos. La [verificación local Q4](evidence/local-quality-q4.json) acredita 155 pruebas distintas y una demo de resumen, además del intento de preparación fallido conservado. El intercambio continúa por GitHub privado y reportes cerrados; los artefactos individuales y checkpoints se conservan en mac24 como evidencia.

## Preparar el paquete H (T4-A)

`release-job start/status/export` construye una versión local de historial28d+popularidad7d, verifica integridad y compara top200/métricas con Q1-V3. No entrena ni activa servicio. Las corridas demo → H&M en mac24 ya fueron [revisadas](evidence/releases-review-mac24.json). Full produjo un paquete de 56.49 MiB con 1,351,314 compradores históricos, 949,748 pares de historial y 102,967 artículos; construcción y paridad tardaron 29.048 s, con pico cgroup de 2.248 GiB, sin swap/OOM. Conservó las recomendaciones y métricas para 72,019 compradores V3. [docs/releases.md](docs/releases.md) conserva el procedimiento de reproducción. API/activación y acceso desde el host también se comprobaron en mac24 y están cerrados tras revisión. Estas cifras offline no son latencia de servicio.

## Servir recomendaciones (T4-B)

T4-B está **cerrada tras [auditar el seguimiento v2 de mac24](evidence/serving-host-review-mac24.json)**: 877 recomendaciones internas y diez desde `127.0.0.1:8000`, cinco etapas con identidad y paridad correctas, reinicio y rollback. Duró 20.607 s, con pico API de 1.015 GiB y sin swap/OOM. La [corrección local](evidence/serving-host.json) pasó 216 pruebas distintas y el ensayo sintético; mac24 declara 175 pruebas y preservación de 3,072 archivos, separadas del ZIP auditado. [docs/serving.md](docs/serving.md) conserva el contrato y los procedimientos históricos; no repetir sus prompts. T4-B dejó H&M activo. T5 amplía el perfil con Streamlit, eventos idempotentes y actualización de perfiles; su reproducción H&M fue revisada y sigue abierta por los hallazgos descritos al inicio. Test y carga sostenida siguen reservados para T8.

## Empezar en la segunda Mac

Después de clonar en una Mac Apple Silicon con Git, Homebrew, Python 3.9+ y Ruby, lee [las instrucciones del agente](AGENTS.md) y ejecuta desde la raíz:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/start-second-mac.py --machine-id mac24
```

Prepara Docker/Colima, instala la infraestructura, ejecuta las pruebas y genera un ZIP incluso ante fallos normales. Con GitHub CLI autenticado, publícalo con `python3 scripts/github-handoff.py upload RUTA-DEL-ZIP`. En la Mac de desarrollo, `python3 scripts/github-handoff.py list` muestra reportes y `python3 scripts/github-handoff.py download RUN_ID` descarga y valida el elegido. La [guía entre equipos](docs/second-mac.md) explica la autenticación y los comandos. Esta primera tarea no necesita el dataset ni inicia entrenamiento.

T1 ya pasó en la Mac física de 24 GB (35 pruebas). Para continuar allí con los datos, sigue [T2 en mac24](docs/second-mac.md#t2-preparar-los-datos-en-mac24). El agente debe fijar el commit, preparar Colima con 14 GiB, descargar H&M de Kaggle, ejecutar una corrida sintética y otra completa con `data-job start --resources mac24` (10 GiB para el job), y publicar ambos reportes. `--resources base` conserva el presupuesto original de 5 GiB. Los [perfiles y límites efectivos](docs/data.md#salidas-y-recursos) quedan registrados y se conservan al reanudar. T3 ya completó demo, dos pilotos y una época full de 22,674,665 ejemplos en mac24; la [evidencia T3](evidence/mac24-t3-2026-10-19.json) registra recursos, métricas y Releases. Full supera la popularidad histórica en validación, pero queda por debajo de popularidad de siete días en el diagnóstico posterior; su calidad sigue pendiente de revisión. **T2 ya pasó en mac24**: corridas sintética y H&M completo, con límites efectivos de 10 GiB/3 CPU/2 hilos. La [evidencia de mac24](evidence/mac24-t2-2026-10-19.json) contiene conteos, memoria, duración y enlaces a ambos reportes privados.

## Entrenar (T3)

Sigue [T3 en la segunda Mac](docs/second-mac.md#t3-entrenar-y-devolver-evidencia-desde-mac24): verifica MLflow, ejecuta demo → hm-small → hm-medium → hm-full secuencialmente y publica cada reporte. El [contrato de evaluación](docs/evaluation.md) describe métricas, cortes, recursos y recuperación. `train-job start/status/stop/resume/export` reutiliza el transporte privado de T1/T2. No necesita TensorFlow, Hopsworks, nube ni librerías científicas en el host. El despliegue de la API y el transporte de pesos siguen siendo T4.

## Diagnosticar el modelo existente

El diagnóstico de demo y full en mac24 ya pasó; la [evidencia](evidence/mac24-t3-diagnostics-2026-10-19.json) muestra que popularidad de 7 días supera al modelo y confirma concentración alta. La calidad permanece pendiente de revisión. Para [diagnosticar concentración y personalización](docs/diagnostics.md) se usa `diagnose-job`: reproduce la evaluación del checkpoint full, compara popularidad de 7/28 días y mide cambios al modificar las entradas del usuario. Reutiliza pesos en lectura y devuelve agregados por GitHub. No entrena ni evalúa test; la aceptación de calidad sigue pendiente.

## Experimento de tres épocas

El [procedimiento de tres épocas](docs/three-epochs.md) ya produjo cuatro reportes aprobados en mac24, descargados y verificados en la máquina principal. [Resultados](evidence/mac24-three-epochs-2026-10-20.json): MAP/NDCG empeoraron frente a una época, aunque Recall y cobertura mejoraron; popularidad7d supera ambos modelos. La calidad del experimento no se aceptó. Test sigue reservado; el motor H posterior ya tiene paquete T4-A y servicio T4-B verificados, incluido el acceso desde el host.

## Historial reciente: resultados revisados

La [validación de compras recientes](docs/recent-history.md) compara un prefijo personal de artículos comprados en 28 días, completado con popularidad7d o con el modelo de una época. Usa los mismos compradores, bootstrap pareado y diez permutaciones del historial. Los dos reportes de mac24, demo y full, ya se descargaron y revisaron. Historial+popularidad cumple el criterio principal de validación; añadir el modelo no demuestra mejora de MAP y reduce NDCG/Recall. La [revisión](evidence/mac24-recent-history-review-2026-10-20.json) sustenta la referencia provisional de la nueva campaña Q1–Q4. Q4 confirmó posteriormente H; T4-A y T4-B ya cerraron paquete, API y acceso desde el host. Este candidato no acredita superioridad del modelo PyTorch. Test y aceptación final siguen pendientes.

La [verificación local](evidence/local-recent-history.json) pasó 96 pruebas y dos diagnósticos, demo y piloto H&M; conserva también un intento fallido de empaquetado y su corrección. La [ejecución full en mac24](evidence/mac24-recent-history-2026-10-20.json) completó 72,019 compradores y 102,967 artículos en 132.132 s, con pico de 2.568 GiB, sin swap ni OOM. Es tiempo del diagnóstico offline, no latencia del servicio.

## Instalar la infraestructura del proyecto

Requisitos del host: Docker con Compose y Buildx, Python 3.9+ y Git si se obtiene el código por clonación. No se necesita instalar PyTorch, uv ni bibliotecas científicas en el host. La primera construcción descarga imágenes y paquetes.

Desde la carpeta del proyecto, después de preparar Docker según la sección siguiente:

```bash
python3 scripts/project.py bootstrap
python3 scripts/project.py check
python3 scripts/project.py verify --suite bootstrap
python3 scripts/doctor.py --application
```

`bootstrap` genera `.env` privado con credenciales propias, construye las imágenes, inicia PostgreSQL y Qdrant, aplica migraciones y verifica importaciones y conexiones. Puede repetirse: conserva contraseñas, datos y volúmenes. No hay que copiar `.env.example`; es la referencia de variables. La prueba `verify` crea una instalación temporal en otra ruta con espacios, comprueba persistencia y elimina sólo sus propios recursos.

```bash
python3 scripts/project.py status
python3 scripts/project.py down
```

`down` conserva los datos. `bootstrap --skip-build` permite volver a arrancar usando imágenes ya construidas; al cambiar código o locks, ejecutar bootstrap sin esa opción. Los scripts también funcionan invocados por ruta absoluta desde otro directorio.

La [guía de la primera entrega](docs/bootstrap.md) explica credenciales, red, versiones, certificados, mantenimiento de locks y límites de lo comprobado. El perfil `serve` inicia PostgreSQL/API/Streamlit; `demo` genera sintéticos y comprueba eventos. El perfil `pipeline` sigue pendiente. Consulta [T5](docs/events.md).

## Preparar y comprobar Docker

La [guía de instalación local](docs/setup.md) contiene requisitos, instrucciones para macOS y Linux y diagnóstico de problemas. En macOS, desde la raíz de esta carpeta:

```bash
python3 scripts/setup-docker-macos.py
export DOCKER_CONTEXT=colima
python3 scripts/doctor.py
bash scripts/docker-smoke.sh
```

La última orden comprueba construcción de imágenes, HTTP, red interna, montajes y persistencia con un proyecto Compose temporal. La configuración en `docker/smoke/` no despliega el recomendador.

## Documentos disponibles

- [Infraestructura inicial y operación](docs/bootstrap.md): qué instala esta entrega y cómo repetirla.
- [Trabajo entre dos Macs](docs/superpowers/specs/2026-10-19-entrenamiento-dos-macs-design.md): instalación en la Mac Apple Silicon de 24 GB, reportes por GitHub Releases privado y diseño pendiente de entrenamiento; H&M se descargará de Kaggle en esa Mac.
- [Plan de plataforma, entregables y estructura](docs/superpowers/plans/2026-10-18-plataforma-local-hm.md): ocho etapas, contratos, estructura objetivo y cobertura del curso; incorpora Q1–Q4 como nueva dependencia antes de T4 y distingue lo preparado de lo pendiente.
- [Ampliación Q1–Q4: calidad temporal, grafos y ranking](docs/superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md): Q1–Q4 cerradas y auditadas; H seleccionado y T4-A cerrada; siguiente ejecución: seguimiento T4-B v2 en mac24. Incluye protocolo, pruebas, entregas entre Macs y criterios de cierre.
- [Q4: resumen y selección](docs/quality-summary.md): comando único sobre resultados cerrados, presupuesto, verificaciones y devolución de evidencia.
- [Cobertura del temario](docs/course-coverage.md): subtemas cubiertos, parciales y pendientes, sin confundir una red comercial con una red social.
- [Selección del motor y explicación académica](docs/model-selection.md): criterio confirmado, resultados comparables, experimentos del temario y límites de las conclusiones.
- Propuesta académica en Word: archivo local `Propuesta_proyecto_final_Datos_Masivos.docx`, fuera de Git. El diseño y el plan en Markdown contienen las decisiones técnicas versionadas.
- [Diseño de ejecución local y reproducibilidad](docs/superpowers/specs/2026-10-18-despliegue-local-reproducible-design.md): recursos verificados, arquitectura local, contrato del futuro repositorio y pruebas para otra computadora.
- [Resultados de validación de H&M](validation_hm/README.md) y [resumen estructurado sin muestras](evidence/dataset-validation-summary.json).
- Notebook temporal de validación: archivo exploratorio local `notebooks/validacion_hm_temporal.ipynb`, fuera de Git. No constituye el instalador del proyecto.

## Requisitos de la futura entrega

Ya existen versiones bloqueadas, `.env.example`, Dockerfiles, Compose y scripts de diagnóstico, configuración y pruebas de la infraestructura. Ya hay procesamiento temporal T2 y entrenamiento/evaluación T3. T4-B añade servicio de recomendaciones y T5 implementa interfaz y demo sintética sin Kaggle. El ensayo H&M T5 está preparado para mac24. Quedan transporte de paquetes completos, respaldo/restauración y operación automática. Los contratos y criterios de aceptación están definidos en el diseño y el plan.

Los datos reales, tokens y credenciales permanecen fuera del código versionado. La documentación distingue la infraestructura disponible de las funcionalidades de recomendación pendientes.
