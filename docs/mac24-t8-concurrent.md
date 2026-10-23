# T8-B: carga con construcción H dentro del scheduler existente

Protocolo `scheduler-concurrency-v1`. T8-A y su ZIP permanecen cerrados; no repetirlos. La revisión capacity-v1 rechazó un constructor **adicional** de4GiB, no el trabajo dentro de la cuota existente de Airflow.

## Alcance y presupuesto

Una transición temporal detiene exclusivamente MLflow original y API/PostgreSQL de la restauración T7. Se exige identidad exacta desde el inventario auditado, PostgreSQL destino T7 y el proyecto de la configuración privada `.runtime/T7 restored installation airflow followup/.env`. Nunca se identifica un destino sólo por un nombre de servicio. No se borra, recrea, actualiza ni modifica ningún contenedor, imagen, volumen o cuota. Se guardan identidades y generaciones previas y posteriores; los auxiliares tendrán un nuevo started_at. API/PostgreSQL/UI originales y Airflow permanecen activos y deben conservar generación.

Quedan límites de11.25GiB, más margen1GiB, dentro de13.621GiB efectivos. El constructor comparte los5GiB/2CPU del scheduler con éste; no es otra asignación. Se conservan4CPU de VM y dos hilos de bibliotecas. Las cuotas de CPU son compartidas, no reservas exclusivas.

Se invoca una sola vez la función de construcción del pipeline instalada en el scheduler (fuente T6 SHA256 `5e0ba69395ee17158d4b823d63776dadc072a5559204b3101df00b348765d442`, commit `13af71c012cc8f0142e2b6eba111357160fb6f69` fijado explícitamente para procedencia del paquete). Este ensayo mide el constructor en su contenedor de ejecución, **no el tiempo de programación de un DAG**. Ambos DAG siguen pausados y se exige ausencia de dag_run running/queued. No se modifica la configuración T6 ni se dispara su DAG con reintentos. API conserva fuente T7; no hacen falta imágenes nuevas.

El constructor usa datos H&M verificados, corte2020-09-09 y evidencia Q4, con salida nueva en artifacts/concurrency-private. La función toma el bloqueo asesor del pipeline. Nunca se llama publish ni se activa el paquete. Su release_id puede diferir del H activo por procedencia; no se exige identidad de paquetes construidos con commits distintos. No entrena ni abre test. El proceso hijo tiene540s de presupuesto, se termina y, si hace falta, se mata tras10s adicionales; el scheduler no se reinicia. Los fallos/archivos parciales se conservan.

## Medición fijada antes del ensayo

Reutilizar exactamente las96 peticiones y referencias privadas de T8-A mediante su SHA256, sin regenerarlas. Consultarlas una vez antes de carga para verificar paridad. Calentar120s a20req/s y medir600s a20req/s. Al comenzar la medición se lanza el constructor en paralelo una sola vez. El generador conserva sus reglas:64 workers, timeout2s, retraso máximo50ms, omisiones cuentan como errores, percentiles por rango superior.

Se guardan agregados de las ventanas completas y una ventana de solapamiento definida por inicio/fin de la invocación host al worker. Incluye arranque de docker exec y recogida de resultados: no se presenta como tiempo exacto de cómputo. El tiempo interno de construcción debe representar al menos80% de esa ventana. Se exige solapamiento de al menos10s y200 llegadas programadas, y que termine dentro de la medición. Si la construcción termina demasiado pronto, el intento falla por evidencia insuficiente y **no se repite automáticamente**. Se cuentan también llegadas omitidas dentro de esa ventana; las respuestas que terminan después de ella siguen perteneciendo a su llegada. La tasa efectiva del solapamiento es completadas por duración de esa ventana, no una medida de capacidad máxima.

Aceptación tanto global de medición como de solapamiento: p95<200ms y errores+omisiones<1%. Discrepancia UTC/monotónico≤2s en calentamiento y medición. El resultado por segmentos se conserva. No ajustar umbrales tras los resultados.

Capturas cgroup cada10s para API, PostgreSQL, UI y scheduler: mínimo60 por servicio, cobertura≥720s, huecos≤35s, límites/identidades/generaciones estables, sin swap capturado ni crecimiento de swap.peak/OOM. Se conservan picos históricos, presión y eventos max sin reinterpretarlos. Un constructor breve puede quedar cubierto por pocas capturas; el pico cgroup acumulado se conserva, sin atribuirlo exclusivamente al constructor. CPU/maxRSS del generador abarcan todo el proceso.

**Comparación con A:** A tenía auxiliares activos; B los detiene temporalmente. La comparación A/B es entre configuraciones operativas distintas. El calentamiento B muestra el contexto reducido antes del constructor, pero no sustituye una campaña independiente sin trabajo. No atribuir causalmente toda diferencia A/B al constructor.

## Conservación y recuperación

El lanzador guarda reserva y estado privado en `.runtime/t8-concurrency-transition`. El bloqueo host evita dos trabajos pesados coordinados. Guarda estado agregado de BD antes/después, verifica H vigente, generaciones de servicios originales, DAG pausados y recupera auxiliares en orden PostgreSQL destino → API destino → MLflow, mediante start sobre los mismos IDs. Comprueba salud y readiness H del destino. No usa up, down, rm ni migraciones.

Al terminar o ante fallo controlado intenta recuperar auxiliares. Si no hay constancia de que el worker terminó, no los arranca sobre un constructor posiblemente vivo. Ante pérdida de SSH, esperar al menos diez minutos y ejecutar únicamente el modo de recuperación con el mismo commit:

```bash
python3 scripts/mac24_t8_concurrent.py --restore-only --expected-commit SHA_COMPLETO
```

Ese modo exige la reserva, los mismos auxiliares, el bloqueo libre y el archivo de finalización del worker si se llegó a lanzar; no ejecuta carga ni construcción. Si se detiene, conservar logs y pedir revisión, sin borrar la reserva ni repetir. Un corte abrupto puede dejar un reporte incompleto, que no acredita éxito. La recuperación posterior queda en un archivo privado separado y no reescribe un ZIP cerrado.

## Ejecución única mac24

AC, tapa y Terminal abiertas. No usar demo, enviar eventos, modificar contenedores ni iniciar otros trabajos. Con clon limpio y SHA completo entregado:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/mac24_t8_concurrent.py --expected-commit SHA_COMPLETO
```

Revalida ZIP T8-A, capacity-v1 y T7; imágenes/límites de los nueve contenedores; hardware, alimentación, DAG pausados, fuente API/scheduler, H vigente y suite host antes de reservar. Reporte cerrado `concurrency-mac24-*` publicado por GitHub privado, también ante failed válido. Si sólo falla publicación, subir únicamente el ZIP con github-handoff.py. Cohortes, paquetes nuevos, configuración y logs privados permanecen locales. No ejecutar T8-C/test por recibir passed: revisar primero el ZIP y la recuperación de auxiliares.
