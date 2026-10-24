# Guía del evaluador y demostración

**Actualización:** el comando de [aceptación sintética](acceptance.md) ya está implementado; su ejecución Docker integral sigue pendiente de un motor dedicado con capacidad. La preparación anterior que lo describe como inexistente es histórica. No ejecutar sobre el motor mac24 ocupado.

## Recorrido sin ejecutar trabajos (10–15 minutos)

Esta ruta sirve para revisar la entrega actual sin modificar mac24. No necesita Kaggle ni abrir test. Los reportes son evidencia de ejecuciones anteriores, no una ejecución nueva.

1. Leer [informe](final-report.md) y [ficha H](model-card.md). Explicar por qué RG tiene mayor MAP pero H satisface el conjunto de restricciones.
2. Mostrar [cobertura](course-coverage.md): grafo comercial, PageRank acotado y componentes de recomendación; distinguir los subtemas no implementados.
3. Mostrar [T8-C](../evidence/final-review-mac24.json): población, ventana, H/P, recompra frente a descubrimiento y recursos. No abrir artefactos individuales.
4. Mostrar [T8-A](../evidence/benchmark-review-mac24.json) y [T8-B](../evidence/concurrency-review-mac24.json). Diferenciar latencia observada y concurrencia sostenida no acreditada.
5. Mostrar [T7](../evidence/portability-review-mac24.json): respaldo privado, credenciales nuevas, paridad y operación offline. Explicar qué quedó fuera del respaldo.
6. Cerrar con [pendientes reales](delivery-status.md), sin presentar el informe como certificación de todos ellos.

## Demo interactiva sintética en una instalación independiente

Para una persona nueva, seguir primero [setup](setup.md) y [bootstrap](bootstrap.md). Se necesita Git con acceso propio, Python 3.9+ y Docker/Compose disponible. No se necesitan bibliotecas científicas en el Python del host ni Kaggle. Usar un clon limpio separado del H&M operativo, con su propia configuración, almacenamiento y puertos libres, siguiendo [events](events.md). No copiar `.env` o volúmenes del autor.

Antes de iniciar otra instalación en mac24 hay que comprobar presupuesto total: la VM ya aloja originales y restauración. **No lanzar esta demo junto a esos servicios como siguiente comando automático.** Tampoco repetir los adaptadores `mac24_t8_*`.

Cuando el entorno independiente esté preparado y tenga capacidad:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
# Seleccionar el contexto real preparado para esta demo, no copiar un socket.
export DOCKER_CONTEXT=colima
python3 scripts/doctor.py
python3 scripts/project.py demo
```

`demo` construye y prepara sintéticos, activa un paquete y produce un reporte de eventos. Es una operación con escrituras, no una consulta de salud. Si falla, conservar el reporte antes de decidir otra ejecución. En macOS un perfil propio puede tener otro nombre de contexto; en Linux se usa el motor local preparado.

Una vez anunciado el éxito, abrir la dirección UI de esa instalación (por defecto `http://127.0.0.1:8501`). Mostrar:

| Paso | Acción | Observación esperada |
| --- | --- | --- |
| Recomendación | Seleccionar comprador | Lista y motivos, versión/corte visibles; fallback cuando corresponda |
| Evento | Seleccionar artículo y registrar compra | Confirmación de escritura; no asumir que el primer artículo cambiará |
| Publicación | Pulsar **Actualizar recomendaciones** | Recomendaciones del stream para su reloj de simulación |
| Explicación | Comparar historial y respaldo | Compra estrictamente anterior al corte; vistas/clics no alimentan compras |
| Evidencia | Abrir el ZIP agregado con `scripts/read-handoff.py` | Inventario/huellas/estado; sin compradores en el paquete público |

La idempotencia y conflicto se acreditan mediante el ensayo HTTP y pruebas, no pulsando dos compras nuevas: dos UUID distintos son eventos diferentes. No simular corrupción, caída o rollback sobre H&M durante una presentación. Usar la evidencia T4/T7 para explicar recuperación; cualquier nueva demostración de restore debe usar otro destino y presupuesto explícito.

## Capturas o video pendientes

Grabar sólo la demo sintética: arranque confirmado, recomendaciones, evento confirmado y actualización. Para recuperación puede mostrarse el reporte auditado, etiquetado «evidencia histórica», sin fingir una restauración en vivo. Evitar terminales con tokens, `.env`, identificadores reales de clientes y rutas privadas. Registrar commit, fecha, entorno y qué acciones se ejecutaron. Las capturas todavía no existen como entregable final de T8-D.

## Comandos que no forman parte de esta demo

No ejecutar `mac24_t8_final.py`, reentrenamientos Q1–Q4, benchmarks históricos, `prune`, `down -v` ni limpieza de reservas. El CLI ofrece `verify --suite acceptance` para un motor dedicado; su ensayo integral está pendiente. No ofrece `project.py benchmark`; usar los reportes auditados de carga, sin repetirlos. La demo no sustituye esa aceptación final.
