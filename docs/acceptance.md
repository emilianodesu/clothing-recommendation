# Aceptación final sintética

`python3 scripts/project.py verify --suite acceptance` reúne pruebas existentes de recomendaciones, activación/rollback, HTTP, eventos idempotentes, demo, interfaz, publicación de perfiles y respaldo/restauración. Usa exclusivamente fixtures sintéticas dentro de la imagen. No incluye entrenamiento, evaluación científica final, carga T8 ni el scheduler Airflow real. No sustituye los ensayos H&M ya auditados ni una captura de navegador.

## Requisitos y ejecución

El motor Docker debe estar dedicado y **sin ningún contenedor activo**, con al menos 7 GiB efectivos y 2 CPU. No usar el motor mac24 operativo tal como está: el comando lo rechazará antes de crear recursos. No detiene servicios ni crea una VM automáticamente. Otro perfil Colima consume RAM adicional; no iniciarlo junto a la VM mac24 de 14 GiB sin un plan de capacidad.

Se requieren imágenes locales `hmrec-jobs:0.1.0` y PostgreSQL fijado por digest en Compose. Preparar el entorno siguiendo [setup](setup.md) y [bootstrap](bootstrap.md); construir sólo jobs desde el checkout que se verificará, con su configuración local y CA. El comando no descarga imágenes ni construye nada, y rechaza fuentes, tests o migraciones de jobs distintos del checkout. Detener previamente sólo los servicios de la instalación sintética dedicada, de forma explícita y conservando sus volúmenes; nunca usar `down` sobre la instalación H&M para seguir esta guía.

En la terminal del entorno dedicado, con `DOCKER_CONTEXT` seleccionado:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
python3 scripts/project.py verify --suite acceptance
```

No requiere `.env` para ejecutarse: genera credenciales temporales nuevas, ignora las de los servicios del proyecto y no monta datos/configuraciones del host. No admite `--existing-services`.

## Aislamiento y reporte

Crea una red interna de nombre aleatorio, PostgreSQL temporal de 512 MiB/1 CPU y un worker de 5 GiB/2 CPU, con swap deshabilitado. PostgreSQL usa datos en tmpfs; worker raíz de sólo lectura y tmpfs de 2 GiB dentro de su límite de RAM. Sin publicación de puertos al host ni Docker socket. Las pruebas HTTP/UI usan su propio proceso y PostgreSQL temporal.

El estado passed exige salida cero y JUnit con al menos una prueba, cero fallos, errores u omisiones. El reporte registra commit/limpieza del checkout, imágenes, huellas de código/fixtures y resumen de tests. No es una medición de pico de RAM, una certificación del código en un checkout sucio ni un nuevo resultado H&M. El tiempo incluye preparación y limpieza de estos recursos.

La salida indica `.runtime/hmrec-acceptance-.../report.json`; JUnit y log quedan privados junto a él. La limpieza elimina exclusivamente los contenedores creados por esta ejecución y su red, también tras fallo normal; si falla, el reporte conserva failed. Una interrupción abrupta puede dejar recursos: conservar nombre y carpeta, inspeccionar antes de actuar y no repetir a ciegas. No usar prune.

Este reporte no es un ZIP del protocolo `github-handoff`: no enviarlo a ese lector/publicador. La integración de transporte y el ensayo remoto requieren un paso posterior revisado. Un rechazo de preflight (motor ocupado, recursos o imagen incompatible) no crea reporte ni recursos; se imprime el motivo.

## Validación ejecutada — 24 de octubre de 2026

[Aceptación local](../evidence/acceptance-local.json) en checkout limpio `8c8b730feed982f2c45a6118933d36cb3096294a`: **60 pruebas, cero fallos, errores u omisiones**, 19.894 s incluyendo preparación y limpieza de los recursos de la prueba (no construcción de imagen/VM). Se recontaron los casos del JUnit y se registraron sus huellas. Se ejecutó en la Mac principal ARM64 de 16 GiB, no en mac24.

Con autorización del usuario, se pausó `default`, se preparó `hm-acceptance` con 8 GiB/4 CPU (8,307,101,696 bytes efectivos) y se construyó jobs desde el checkout con `uv.lock` y CA local. PostgreSQL se descargó por digest. Tras el ensayo se detuvo `hm-acceptance` y se restauró `default`: los 22 contenedores originales conservaron ID/imagen y quedaron activos y healthy. No se modificó mac24 ni se montaron datos H&M. El perfil de aceptación y sus imágenes quedan conservados, detenidos.

La suite host previa tiene 246 pruebas: 245 aprobadas y una opcional omitida. La aceptación Docker de 60 casos es una comprobación distinta, sin omisiones. No representa un benchmark, una medición de pico de memoria ni una grabación de navegador. La suite usa AppTest/HTTP para UI y componentes de pipeline; no reproduce un scheduler Airflow completo ni el ensayo H&M.

La aceptación sintética ya está comprobada. Las [capturas de demo](demo-gallery.md), el [tag académico](release-notes.md) y la [revisión visual posterior de mac24](../evidence/ui-visual-review-mac24.json) también están disponibles. No se grabó un video nuevo de instalación o restauración. T8-B conserva failed y no se declara aprobación global T8/E8. No repetir test, carga ni esta suite para fabricar evidencia visual.
