# Proyecto final de Datos Masivos

Plataforma local de recomendaciones para comercio electrónico con H&M, PyTorch y Docker Compose. La ejecución prevista utiliza recursos propios, sin nube ni servicios de pago.

Repositorio privado: [emilianodesu/clothing-recommendation](https://github.com/emilianodesu/clothing-recommendation). Cada equipo necesita autenticación Git con acceso al repositorio; sus credenciales de ejecución se generan localmente mediante bootstrap.

**Implementación iniciada:** repositorio Git local, paquete Python, dependencias bloqueadas, imágenes de aplicación/trabajos y bootstrap de PostgreSQL y Qdrant. T3 incorpora el entrenamiento de dos torres PyTorch, checkpoints, evaluación exacta y MLflow local. La API de recomendaciones, Streamlit y los DAG todavía están pendientes. T2 ya incluye descarga verificable, ingestión completa a Parquet, perfiles temporales y trabajos desacoplados con reportes por GitHub. Consulta [datos y ejecución T2](docs/data.md).

## Empezar en la segunda Mac

Después de clonar en una Mac Apple Silicon con Git, Homebrew, Python 3.9+ y Ruby, lee [las instrucciones del agente](AGENTS.md) y ejecuta desde la raíz:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/start-second-mac.py --machine-id mac24
```

Prepara Docker/Colima, instala la infraestructura, ejecuta las pruebas y genera un ZIP incluso ante fallos normales. Con GitHub CLI autenticado, publícalo con `python3 scripts/github-handoff.py upload RUTA-DEL-ZIP`. En la Mac de desarrollo, `python3 scripts/github-handoff.py list` muestra reportes y `python3 scripts/github-handoff.py download RUN_ID` descarga y valida el elegido. La [guía entre equipos](docs/second-mac.md) explica la autenticación y los comandos. Esta primera tarea no necesita el dataset ni inicia entrenamiento.

T1 ya pasó en la Mac física de 24 GB (35 pruebas). Para continuar allí con los datos, sigue [T2 en mac24](docs/second-mac.md#t2-preparar-los-datos-en-mac24). El agente debe fijar el commit, preparar Colima con 14 GiB, descargar H&M de Kaggle, ejecutar una corrida sintética y otra completa con `data-job start --resources mac24` (10 GiB para el job), y publicar ambos reportes. `--resources base` conserva el presupuesto original de 5 GiB. Los [perfiles y límites efectivos](docs/data.md#salidas-y-recursos) quedan registrados y se conservan al reanudar. T3 ya completó demo, dos pilotos y una época full de 22,674,665 ejemplos en mac24; la [evidencia T3](evidence/mac24-t3-2026-10-19.json) registra recursos, métricas y Releases. Full supera popularidad en validación, con cobertura baja pendiente de revisión. **T2 ya pasó en mac24**: corridas sintética y H&M completo, con límites efectivos de 10 GiB/3 CPU/2 hilos. La [evidencia de mac24](evidence/mac24-t2-2026-10-19.json) contiene conteos, memoria, duración y enlaces a ambos reportes privados.

## Entrenar (T3)

Sigue [T3 en la segunda Mac](docs/second-mac.md#t3-entrenar-y-devolver-evidencia-desde-mac24): verifica MLflow, ejecuta demo → hm-small → hm-medium → hm-full secuencialmente y publica cada reporte. El [contrato de evaluación](docs/evaluation.md) describe métricas, cortes, recursos y recuperación. `train-job start/status/stop/resume/export` reutiliza el transporte privado de T1/T2. No necesita TensorFlow, Hopsworks, nube ni librerías científicas en el host. El despliegue de la API y el transporte de pesos siguen siendo T4.

## Diagnosticar el modelo existente

El diagnóstico de demo y full en mac24 ya pasó; la [evidencia](evidence/mac24-t3-diagnostics-2026-10-19.json) muestra que popularidad de 7 días supera al modelo y confirma concentración alta. La calidad permanece pendiente de revisión. Para [diagnosticar concentración y personalización](docs/diagnostics.md) se usa `diagnose-job`: reproduce la evaluación del checkpoint full, compara popularidad de 7/28 días y mide cambios al modificar las entradas del usuario. Reutiliza pesos en lectura y devuelve agregados por GitHub. No entrena ni evalúa test; la aceptación de calidad sigue pendiente.

## Siguiente experimento: tres épocas

El [procedimiento de tres épocas](docs/three-epochs.md) prepara un candidato nuevo con una sola variable modificada y compara ambos checkpoints con bootstrap pareado y diez permutaciones entre usuarios conocidos. La secuencia de mac24 produce cuatro reportes por GitHub: entrenamiento/comparación demo y entrenamiento/comparación full. Test sigue reservado; no configura SSH ni inicia T4.

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
- [Plan de implementación, entregables y estructura](docs/superpowers/plans/2026-10-18-plataforma-local-hm.md): estado actual, ocho etapas, tareas verificables, contratos, estructura objetivo y cobertura del curso. Es la referencia operativa más reciente; distingue lo preparado de lo pendiente.
- Propuesta académica en Word: archivo local `Propuesta_proyecto_final_Datos_Masivos.docx`, fuera de Git. El diseño y el plan en Markdown contienen las decisiones técnicas versionadas.
- [Diseño de ejecución local y reproducibilidad](docs/superpowers/specs/2026-10-18-despliegue-local-reproducible-design.md): recursos verificados, arquitectura local, contrato del futuro repositorio y pruebas para otra computadora.
- [Resultados de validación de H&M](validation_hm/README.md) y [resumen estructurado sin muestras](evidence/dataset-validation-summary.json).
- Notebook temporal de validación: archivo exploratorio local `notebooks/validacion_hm_temporal.ipynb`, fuera de Git. No constituye el instalador del proyecto.

## Requisitos de la futura entrega

Ya existen versiones bloqueadas, `.env.example`, Dockerfiles, Compose y scripts de diagnóstico, configuración y pruebas de la infraestructura. Ya hay procesamiento temporal T2 y entrenamiento/evaluación T3. Se añadirán servicio de recomendaciones, transferencia de modelos y respaldo/restauración. Habrá una demo sintética sin Kaggle y un modo completo con acceso propio a los datos H&M. Los contratos y criterios de aceptación están definidos en el diseño y el plan.

Los datos reales, tokens y credenciales permanecen fuera del código versionado. La documentación distingue la infraestructura disponible de las funcionalidades de recomendación pendientes.
