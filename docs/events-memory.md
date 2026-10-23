# Diagnóstico T5 de memoria por fase

El intento H&M original conserva `failed` y T5/E4 siguen abiertas. Su [auditoría](../evidence/events-review-mac24.json) encontró un pico acumulado PostgreSQL de 512.258 MiB frente a 512 MiB. Esta entrega mide dónde aparece la presión, sin aumentar recursos, cambiar H ni reinterpretar el reporte original.

## Protocolo y criterios

`events-check --memory-diagnostics` genera `events-memory-v1` dentro del mismo transporte `events-handoff`. Sin la opción sigue disponible `events-v1`. El nuevo lector admite ambos; un lector antiguo no puede validar un resultado aprobado del protocolo nuevo. El SHA de todos los scripts Python del host queda en `environment.json`, además de código científico y lock. Cada corrida exige clon limpio y SHA completo.

Se conserva el criterio de pico estrictamente inferior al límite, cero swap y ausencia de OOM, junto con todos los controles funcionales v1. Además, se exigen capturas completas, límites efectivos coherentes, contadores consistentes y generaciones esperadas. Los contadores OOM deben ser cero en todas las capturas. La API cambia de generación al reiniciarse; UI/PostgreSQL permanecen estables. No se fija un umbral nuevo de presión mirando esta corrida.

**`diagnostics.complete=true` sólo significa que se tomaron las doce capturas.** Puede coexistir con `status=failed`. Un pico histórico superior al límite seguirá causando fallo, pero ahora habrá evidencia para separar el estado heredado de la actividad nueva. Ningún resultado cierra T5 sin revisión en la Mac principal.

## Qué se mide

Se capturan API, UI y PostgreSQL en cuatro momentos: antes de semilla, después de semilla, después de replay y después del reinicio de API. Se guardan:

- Identidad del contenedor, imagen, hora de arranque, número de reinicios e inode del cgroup; límites Docker y del kernel.
- Memoria actual, pico de vida, swap actual/pico, contadores `low`, `high`, `max`, `oom`, `oom_kill`, `oom_group_kill`.
- Memoria anónima, archivos/caché, kernel y memoria compartida; esta última puede formar parte de la memoria de archivos, no se suman como categorías disjuntas.
- Totales de presión `some` y `full`, en microsegundos, y tiempos de captura/fase.

Las diferencias sólo se calculan dentro de la misma generación del cgroup. Después del reinicio API se registra `generation_changed` y diferencias nulas. Una caída de contador sin cambio de identidad queda inválida. El crecimiento de `memory.peak` indica aumento del máximo histórico: **no es el pico independiente de ese intervalo**. Una diferencia cero puede ocultar picos inferiores al máximo anterior. La semilla conserva sus límites/pico/swap/OOM aislados; no se añaden contadores internos al worker.

Las lecturas son secuenciales y usan pequeños procesos `docker exec`, por lo que añaden algo de tiempo/memoria y no son una instantánea atómica. La identidad se verifica antes/después de cada captura. No se modifica ni reinicia el contador. No se exportan variables de entorno, configuración Docker, rutas de montajes, SQL ni datos por comprador.

Referencia de semántica: [interfaces de memoria cgroup v2 del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files).

## Fallos y lectura

La fase final se identifica como `final-validation`; un fallo de recursos incluye componente, criterio, observado y esperado, sin copiar textos de excepciones que puedan contener secretos. Capturas incompletas conservan el prefijo disponible y el tipo de error, y generan un reporte cerrado ante fallos controlados. Un corte de energía/proceso puede impedir el cierre: nunca interpretar una carpeta incompleta como éxito.

El lector recomputa los derivados y rechaza capturas/intervalos incoherentes incluso en reportes fallidos. El contrato v1 no se relaja. Un `peak_below_limit` fallido sigue siendo un fallo del ensayo, no corrupción del ZIP: lector salida 1; ZIP inválido salida 2.

## Ejecución

Para una prueba sintética usa un clon limpio con configuración propia:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima python3 scripts/project.py demo \
  --machine-id development --memory-diagnostics
