# T8-B: comprobar capacidad antes de diseñar la carga concurrente

T8-A está cerrado. No repetir baseline, alineación API, T7 ni entrenamiento. Test permanece reservado.

## Resultado auditado y siguiente diseño

[Inventario revisado](../evidence/capacity-review-mac24.json): nueve contenedores; siete originales y dos de otro proyecto compatibles con la restauración T7. API/PostgreSQL/UI originales mantienen IDs e imágenes de T8-A. ZIP, huellas y decisión recalculados en la máquina principal. No repetir el inventario salvo cambio explícito de configuración.

| Concepto | GiB |
| --- | ---: |
| Docker efectivo | 13.621 |
| Límites del proyecto original | 12.25 |
| API/PostgreSQL del otro proyecto | 4.50 |
| Total de límites activos | 16.75 |
| Total con constructor aislado4GiB y margen1GiB | 21.75 |

`sequential_policy_required` rechaza ese constructor adicional con la configuración actual. No prueba consumo actual de 16.75GiB, OOM ni degradación empírica. No permite cerrar T8-B como una medición fallida.

**Ruta recomendada para el siguiente desarrollo:** aprovechar el constructor que ya ejecuta Airflow dentro del scheduler de5GiB/2CPU, en vez de añadir otro contenedor de4GiB. Una transición explícita que detenga únicamente el proyecto restaurado T7 (4.5GiB) y MLflow (1GiB), conservando contenedores/volúmenes y capturas previas, dejaría límites activos de11.25GiB. Con margen1GiB,12.25GiB caben en13.621GiB efectivos. El presupuesto del scheduler incluiría al proceso constructor; no se suman otros4GiB. Esto exige un protocolo nuevo y verificar límites reales, ausencia de otros jobs, fuente/configuración del constructor, guard de no activación y preservación de H. CPU seguiría compartida: el presupuesto de memoria no garantiza latencia.

Esa transición todavía NO está implementada ni ejecutada. Debe identificar el destino T7 exacto desde su reporte/configuración privada, nunca detener un proyecto arbitrario por llamarse other. Conservar API/PostgreSQL/UI originales, identidades, paquetes, datos y evidencia. Registrar el estado de auxiliares y su restauración posterior; no eliminar volúmenes, reconstruir imágenes ni cambiar cuotas para aparentar éxito. El baseline A incluye auxiliares activos: una comparación futura debe declarar ese cambio de contexto y no atribuir todas las diferencias exclusivamente al constructor.

**Alternativa secuencial si no se prepara un ensayo concurrente:** publicar una ventana de mantenimiento; drenar y detener API/UI originales explícitamente, conservar estado y contadores, ejecutar una única construcción H sin activación mediante Airflow, validar paquete y después recuperar el servicio con el mismo H. Las detenciones y arranques deben quedar registradas como nuevas generaciones. Este procedimiento también requiere preparar su transición de auxiliares para respetar el presupuesto; detener sólo API/UI no basta con todos los demás contenedores activos. La indisponibilidad sería duración de construcción más controles y reinicio, aún no medida. La frescura aumenta con la espera hasta la ventana y la duración de preparación; no prometer actualización continua ni latencia concurrente. No ejecutar esta alternativa desde esta guía.

El próximo desarrollo debe concretar una de esas rutas antes de producir un comando operativo. No hay otra ejecución pendiente de mac24; T8-C/test permanece reservado.

## Decisión de implementación

El objetivo pendiente es observar degradación al preparar un paquete H mientras se sirve el H vigente, sin activar el nuevo paquete. Antes de lanzar un trabajo necesitamos conocer el conjunto real de contenedores: original, servicios auxiliares y restauración T7. El compose original incluye una cuota de 5 GiB para Airflow scheduler; por tanto no basta con sumar API y PostgreSQL. La RAM libre de macOS tampoco equivale a capacidad disponible dentro de Colima.

El colector `scripts/mac24_t8_capacity.py` sólo consulta Docker, valida el ZIP auditado T8-A y escribe/publica un reporte agregado. No crea contenedores, modifica servicios, reconstruye imágenes ni inicia cargas. Lee campos seleccionados de inspect; nunca exporta variables de entorno, montajes, credenciales o nombres de proyecto. Los proyectos se representan mediante SHA-256 y scope original/other. Compara nuevamente el inventario y los límites al finalizar para detectar cambios durante la captura.

Política `capacity-v1`, fijada antes de la consulta:

- VM existente de 14 GiB/4 CPU; se utiliza la memoria efectiva que informa Docker.
- Candidato de construcción H aislado: 4 GiB/1 CPU/2 hilos. Es una propuesta de presupuesto, no un consumo medido ni una garantía de que el trabajo terminará. El pico de construcción T4-A fue 2.248 GiB; no se usa como máximo garantizado. No se cambia el perfil histórico mac24 de entrenamiento de 10 GiB/3 CPU.
- Margen de 1 GiB para VM/runtime, añadido a la suma de todos los límites de contenedores activos y los 4 GiB del candidato.
- Un límite de memoria cero significa sin límite, no consumo cero. Servicios desconocidos o sin límite requieren inspección antes de decidir.
- `fits_memory_budget`: la suma cabe bajo esa política; sólo permite diseñar el ensayo, no lo ejecuta ni acredita rendimiento.
- `sequential_policy_required`: no admitir automáticamente el candidato con la configuración actual. Preparar una política secuencial y explicar su efecto en frescura, o diseñar posteriormente una transición controlada de servicios. No detener restauraciones/auxiliares ni ampliar recursos automáticamente.
- `inspect_unknown_limits`: no hay un presupuesto cerrado que permita tomar la decisión.

Las cuotas de CPU conocidas y contenedores sin cuota se reportan por separado. No son CPU consumida ni reservas exclusivas. Esta comprobación sólo decide admisión conservadora de memoria; no garantiza aislamiento CPU ni mide latencia. Sumar límites es deliberadamente más conservador que observar memoria actual: que no quepan todos los máximos no demuestra un fallo empírico bajo concurrencia. La decisión no cierra T8-B ni E7.

## Comando mac24

Con SHA completo entregado y clon limpio, desde la raíz:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/mac24_t8_capacity.py --expected-commit SHA_COMPLETO
```

Guarda `capacity-mac24-*` en artifacts/handoff y publica el ZIP en GitHub privado. `passed` significa inventario válido, incluso si no se admite concurrencia. Devolver URL y decisión. Si la publicación falla, conservar el ZIP y usar sólo `scripts/github-handoff.py upload RUTA.zip`; no iniciar trabajos. Ante error sin reporte, devolver salida y conservar logs.

Siguiente acción principal: descargar, validar y revisar el inventario; concretar el ensayo o el procedimiento secuencial con los recursos observados. Ningún resultado de este comando autoriza entrenamiento, test o cambios en H.
