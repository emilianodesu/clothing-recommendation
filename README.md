# Proyecto final de Datos Masivos

Plataforma local de recomendaciones para comercio electrónico con H&M, PyTorch y Docker Compose. La ejecución prevista utiliza recursos propios, sin nube ni servicios de pago.

Repositorio privado: [emilianodesu/clothing-recommendation](https://github.com/emilianodesu/clothing-recommendation). Cada equipo necesita autenticación Git con acceso al repositorio; sus credenciales de ejecución se generan localmente mediante bootstrap.

**Implementación iniciada:** repositorio Git local, paquete Python, dependencias bloqueadas, imágenes de aplicación/trabajos y bootstrap de PostgreSQL y Qdrant. El motor personalizado, la API de recomendaciones, Streamlit y los DAG todavía están pendientes. Esta entrega prepara y comprueba la infraestructura base.

## Empezar en la segunda Mac

Después de clonar en una Mac Apple Silicon con Git, Homebrew, Python 3.9+ y Ruby, lee [las instrucciones del agente](AGENTS.md) y ejecuta desde la raíz:

```bash
python3 scripts/start-second-mac.py --machine-id mac24
```

Prepara Docker/Colima, instala la infraestructura, ejecuta las pruebas y genera un ZIP incluso ante fallos normales. La [guía entre equipos](docs/second-mac.md) explica los requisitos, el intercambio manual del paquete y cómo leerlo aquí con `scripts/read-handoff.py`. Esta primera tarea no necesita el dataset ni inicia entrenamiento.

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
- [Trabajo entre dos Macs](docs/superpowers/specs/2026-10-19-entrenamiento-dos-macs-design.md): instrucciones disponibles para comprobar la instalación en la Mac Apple Silicon de 24 GB y diseño pendiente de entrenamiento e intercambio de resultados mediante Git privado y carpetas.
- [Plan de implementación, entregables y estructura](docs/superpowers/plans/2026-10-18-plataforma-local-hm.md): estado actual, ocho etapas, tareas verificables, contratos, estructura objetivo y cobertura del curso. Es la referencia operativa más reciente; distingue lo preparado de lo pendiente.
- Propuesta académica en Word: archivo local `Propuesta_proyecto_final_Datos_Masivos.docx`, fuera de Git. El diseño y el plan en Markdown contienen las decisiones técnicas versionadas.
- [Diseño de ejecución local y reproducibilidad](docs/superpowers/specs/2026-10-18-despliegue-local-reproducible-design.md): recursos verificados, arquitectura local, contrato del futuro repositorio y pruebas para otra computadora.
- [Resultados de validación de H&M](validation_hm/README.md) y [resumen estructurado sin muestras](evidence/dataset-validation-summary.json).
- Notebook temporal de validación: archivo exploratorio local `notebooks/validacion_hm_temporal.ipynb`, fuera de Git. No constituye el instalador del proyecto.

## Requisitos de la futura entrega

Ya existen versiones bloqueadas, `.env.example`, Dockerfiles, Compose y scripts de diagnóstico, configuración y pruebas de la infraestructura. Se añadirán procesamiento, entrenamiento, servicio de recomendaciones, transferencia de modelos y respaldo/restauración. Habrá una demo sintética sin Kaggle y un modo completo con acceso propio a los datos H&M. Los contratos y criterios de aceptación están definidos en el diseño y el plan.

Los datos reales, tokens y credenciales permanecen fuera del código versionado. La documentación distingue la infraestructura disponible de las funcionalidades de recomendación pendientes.
