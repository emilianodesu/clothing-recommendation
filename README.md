# Proyecto final de Datos Masivos

Plataforma local de recomendaciones para comercio electrónico con H&M y Docker Compose, con selección del motor por calidad medida. La ejecución utiliza recursos propios, sin nube ni servicios de pago. PyTorch se conserva como experimento académico reproducible.

Repositorio privado: [emilianodesu/clothing-recommendation](https://github.com/emilianodesu/clothing-recommendation). Cada equipo necesita autenticación Git con acceso al repositorio; sus credenciales de ejecución se generan localmente mediante bootstrap.

**Implementación iniciada:** repositorio Git local, paquete Python, dependencias bloqueadas, imágenes de aplicación/trabajos y bootstrap de PostgreSQL y Qdrant. T3 incorpora el entrenamiento de dos torres PyTorch, checkpoints, evaluación exacta y MLflow local. La API de recomendaciones, Streamlit y los DAG todavía están pendientes. T2 ya incluye descarga verificable, ingestión completa a Parquet, perfiles temporales y trabajos desacoplados con reportes por GitHub. Consulta [datos y ejecución T2](docs/data.md).

**Decisión vigente (20 de octubre):** el usuario aprobó ampliar la validación antes de cerrar el motor. Historial28d+popularidad7d queda como referencia provisional. El [plan Q1–Q4](docs/superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md) añade tres cortes temporales, recuperación por grafo y ordenamiento LightGBM, con presupuesto y criterios de selección fijados antes de medir. [Selección y explicación de resultados](docs/model-selection.md) distingue evidencia, hipótesis y límites. Q1 ya dispone de evaluador y `quality-job`; sigue la [guía de campaña](docs/quality-campaign.md). Los cinco reportes de mac24 ya se descargaron y [revisaron](evidence/quality-q1-review.json): Q1 está cerrada. T4 sigue pendiente y test reservado.

## Campaña de calidad temporal, grafos y ranking

Secuencia: Q1 referencias temporales → Q2 grafo y control PageRank → Q3 ordenamiento aprendido → Q4 selección → T4–T8 despliegue y aceptación. El [diseño](docs/superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md) fija fechas, candidatos, límites, cobertura del curso y cierre de experimentos. Los experimentos PyTorch existentes se conservan sin repetirlos. PageRank se estudia en una cohorte acotada; no se promueve un motor por resultados de esa muestra.

Q1, Q2 y Q3 están cerradas operativamente: se descargaron y verificaron cinco, cinco y diez reportes mac24 respectivamente. H supera a P en las tres semanas. La [revisión Q2](evidence/quality-q2-review.json) confirma que G mejora el MAP medio +6.47%, pero pierde Recall@200 −4.39%. La [revisión Q3](evidence/quality-q3-review.json) confirma que RH mejora MAP medio +4.03% conservando Recall, pero incumple el límite de pérdida por segmento; RG mejora MAP +14.48%, pero incumple Recall y segmentos. H sigue provisional: **el siguiente desarrollo es Q4, intervalos y decisión formal**, aún sin comando ejecutable. No repetir las campañas ni ajustar umbrales. La [guía Q3](docs/ranker-quality.md) conserva resultados, recursos y reproducción; su prompt es histórico. La [verificación local Q3](evidence/local-quality-q3.json) acredita 145 pruebas distintas y dos demos. El intercambio continúa por GitHub privado y reportes cerrados; los artefactos individuales permanecen en mac24 para Q4.

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

El [procedimiento de tres épocas](docs/three-epochs.md) ya produjo cuatro reportes aprobados en mac24, descargados y verificados en la máquina principal. [Resultados](evidence/mac24-three-epochs-2026-10-20.json): MAP/NDCG empeoraron frente a una época, aunque Recall y cobertura mejoraron; popularidad7d supera ambos modelos. La calidad no se aceptó. Test sigue reservado y T4 permanece pendiente.

## Historial reciente: resultados revisados

La [validación de compras recientes](docs/recent-history.md) compara un prefijo personal de artículos comprados en 28 días, completado con popularidad7d o con el modelo de una época. Usa los mismos compradores, bootstrap pareado y diez permutaciones del historial. Los dos reportes de mac24, demo y full, ya se descargaron y revisaron. Historial+popularidad cumple el criterio principal de validación; añadir el modelo no demuestra mejora de MAP y reduce NDCG/Recall. La [revisión](evidence/mac24-recent-history-review-2026-10-20.json) sustenta la referencia provisional de la nueva campaña Q1–Q4. El diseño T4 basado en esa regla se conserva, condicionado a la selección final. Este candidato no acredita superioridad del modelo PyTorch. Test y aceptación final siguen pendientes.

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

La [guía de la primera entrega](docs/bootstrap.md) explica credenciales, red, versiones, certificados, mantenimiento de locks y límites de lo comprobado. Los perfiles de recomendación `demo`, `serve` y `pipeline` se añadirán con sus componentes; aún no existen y no están sustituidos por comandos simulados.

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
- [Ampliación Q1–Q4: calidad temporal, grafos y ranking](docs/superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md): Q1–Q3 cerradas y auditadas; Q4 pendiente. Incluye protocolo, pruebas, entregas entre Macs y criterios de cierre.
- [Selección del motor y explicación académica](docs/model-selection.md): criterio confirmado, resultados comparables, experimentos del temario y límites de las conclusiones.
- Propuesta académica en Word: archivo local `Propuesta_proyecto_final_Datos_Masivos.docx`, fuera de Git. El diseño y el plan en Markdown contienen las decisiones técnicas versionadas.
- [Diseño de ejecución local y reproducibilidad](docs/superpowers/specs/2026-10-18-despliegue-local-reproducible-design.md): recursos verificados, arquitectura local, contrato del futuro repositorio y pruebas para otra computadora.
- [Resultados de validación de H&M](validation_hm/README.md) y [resumen estructurado sin muestras](evidence/dataset-validation-summary.json).
- Notebook temporal de validación: archivo exploratorio local `notebooks/validacion_hm_temporal.ipynb`, fuera de Git. No constituye el instalador del proyecto.

## Requisitos de la futura entrega

Ya existen versiones bloqueadas, `.env.example`, Dockerfiles, Compose y scripts de diagnóstico, configuración y pruebas de la infraestructura. Ya hay procesamiento temporal T2 y entrenamiento/evaluación T3. Se añadirán servicio de recomendaciones, transferencia de modelos y respaldo/restauración. Habrá una demo sintética sin Kaggle y un modo completo con acceso propio a los datos H&M. Los contratos y criterios de aceptación están definidos en el diseño y el plan.

Los datos reales, tokens y credenciales permanecen fuera del código versionado. La documentación distingue la infraestructura disponible de las funcionalidades de recomendación pendientes.