```

El [prompt mac24](mac24-t5-memory-prompt.md) es ahora histórico: su única corrida H&M ya terminó y fue revisada abajo. No repetirlo. El reporte v1, sus streams, datos, ledger y volúmenes se preservan. No entrenar, abrir test ni iniciar T6.

## Evidencia local y revisión

Dos ensayos sintéticos desde clones limpios produjeron doce capturas completas y reportes aprobados; el último usa `7332cea42df360fd6910b3100412b01948844ed5`, dura 5.245 s y confirma el cambio de generación API sin reiniciar PostgreSQL. [Reporte privado](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-events-development-20261022T050923Z-da5ae7f6), [evidencia agregada](../evidence/events-memory-local.json). Son pruebas locales, no una medición de capacidad H&M.

La revisión independiente encontró y se corrigieron con regresiones dos problemas del lector: evasión mediante cambio de protocolo y causas/recursos contradictorios en reportes fallidos. No quedaron menores aplazados. La suite host final pasó 112 casos; la suite oficial events y las ocho pruebas de bootstrap Docker también pasaron. La suite científica completa pasó 133 casos y omitió la prueba dedicada a MLflow. El primer comando genérico `pytest` dentro de jobs encontró siete errores de colección de pruebas que pertenecen al host; el comando corregido separa ambos entornos y sus resultados están registrados en la evidencia.

Decisiones conservadas: el criterio `peak < limit` permanece, con el coste de que el diagnóstico H&M puede volver a terminar fallido por un pico histórico; la semilla mantiene métricas aisladas del job, sin añadir contadores internos por fase al worker. T5/E4 siguen pendientes de la nueva evidencia H&M y su revisión.

## Diagnóstico H&M mac24 (2026-10-22)

Corrida única `events-mac24-20261022T184956Z-e167c9d2`, commit
`44327b4cd9bfd8c5a803e58f3e34ccf675ed5fd2`, protocolo `events-memory-v1`.
Estado **failed**, con `diagnostics.complete=true`, doce capturas y cero errores
de captura. [ZIP válido publicado y verificado por descarga](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-events-mac24-20261022T184956Z-e167c9d2);
[resultado y entorno exactos, intervalos y evidencia local](../evidence/events-memory-mac24.json).

La fase final-validation identificó `postgres / peak_below_limit`: 537,141,248
bytes frente a 536,870,912. Ese máximo ya estaba presente antes de semilla y
no creció. El ZIP acredita identidad y arranque PostgreSQL del 2026-10-20
estables durante sus cuatro capturas. Mac24 declara por separado que también
se conservaron durante preparación/suite y que no se limpiaron contadores.

| Intervalo PostgreSQL | Segundos monotónicos entre capturas | Delta max | Presión some/full (µs) | Crecimiento del pico |
| --- | ---: | ---: | ---: | ---: |
| Antes/después de semilla | 1428.393 | 14,240 | 220,320 / 220,320 | 0 |
| Después de semilla/replay | 158.911 | 149,849 | 1,459,060 / 1,459,043 | 0 |
| Después de replay/reinicio API | 4.516 | 0 | 0 / 0 | 0 |

Todos los contadores OOM y valores swap capturados fueron cero. API cambió
de generación al reiniciarse, por lo que su intervalo de reinicio no es
comparable. UI/PostgreSQL mantuvieron generación. Los 164,089 eventos max
nuevos muestran actividad junto al límite; no son OOM ni explican por sí solos
la duración de SQL. Un crecimiento de pico cero no mide el pico propio del intervalo.

La semilla conservó 972,591 filas diarias y 1,069,693 compras anteriores
al 2020-09-09. Pasaron cuatro paridades iniciales, cuatro eventos, deduplicación,
conflicto, actualización, publicación idempotente y persistencia tras reinicio.
La fuente y el endpoint base permanecieron iguales. Duración registrada por el
reloj monotónico: 1593.043 s (26.55 min); **no es el tiempo total entre marcas
UTC**, como detectó la revisión siguiente, ni latencia de solicitudes. Picos registrados: semilla
692,932,608 bytes, API 800,714,752, UI 67,268,608, PostgreSQL 537,141,248.
Se conservaron todos los límites.

La suite independiente pasó 213 pruebas (62 unitarias, 28 serving, una de
publicación, diez eventos/UI y 112 host). El inventario local verificó 3,086
archivos intactos, incluido el intento fallido anterior y ledger; esas
comprobaciones se declaran separadas del ZIP. Sólo jobs se reconstruyó.
El resultado ya fue revisado en la Mac principal; T5/E4 siguen abiertas.
No se reintentó, entrenó, evaluó test ni inició T6.

## Revisión en la Mac principal (2026-10-22)

La [auditoría](../evidence/events-memory-review-mac24.json) valida el ZIP de
8,211 bytes, inventario, SHA-256 e identidad; confirma igualdad exacta con
`result`/`environment` recibidos y recalcula código científico, scripts y lock
del commit `44327b4`. También verifica la cadena T4-A y conserva intacto el ZIP
fallido original. El lector devuelve **1: fallo válido**, no ZIP corrupto.

Se recalcularon los nueve intervalos y se inspeccionaron los 45 predicados
ejecutados por el validador v1 sobre la entrada previa al fallo, sólo en memoria
y sin cambiar mediciones. La única condición falsa sigue siendo el pico
PostgreSQL inferior al límite. Las capturas completas, límites efectivos,
generaciones esperadas, cero swap/OOM y controles funcionales reportados
cumplen las demás comprobaciones. Se resolvió la atribución de fase:
`final-validation` identifica ahora componente, criterio y valores exactos.

El máximo heredado no explica por sí solo la actividad nueva: los contadores
`max` avanzan de 47,782 a 211,871 y la presión acumulada aumenta
1,679,380/1,679,363 µs (`some`/`full`). En las cuatro capturas dominan los bytes
`file` (467.9–493.9 millones); incluyen 147.1 millones de `shmem`. No se suman
ambas categorías ni se interpreta todo `file` como caché recuperable. La
[semántica del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files)
permite excesos transitorios de `memory.max` y define `memory.peak` como máximo
acumulado. El reporte confirma actividad cerca del límite, pero no determina
cuánta RAM adicional sería necesaria ni demuestra que esa actividad causara
una consulta lenta.

### Discrepancia temporal abierta

| Medición del ZIP | Valor |
| --- | ---: |
| Inicio UTC | 2026-10-22 18:49:56.268490 |
| Fin UTC | 2026-10-22 23:12:44.128825 |
| Diferencia UTC | 15,767.860 s — 4 h 22 min 48 s |
| Duración monotónica registrada | 1,593.043 s — 26.55 min |
| Diferencia entre ambas | 14,174.818 s — 3 h 56 min 15 s |

El problema aparece incluso dentro de `after_seed`: entre la captura UI y la
de PostgreSQL transcurren **879.425 s UTC frente a 0.341 s monotónicos**. Estas
observaciones de la misma fase no son contemporáneas. El código usa
`time.monotonic()` para duración y presupuestos; ese reloj no mide CPU ni es
intercambiable con las marcas UTC. Python permite inspeccionar su implementación
con [`time.get_clock_info`](https://docs.python.org/3/library/time.html#time.get_clock_info).
El ZIP no contiene esa información ni registros de energía/cambios de reloj.
Suspensión, ajustes de hora y efectos de la VM son hipótesis; ninguna queda
probada aquí.

Por tanto, esta corrida **no sirve para comparar rendimiento**, calcular
porcentajes de presión temporal o atribuir su diferencia frente a los 46.814 s
anteriores a PostgreSQL. Tampoco acredita una ejecución ininterrumpida dentro
de dos horas de tiempo total: el presupuesto de 7,200 s existente usa el reloj
monotónico. No se modifica retroactivamente ese criterio ni el estado del ZIP.
Los contadores y comprobaciones funcionales siguen siendo evidencia útil con
los límites descritos. No se midió latencia por solicitud ni carga T8.

### Siguiente paso propuesto, sin nueva ejecución

1. Obtener de mac24 un diagnóstico **en lectura** de versiones Python/macOS,
   implementaciones de reloj y eventos de reposo/despertar o cambios de hora
   acotados al intervalo UTC anterior. Mantener logs completos locales; publicar
   sólo un agregado mínimo y marcar evidencia ausente. No repetir el ensayo,
   tocar servicios ni reconstruir datos para producir esa información.
2. Preparar controles de continuidad y tiempo para una nueva versión: metadatos
   de reloj, tiempos UTC/monotónicos por operación, detección de incoherencia y
   protección contra reposo durante el proceso. Fijar la política de duración
   antes de medir y probar interrupciones/desajustes sintéticos localmente.
3. Definir explícitamente el contrato operativo de memoria de esa versión.
   Conservar límites efectivos, cero swap/OOM, funcionalidad y generaciones;
   mantener pico histórico, eventos `max` y presión como evidencia. Resolver
   su papel en la aceptación según la semántica del kernel, sin introducir una
   tolerancia calculada para aprobar estos 264 KiB ni reclasificar v1. Esto aún
   es una propuesta: ningún validador o umbral ha cambiado.
4. Sólo después de comprobar contrato/código localmente, preparar otro prompt
   de una corrida con commit exacto, PostgreSQL 512 MiB y los demás recursos
   actuales. Congelar criterios antes de ejecutarla; conservar originales y
   reportar también un fallo válido. No hay reintento automático.

Las 213 pruebas y preservación de 3,086 archivos son declaraciones remotas:
sus logs/inventarios privados no se transfirieron. Esta revisión principal
verificó reportes, fuentes y cálculos agregados; no ejecutó Docker ni recalculó
perfiles H&M. **T5/E4 permanecen abiertas; T6, entrenamiento y test no se
activan al recibir este reporte.**

El primer paso de seguimiento ya se ejecutó y fue [auditado](../evidence/events-clock-review-mac24.json): la [recolección de relojes/energía
en lectura](clock-diagnostics.md) quedó incompleta por límite de tamaño y su [prompt mac24](mac24-t5-clock-prompt.md) es histórico; no repetirlo. La causa permanece desconocida. Sigue integrar y comprobar el protocolo prospectivo antes de otra ejecución H&M.
Los controles temporales están probados como utilidades para la siguiente
versión; aún no se integraron al ensayo H&M ni se modificaron sus validadores
vigentes. La nueva integración debe verificarse antes de otra corrida.
