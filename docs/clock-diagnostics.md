# T5: seguimiento de relojes en lectura

El diagnóstico H&M conserva `failed` y T5/E4 siguen abiertas. La [auditoría](../evidence/events-memory-review-mac24.json) encontró 15,767.860 s UTC frente a 1,593.043 s monotónicos. Esta entrega prepara una recolección breve sobre ese ZIP y el historial de energía del host; no reproduce compras ni inicia Docker.

## Comando reproducible

Desde un clon limpio, con Python 3.9+ estándar y el SHA completo recibido:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py clock-check \
  --machine-id mac24 --expected-commit "$T5_CLOCK_COMMIT" \
  --source-report artifacts/handoff/events-mac24-20261022T184956Z-e167c9d2.zip
```

No necesita `.env`, dependencias científicas, build, Colima ni acceso SQL. El alias debe coincidir con el reporte fuente: ejecutar este comando sobre el ZIP de mac24 en otra máquina no produce evidencia de mac24. Conserva siempre esa distinción. Fuente inválida, alias distinto o checkout incorrecto se rechazan antes de recolectar; salida 2 y ningún reporte exitoso.

Se crea un nuevo run `events-…` con protocolo **`events-clock-v1`** y contexto `host-read-only`, compatible con el lector/transportador actualizado. Se registra SHA del ZIP original, identidad de paquete y commit, sin modificarlo. Se recalculan la duración UTC, la monotónica y las diferencias entre capturas. No se atribuye automáticamente la diferencia a suspensión.

La fuente debe conservar sus doce capturas, en orden de fase (`before_seed`, `after_seed`, `after_replay`, `after_restart`) y servicio (`api`, `ui`, `postgres`), sin regresiones UTC o monotónicas. Capturas faltantes, reordenadas o regresivas se rechazan como fuente inválida; no se fabrican observaciones para cerrar un reporte. Este control del seguimiento no cambia el estado del ZIP histórico.

## Energía y privacidad

El único comando de energía es `/usr/bin/pmset -g log`, en lectura. La captura tiene límite de 16 MiB y 30 s comprobados al ejecutar; si el host se suspende, el proceso sólo puede comprobar el vencimiento al volver a ejecutarse. Un archivo temporal con modo 0600 se elimina al terminar. No se exporta texto libre, motivos de wake, nombres de procesos/dispositivos, rutas ni configuración de energía.

El reporte conserva únicamente categorías `sleep`, `wake`, `darkwake` y fechas con zona normalizadas a UTC **dentro de la ventana del ZIP fuente**, hasta 256 entradas, más conteos y señales de cobertura/formato. Son entradas del log, no un conteo deduplicado de ciclos completos. Se indica si las fechas disponibles abarcan ambos extremos de la ventana; esto no garantiza que el sistema haya conservado todos los eventos.

Una lectura vacía, incompleta, no disponible o truncada queda explícita. `passed` significa comando completo, ventana abarcada y sin truncamiento/formato inválido detectado; **no prueba ausencia de suspensión, causalidad, rendimiento ni cierre T5**. `failed` por faltantes sigue siendo un reporte válido y se publica. La lectura incompleta no se soluciona repitiendo H&M. No se consulta todo el Unified Log ni se piden privilegios adicionales.

Las categorías ajenas a Sleep/Wake/DarkWake se contabilizan como desconocidas y no vuelven fallida una captura completa: el log contiene otras clases de eventos. Incluso si todas las filas de la ventana son desconocidas, `passed` sólo acredita la recolección, nunca una interpretación exhaustiva del formato. Los lectores de paquetes y el publicador rechazan campos extra y texto libre: `job.log`, `summary.md` y `commit.txt` deben coincidir exactamente con su contenido agregado generado.

También se registran versiones de Python/macOS, arquitectura e implementación/resolución de `time`, `monotonic` y `perf_counter` **en el momento del seguimiento**. No prueban qué intérprete se usó en el ensayo pasado. Los relojes actuales no se presentan como una reconstrucción de aquel entorno. Python distingue estas fuentes y permite [consultar su implementación](https://docs.python.org/3/library/time.html#time.get_clock_info).

## Controles temporales preparados

`scripts/clock_guard.py` contiene dos utilidades probadas para integrar después de revisar este seguimiento:

- `Timeline`: conserva UTC y monotónico en cada marca; rechaza regresiones, números inválidos, discrepancia acumulada o por intervalo superior a **2 s** y duración superior a **7,200 s** en cualquiera de los dos relojes. Son límites del futuro control de continuidad, fijados antes de otra medición; no se aplican a ZIP históricos. Las comprobaciones ocurren al marcar/verificar; no es un watchdog capaz de ejecutar durante suspensión.
- `AwakeGuard`: administra su propio proceso `caffeinate -is -w PID` y lo libera incluso ante excepciones. No cambia preferencias persistentes, no mantiene la pantalla encendida y no promete impedir cierre de tapa, suspensión forzada o apagado. La opción `-s` sólo tiene efecto con alimentación AC, según el manual local de macOS. El proceso se supervisa con `check()`; que esté vivo no constituye por sí solo una medición de continuidad.

**Aún no están conectados a `events-check`.** La siguiente integración necesita marcas antes/después de semilla, cada operación HTTP y reinicio, supervisión del guard, presupuesto restante en llamadas y un cierre fallido que conserve las observaciones ante una discontinuidad. No ejecutar ahora el ensayo antiguo envuelto en `caffeinate` como sustituto del protocolo pendiente.

## Propuesta del siguiente contrato operativo

La evidencia separó el máximo histórico de la actividad nueva. La documentación del kernel define [`memory.peak` como máximo acumulado y admite excesos transitorios de `memory.max`](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files). Por ello se propone una versión futura, explícita y separada de los dos protocolos anteriores:

| Control propuesto | Papel |
| --- | --- |
| Límites Docker/cgroup exactos; PostgreSQL 512 MiB y recursos actuales | Obligatorio |
| Cero swap/OOM capturados, identidad y generaciones esperadas, capturas completas | Obligatorio |
| Semilla temporal, paridad, idempotencia, actualización, fuente intacta y persistencia | Obligatorio |
| Continuidad de relojes, guard supervisado y presupuesto de ejecución | Obligatorio |
| Pico histórico, crecimiento del pico, eventos `max`, composición y presión | Diagnóstico conservado; no sustituirlo por una tolerancia ajustada a los 264 KiB |
| Latencia/capacidad bajo carga y margen operativo | Se evalúan posteriormente en T8 con condiciones y objetivos explícitos |

Esta propuesta reconoce que un pico de vida no mide la demanda aislada del ensayo. No demuestra que 512 MiB tengan margen suficiente ni que el sistema cumpla baja latencia. No se añaden ahora umbrales de presión ni se cambia la memoria para obtener un aprobado. **Los validadores vigentes permanecen intactos** y los dos fallos originales mantienen su estado.

Después de recibir el seguimiento, revisar correlación con energía y limitaciones de disponibilidad; integrar y verificar el nuevo protocolo antes de emitir un prompt H&M con commit exacto. Si no hay registros históricos, conservar causa desconocida y comprobar prospectivamente la continuidad. No reconstruir retrospectivamente evidencia faltante.

## Evidencia local y entrega

La [evidencia local](../evidence/events-clock-local.json) distingue los tests y el seguimiento del reporte sintético `events-development-20261022T050923Z-da5ae7f6` de la tarea pendiente en mac24. Se verificaron también los dos ZIP T5 fallidos y la fuente T4-A con el lector nuevo, conservando sus estados. No se cambiaron imágenes, dependencias, servicio, motor ni datos.

Para el otro equipo sigue [el prompt de recolección](mac24-t5-clock-prompt.md). Al terminar, validar/publicar el ZIP cerrado y devolver el resultado por Git; no entrenar, evaluar test, repetir H&M ni iniciar T6.
