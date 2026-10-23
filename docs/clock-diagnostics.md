# T5: seguimiento de relojes en lectura

El diagnóstico H&M conserva `failed` y T5/E4 siguen abiertas. La [auditoría](../evidence/events-memory-review-mac24.json) encontró 15,767.860 s UTC frente a 1,593.043 s monotónicos. Esta entrega prepara una recolección breve sobre ese ZIP y el historial de energía del host; no reproduce compras ni inicia Docker.

**Estado vigente:** la recolección mac24 ya fue [auditada](../evidence/events-clock-review-mac24.json) y resultó incompleta por límite de tamaño. El procedimiento siguiente se conserva como histórico; no repetirlo automáticamente. Sigue implementar y comprobar el protocolo prospectivo descrito abajo, con la causa histórica aún desconocida.

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

El [prompt de recolección](mac24-t5-clock-prompt.md) ya se ejecutó y se conserva como histórico. No hay otra ejecución pendiente en mac24 hasta preparar y comprobar el siguiente protocolo; no entrenar, evaluar test, repetir H&M ni iniciar T6.

## Recolección mac24 (2026-10-22, America/Mexico_City)

Una única recolección `events-mac24-20261023T012714Z-659b1686` (run_id UTC),
en `7900b82f5e7483624ba4df888d821b16438839c3`: **failed**, evidencia incompleta
válida, `clock_diagnostic.complete=false`. [Reporte publicado y verificado por
descarga](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-events-mac24-20261023T012714Z-659b1686).
[Resultado y entorno exactos](../evidence/events-clock-mac24.json).

La consulta pmset terminó con `collection_status=size_limit` (límite 16 MiB).
El fragmento disponible no abarca la ventana fuente: cero filas fechadas dentro
de ella y una fila con timestamp no reconocido. Sleep/Wake/DarkWake: 0/0/0
observadas; no demuestra ausencia de reposo. No se repitió la consulta ni se
exportaron mensajes completos. El lector devolvió 1, fallo válido.

Entorno actual: Darwin 26.6.2, ARM64, Python 3.14.3. `time` usa
`clock_gettime(CLOCK_REALTIME)`, ajustable, resolución aproximada 1 µs;
`monotonic` y `perf_counter` usan `mach_absolute_time()`, no ajustables,
resolución aproximada 41.67 ns. El ZIP anterior no registra versión/identidad
del intérprete: no puede afirmarse si cambió desde aquel ensayo.

Se recalculan 15,767.860335 s UTC y 1,593.042546 s monotónicos, diferencia
14,174.817789 s. La causa permanece sin confirmar; esta recolección incompleta
no permite atribuirla a reposo ni hacer comparaciones de rendimiento.

La suite host pasó 131 pruebas en 8.498 s, sin Docker. Se comprobó antes y
después el SHA-256 de la fuente:
`98a6a063f6dad2bbda7d56bd8e1a4f8c11111d293407d7f9f7497abb77c31d05`, intacto.
Pruebas y preservación son declaraciones separadas del ZIP. No se ejecutó
events-check, Docker, entrenamiento, test ni T6. T5/E4 permanecen abiertas;
la Mac principal revisará esta evidencia antes de preparar otro paso.

## Revisión en la Mac principal (2026-10-22)

La [auditoría](../evidence/events-clock-review-mac24.json) verifica el ZIP de
5,159 bytes, su inventario/identidad/hash, igualdad de `result` y `environment`
con Git y huella de todos los scripts desde el commit congelado. También
verifica la fuente original y recalcula las doce capturas y los once intervalos
cronológicos. Estos once intervalos de reloj son distintos de los nueve
intervalos por servicio del diagnóstico de memoria anterior.

**La recolección termina revisada como incompleta; T5/E4 no se cierran.** El
límite de captura se respetó, pero sólo se conservó el fragmento inicial del
log. No permite afirmar que el historial completo carezca de eventos para la
ventana. `events_truncated=false` sólo describe el límite de 256 eventos
reconocidos; no contradice `collection_status=size_limit`. La fila malformada
podría ser un corte de línea u otro problema de formato: sin el log original
no puede decidirse. Tampoco puede atribuirse la diferencia temporal a reposo.

Las 131 pruebas y la preservación antes/después siguen siendo declaraciones
de mac24, separadas de los bytes y cálculos comprobados aquí. No se transfirió
ni volvió a consultar su historial de energía. Se conservan los tres reportes
T5 fallidos; no se modifica el criterio congelado de memoria.

El siguiente paso es desarrollo local: integrar marcas UTC/monotónicas por
operación y guard de reposo supervisado, límites de 2 s/7,200 s ya declarados,
timeouts según presupuesto restante y cierre estructurado ante discontinuidad.
El contrato operativo nuevo conservará límites y controles funcionales,
swap/OOM e identidades, documentando por separado pico histórico y presión.
Antes de emitir un prompt H&M se requieren verificación sintética y pruebas
del nuevo protocolo. No repetir la recolección ni elevar su límite para buscar
un resultado distinto; si la causa histórica sigue desconocida, la medición
nueva deberá acreditar su propia continuidad.


## Implementación del protocolo prospectivo

La propuesta anterior está implementada en [events-operational-v1](events-operational.md). Consulta [la evidencia local](../evidence/events-operational-local.json) y [el nuevo prompt mac24](mac24-t5-operational-prompt.md). No repetir la captura histórica de energía; su resultado permanece incompleto y la causa desconocida.
