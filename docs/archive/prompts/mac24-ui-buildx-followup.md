# Continuación UI con el plugin Buildx seleccionado por Docker

**Cerrada tras revisión:** `ui-recovery-5f15b3471c1a` pasó y su preparación fue aceptada; ver [auditoría](../evidence/ui-recovery-review-mac24.json). Imagen histórica verificada en aislamiento y seis auxiliares recuperados. Los comandos de esta página son históricos: no repetir ni usar recuperación. Sigue [preparar candidata y despliegue exclusivo de UI](mac24-ui-deployment-plan.md).

El intento `ui-recovery-926496b63278` conserva **failed**. La traza del operador ubica el fallo en `Private Buildx version differs`, antes de consultar manifiestos, descargar BuildKit o pausar servicios. `maintenance_started=false`: no hubo recuperación mediante arranques; se verificó que el inventario permanecía intacto. No repetir el modo `--public-registry-followup` ni usar su recuperación.

El adaptador anterior buscaba primero `docker-buildx` en `PATH`. Docker selecciona plugins mediante su configuración y directorios de plugins; ambos mecanismos pueden elegir ejecutables distintos. La discrepancia se reprodujo localmente con dos plugins sintéticos y Docker real. Se corrigió la selección consultando `docker info --format '{{json .ClientInfo.Plugins}}'`, usando exclusivamente el `Path` del plugin válido llamado `buildx`. Véase el [código oficial de selección](https://github.com/docker/cli/blob/master/cli-plugins/manager/manager.go).

La configuración privada enlaza ese mismo ejecutable, registra su SHA-256 y comprueba la huella antes de cada comando Docker privado. Conserva la comparación exacta de versión. No instala, actualiza ni degrada Buildx. Las versiones original y privada se incluyen ahora en el resumen; la ruta del ejecutable queda en el estado privado. La configuración anónima y las comprobaciones de acceso público siguen vigentes.

## Ejecución única

El nuevo modo `--buildx-followup` exige los dos reportes exactos, sus estados privados, la traza de discrepancia de versión y ausencia de mantenimiento/build/probe/descargas en el último intento. Conserva la comparación con el inventario anterior y rechaza cambios en cualquiera de sus contenedores. Usa `.runtime/ui-recovery-buildx-v1/` y publica `evidence/ui-recovery-buildx-mac24.json`; las carpetas, reservas y reportes previos permanecen intactos.

Desde SSH, con `main` limpio actualizado al SHA entregado, AC y tapa abierta:

```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export SSL_CERT_FILE=/etc/ssl/cert.pem
python3 scripts/mac24_ui_recovery.py --buildx-followup \
  --expected-commit SHA_COMPLETO --publish
```

Mantener la terminal abierta. Primero verifica plugin, contexto y acceso a imágenes públicas, sin detener servicios. Sólo después aplica el [mantenimiento autorizado de seis auxiliares](mac24-ui-maintenance-plan.md), construye la imagen histórica con 2 GiB/1 CPU, la prueba aislada con 512 MiB/0.5 CPU y recupera los auxiliares. API/PostgreSQL/UI originales permanecen activos. No actualiza UI ni ejecuta campañas.

Si falla, conservar todo y revisar el reporte, sin repetir. Sólo si este seguimiento indica recuperación/verificación pendiente por interrupción:

```bash
python3 scripts/mac24_ui_recovery.py --buildx-followup --restore-only \
  --expected-commit SHA_DEL_SEGUIMIENTO --publish
```

Cuando no empezó mantenimiento, este modo sólo verifica el estado: no arranca servicios detenidos externamente. Si comenzó, conserva la recuperación por IDs ya implementada. Un fallo de publicación se resuelve publicando el commit guardado, sin repetir preparación.

## Evidencia

[Revisión del fallo remoto](../evidence/ui-recovery-buildx-review-mac24.json) y [validación local](../evidence/ui-recovery-buildx-local.json). Las pruebas locales acreditan selección del plugin, rechazo de cambios/discrepancias, preservación de antecedentes y guardas de continuación. La ejecución completa de construcción/probe sigue pendiente en mac24; no se acredita por estas pruebas ni por la revisión estática.
