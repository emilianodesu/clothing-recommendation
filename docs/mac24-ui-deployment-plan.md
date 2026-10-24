# Siguiente paso: actualizar exclusivamente UI en mac24

Estado: **lanzador implementado y verificado localmente; ejecución mac24 pendiente**. Véanse [instrucciones de ejecución](mac24-ui-deploy.md) y [evidencia local](../evidence/ui-deployment-local.json). La [preparación de recuperación](../evidence/ui-recovery-review-mac24.json) quedó aceptada con el alcance indicado abajo. Los adaptadores `mac24_ui_recovery.py` ya terminaron: no volver a ejecutarlos ni usar `--restore-only`.

## Identidades fijadas

| Elemento | Referencia |
| --- | --- |
| Preparación aceptada | `ui-recovery-5f15b3471c1a`, procedimiento `f4cd8a7184d896d7a72f3b29c4c951d99953734f` |
| Imagen histórica recuperable | `sha256:38b8937c7c16245f3198e5a1e1561a9dae75d5857d78cc4bfdcfcdb211fc4fe1` |
| Código UI histórico | `6130943641e599121119660693ed276cf3b3fbca`; archivo SHA-256 `5a59c1c6014f5c5fc4980783f398cff5b45c116d52ac09c1951feb12450fa4d8` |
| Código UI corregido | archivo SHA-256 `fb1cce631c29550e7e138628c2cbbcbb6b86639bd775b9c9e7c5bd99219234da` |
| Estado privado conservado | `.runtime/ui-recovery-buildx-v1/state.json` |

La imagen histórica arrancó el servidor Streamlit en un contenedor sin red ni datos, con imports y huella correctos. El probe no abrió una sesión ni verificó el render completo de `app.py`. Es recuperación de código reconstruido; no una copia binaria de la imagen perdida ni una prueba de integración con la API. Cada auxiliar se verificó saludable después de su arranque; el reporte no acredita una comprobación final conjunta de los seis. El nuevo lanzador correlaciona el reporte agregado con el estado privado, comprueba disponibilidad de esa imagen y la conserva por ID.

## Trabajo previo al cambio de UI

1. Usar `scripts/mac24_ui_deploy.py`: `--preflight-only` comprueba sin reservar; `--deploy` prepara, prueba y aplica en una única ventana; `--restore-only` recupera sólo una ventana pendiente. Tiene reserva propia y publicación de agregados. Usar el plugin Buildx que informa Docker, comprobar su huella/versión y mantener configuración anónima aislada. Conservar ambos intentos fallidos y la preparación aceptada.
2. Capturar y validar el inventario privado actual. Las seis recuperaciones anteriores cambiaron sus horas de inicio; no exigir la instantánea previa al mantenimiento. Exigir mismos IDs/imágenes/límites de esos auxiliares, originales API/PostgreSQL/UI intactos, salud, H vigente, Airflow pausado/inactivo y ausencia de trabajos.
3. Preparar candidata desde un commit limpio y fijado, con tag independiente e imagen final por ID. Dockerfile, `pyproject.toml` y `uv.lock` coinciden entre el histórico y el código corregido. La corrección UI persiste el ID del stream y consulta sus metadatos actuales en cada rerun; no cambia reglas H ni necesita entrenamiento.
4. Mantener los presupuestos de la ventana: 5 GiB de límites de los tres servicios originales + constructor de 2 GiB + margen de 1 GiB = 8 GiB, frente a 13.621 GiB efectivos. La construcción requiere otra pausa acotada de los seis auxiliares; no cabe añadirla al inventario completo. Tras parar el constructor, la prueba de 512 MiB requiere 6.5 GiB con margen. Recalcular en el momento y no ampliar recursos automáticamente.
5. Ejecutar en la candidata la regresión específica `tests/integration/test_ui_metadata.py`, con HTTP sintético sin red ni bases reales: revisión 4→5, selección del segundo stream y metadatos vigentes. Verificar también imports, hash y salud. Esta prueba focalizada no reabre la aceptación completa ni T8. Si falla, recuperar auxiliares y conservar UI actual.

## Aplicación y recuperación

Preparar dos overrides privados de Compose: candidata y recuperación histórica. Cada uno debe modificar únicamente `services.ui.image`, usando su imagen por ID, y conservar el resto de la configuración, red, credenciales propias, binding localhost, 512 MiB y 0.5 CPU. No cambiar `hmrec-app:0.1.0`, que también usan otros servicios.

La operación fija del lanzador es `compose up -d --no-deps --no-build --pull never --wait --wait-timeout 90 ui`, con contexto `colima-hm-project`, directorio original, `.env` propio, archivo Compose, override y proyecto explícitos. Validar la configuración resuelta en privado antes de aplicarla; no mostrar secretos. Éste es el único servicio cuya identidad/generación puede cambiar por despliegue.

Si falla la nueva UI, aplicar el override histórico con las mismas restricciones y comprobar salud; conservar candidata, logs y resultado failed. La recuperación cambia también la generación UI, no restaura su ID antiguo. Terminar siempre recuperando los seis auxiliares por sus IDs guardados y volver a comprobar conjuntamente la salud de todos al cierre; comprobar API/PostgreSQL sin cambios de identidad/generación. No reconstruir servicios para recuperarlos.

El lanzador comprueba UI desde localhost, identidad del archivo y render de la UI instalada mediante Streamlit AppTest contra la API real, sin pulsar botones. La cabecera se contrasta con los metadatos actuales del stream. Este render no equivale a una inspección visual en navegador: el reporte conserva `visible_browser_verified=false`. La revisión visual posterior sigue separada. Cualquier demostración con escritura necesitará un stream sintético explícito, separado de los originales. Conservar el override activo y documentar su uso en operaciones futuras: ejecutar Compose sin él podría seleccionar la imagen antigua del tag compartido.

## Cierre esperado

Un nuevo reporte deberá distinguir preparación de candidata, aplicación, rollback si ocurrió, recuperación de auxiliares y verificación visible. Se publicarán sólo agregados y huellas; logs y configuración seguirán privados. T8-B permanece failed y la entrega académica no cambia. La implementación local no acredita despliegue en mac24 ni autoriza repetir los comandos históricos de recuperación. Una recuperación exitosa después de un fallo conserva el resultado de despliegue `failed`. La salud y generación de la UI se vuelven a comprobar antes y después de restaurar auxiliares; un fallo tardío también activa rollback.
