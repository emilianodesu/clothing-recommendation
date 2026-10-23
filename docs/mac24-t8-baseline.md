# T8-A: carga baseline de H sin entrenamiento

T7/E6 están cerradas bajo el alcance autorizado; no crear otra cuenta ni ejecutar os_user_check. Este ensayo abre T8-A, no T8 completo. No evaluar test, entrenar, activar otra versión, reiniciar servicios o repetir T5–T7.

## Diagnóstico vigente tras el error genérico

La ejecución en 27e87fe informó `ValueError` sin etapa ni reporte cerrado. La causa todavía no está comprobada. No volver a ejecutar la carga ni eliminar reservas. Con el nuevo SHA entregado, ejecutar sólo:

```bash
python3 scripts/mac24_t8_baseline.py --preflight-only --expected-commit SHA_COMPLETO
```

Este modo imprime cada comprobación y `FALLO: etapa (tipo)` sin revelar el contenido de excepciones. Ejecuta pruebas host y guarda sus logs privados en `.runtime/t8-preflight-*`; ante errores muestra únicamente nombres de pruebas. Comprueba alimentación, guard de reposo, Docker, servicios y antecedentes; no reserva un ensayo, prepara cohortes ni envía carga. No cambia servicios ni criterios. Si existe una reserva anterior, se detiene y la conserva. Devolver la salida para revisión antes de autorizar el siguiente comando de carga. Un preflight correcto no acredita rendimiento.

## Protocolo fijado antes de medir

`configs/benchmark.yaml` (JSON compatible con YAML) fija load-baseline-v1:

- API H activa al corte2020-09-09, publicada sólo en localhost. Una conexión HTTP nueva por petición; no TLS, no navegador ni tráfico externo. La latencia incluye TCP/HTTP local y virtualización Colima.
- Cohorte privada determinista:32 compradores con historial,32 conocidos sin historial,32 desconocidos. Semilla42, selección sobre listas ordenadas, alternancia equilibrada de los tres segmentos. No es una muestra ponderada por tráfico real ni valida rendimiento para todo el catálogo de usuarios.
- Referencias completas desde el paquete inmutable; comprobar IDs, orden, motivos, fallback, corte e identidad excluyendo sólo request_id. Cada respuesta bajo carga debe coincidir. Nunca publicar cohortes o respuestas.
- Primero96 consultas secuenciales de **primer contacto**. El servicio sigue activo desde etapas anteriores: no equivalen a arranque en frío ni caché fría del servidor. La prueba de arranque frío queda fuera de T8-A.
- Calentamiento120s a20solicitudes/s (2,400slots), luego medición600s a20solicitudes/s (12,000slots). El drenado de solicitudes pendientes separa ambas ventanas y se registra en elapsed_seconds.
-64 workers como máximo; timeout HTTP2s. Más de50ms de atraso del generador o falta de slot libre cuenta como solicitud omitida, sin recuperarla con ráfagas. `scheduled = completed + omitted`; errores más omisiones usan el total programado como denominador. HTTP fallido, respuesta inválida o paridad incorrecta son errores.
- p50/p95/p99 por rango más cercano superior. Latencia principal desde la llegada programada hasta respuesta; service_seconds separa el tiempo desde entrada al worker. Se conservan errores en la distribución de latencias completadas; las omisiones se penalizan en error_fraction. Tasa efectiva usa duración real hasta drenar, no sólo600s nominales. Resultados globales y por segmento.
- Aceptación principal: p95 **<200ms** y errores+omisiones **<1%** en medición; discrepancia UTC/monotónico **≤2s** en ambas fases. No ajustar umbrales tras observar resultados.

## Recursos y preservación

Conservar VM14GiB/4CPU, API4GiB/2CPU, PostgreSQL512MiB, UI512MiB/0.5CPU. DAG pausados, sin jobs/entrenamiento/campañas simultáneos. Servicios auxiliares ya activos (incluida restauración T7) permanecen intactos; baseline significa sin trabajo pesado, no máquina vacía. El lanzador rechaza contenedores con nombres ajenos a los servicios previstos. No puede probar ausencia de carga de otras aplicaciones del host: cerrar aplicaciones personales intensivas.

Capturas cgroup aproximadamente cada10s, mínimo60 por servicio, intervalo máximo35s y cobertura≥720s. Identidad/generación estables, CPU acumulada, límites, memoria actual/pico, swap y eventos OOM/presión. No reiniciar contadores ni reinterpretar picos históricos. Cero swap actual en muestras, sin aumento de swap.peak/OOM y límites congelados son controles; muestras no prueban ausencia absoluta de actividad entre ellas. Se conserva la etiqueta técnica legacy before_seed del lector cgroup; para carga se usa elapsed_seconds, sin etapa de semilla.

CPU del generador y maxRSS abarcan el proceso completo (incluida preparación), no sólo los600s. Los valores cgroup son contadores, no porcentajes de CPU instantáneos; la revisión calcula deltas sobre tiempos capturados. El lanzador verifica código de API/jobs, identidad de PostgreSQL desde T7, readiness, huella de estado agregado antes/después y DAG pausados. La huella de estado incluye punteros/cursores/conteos, no un hash fila por fila de toda la BD.

## Ejecución única en mac24

AC, tapa y Terminal abiertas; no usar la demo ni enviar eventos. Clon limpio y SHA completo recibido:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/mac24_t8_baseline.py --expected-commit SHA_COMPLETO
```

El lanzador ejecuta las pruebas host antes de reservar, reutiliza imágenes/servicios y prepara sólo una cohorte privada (sin entrenamiento ni test). Reserva `.mac24-t8-baseline-once.json` en artifacts/handoff. No construye ni recrea API/PG. Mantener la configuración sin cambios durante el ensayo.

Reportes `benchmark-mac24-*` cerrados se publican automáticamente como Releases privados; sólo agregados, huellas y contadores. Cohorte, referencias y logs de fallos permanecen en artifacts/benchmark-private. Conservar el ZIP incluso failed. Si falla antes de reservar puede no haber ZIP. Ante fallo no repetir ni borrar reserva. Si falla sólo publicación, subir únicamente el ZIP cerrado con github-handoff.py. Devolver URL/run_id para revisión principal. `passed` no cierra T8.

## Después de la auditoría

1. T8-B: diseñar ensayo concurrente acotado con preparación de estado H, sin activación ni reentrenar PyTorch; comparar degradación y política secuencial si los recursos no bastan. Medir arranque frío por protocolo separado si corresponde, conservando identidad histórica.
2. T8-C: evaluar H y P una sola vez sobre test[2020-09-16,2020-09-23), estado anterior al16. Congelar evaluación/denominadores antes de ejecutar; ninguna elección o ajuste mirando test.
3. T8-D: informe, evidencias de aceptación, mapa del temario y guion de demo. No afirmar cobertura completa del tema5 ni superioridad absoluta de H.

Los pasos B–D no se activan automáticamente por recibir el reporte A.
