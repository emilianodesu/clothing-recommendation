# T5: ensayo operativo con continuidad temporal

`events-operational-v1` es una aceptación prospectiva y separada de `events-v1` y `events-memory-v1`. Conserva los tres reportes históricos fallidos y la causa desconocida de la discrepancia UTC/monotónico. No interpreta una captura de energía incompleta como ausencia de reposo. H sigue siendo historial28d+popularidad7d; no se entrena ni se abre test.

## Qué exige esta versión

- Mismas ventanas, corte y replay funcional: paridad del paquete, cuatro eventos, idempotencia, perfil actualizado, fuente inmutable y persistencia al reiniciar sólo API.
- Doce capturas por fase/servicio, límites efectivos exactos, cero swap/OOM capturados y generaciones esperadas. API cambia generación al reiniciar; UI/PostgreSQL la conservan. Descensos incoherentes de contadores invalidan aceptación.
- API 4 GiB/2 CPU; semilla 3 GiB/1 CPU/2 hilos; PostgreSQL y UI 512 MiB cada uno. VM mac24 14 GiB/4 CPU. No aumenta límites ni limpia picos/contadores.
- Guard de reposo supervisado y UTC/monotónico coherentes: máximo 2 s de diferencia por intervalo y acumulada, presupuesto total 7,200 s. Conserva versiones e implementaciones actuales de reloj.
- Limpieza del contenedor único de semilla y liberación del proceso propio de `caffeinate`; un fallo de limpieza impide el aprobado.

**Cambio de criterio explícito:** `memory.peak`, crecimiento del pico, eventos `max` y presión se conservan como diagnósticos; el pico histórico no decide el aprobado de esta versión. No se reemplaza el valor por uno menor ni se ajusta una tolerancia a los 264 KiB observados. Los validadores históricos continúan exigiendo `peak < limit` y los ZIP antiguos siguen fallidos. Este criterio comprueba funcionamiento con los recursos fijados; no certifica margen de memoria ni latencia/capacidad bajo carga, que corresponden a T8.

## Operaciones y relojes

Cada llamada Docker/Compose, captura cgroup y solicitud HTTP registra etiquetas fijas, índices de marcas, presupuesto, resultado y tipo de error. Se conservan UTC y monotónico incluso si divergen. Las marcas cubren semilla, muestras, replay, reinicio y finalización; cada captura se vincula al intervalo de su operación. No se exportan URLs con IDs, clientes, cuerpos, tokens, comandos ni inspect completo.

El presupuesto restante limita los subprocess y cada lectura HTTP. Compose usa hasta 1,800 s por llamada; HTTP hasta 600 s, UI 10 s y cgroup/inspect 30 s; el muestreo completo tiene 90 s. Reinicio API y su espera de readiness tienen límites de 180 s cada uno. Los intentos de readiness que fallan quedan registrados; sólo se toleran si luego se observa readiness correcto. No se reintenta la semilla ni el ensayo. La limpieza del contenedor propio conserva un timeout de 30 s aunque el trabajo ya haya fallado.

El host no puede ejecutar verificaciones mientras está suspendido. Una discrepancia se detecta al retomar y el reporte no puede quedar aprobado. Estos controles no son un watchdog externo ni una promesa de impedir cierre de tapa, apagado o suspensión forzada. `caffeinate -is -w PID` se libera al finalizar; `-s` sólo tiene efecto con alimentación AC. Mantén la Mac conectada, tapa abierta y terminal activa. La aserción sólo se soporta actualmente en macOS; esto limita este ensayo adicional, no los contenedores del proyecto.

`temporal.complete` requiere coherencia, guard iniciado/liberado, finalización y operaciones completas (salvo intentos de readiness explícitamente recuperados). El lector recalcula la coherencia y comprueba la traza; también valida fallos parciales. Un `passed` sigue requiriendo revisión en la Mac principal antes de cerrar T5/E4. No equivale a un benchmark ni a aceptación final de calidad.

## Ejecución reproducible

En un clon limpio con SHA completo, `.env` propio y servicios preparados:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project \
python3 scripts/project.py events-check \
  --machine-id mac24 --expected-commit "$T5_OPERATIONAL_COMMIT" \
  --source-run-id release-mac24-20261021T190717Z-f0a2d073 --operational
```

`--operational` y `--memory-diagnostics` son excluyentes. Sin estas opciones se conserva el procedimiento antiguo. Para una prueba sintética usa **un clon/proyecto propio**, puertos libres y `.env` generado allí:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima \
python3 scripts/project.py demo --operational --machine-id development
```

La demo prepara servicios de su propio proyecto; no ejecutarla en mac24 sobre la instalación H&M activa. El ensayo con `--source-run-id` reutiliza el paquete y servicios existentes y reinicia sólo API. No usa Qdrant/MLflow, SSH, Hopsworks o nube.

El lector/transporte de estos reportes se ejecuta con Python estándar en el host. El código científico, imágenes fijadas, Compose y sus límites no se modifican en esta entrega. La preparación remota reconstruye sólo `jobs` para ejecutar las pruebas actualizadas; conserva API/UI/PostgreSQL activos.

Salida 0 indica ensayo aprobado por el contrato nuevo; 1 o 130 indican fallo/interrupción con evidencia a conservar. Validar el ZIP anunciado con `scripts/read-handoff.py`: 1 significa fallo válido publicable, 2 paquete inválido que no se publica. Un corte abrupto puede dejar carpeta incompleta sin ZIP; no presentarla como éxito ni reintentar automáticamente. Logs y resumen son agregados deterministas, también ante fallo. El manifiesto del protocolo nuevo admite hasta 1 MiB para su traza de máximo 2,048 marcas; los protocolos anteriores conservan 128 KiB.

## Evidencia y siguiente ejecución

La evidencia local se registra en `evidence/events-operational-local.json`: pruebas y ensayo sintético se distinguen de la ejecución H&M pendiente. La guía mac24 está en [el prompt versionado](mac24-t5-operational-prompt.md). No cerrar T5/E4 ni iniciar T6 hasta descargar y auditar el nuevo reporte remoto.
