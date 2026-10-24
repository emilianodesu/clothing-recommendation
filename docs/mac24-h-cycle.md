# Validación de un ciclo real de H — h-cycle-v1

La solicitud «continua con la validacion» autoriza implementar y ejecutar una
nueva observación del ciclo real de H con la API bajo carga. No vuelve a ejecutar
el lanzador T8-B ni sustituye su resultado: `scheduler-concurrency-v1` permanece
failed por no alcanzar 10 segundos y 200 llegadas solapadas. Este protocolo
responde a una pregunta más acotada: ¿cómo respondió la API durante **una**
construcción real, aunque dure pocos segundos? No acredita concurrencia sostenida.

## Alcance y criterios fijados antes de ejecutar

- Mac24 Apple Silicon 24 GiB, Colima hm-project efectivo 13–14 GiB y 4 CPU.
- API 4 GiB/2 CPU, PostgreSQL 512 MiB, UI 512 MiB/0.5 CPU, scheduler 5 GiB/2 CPU.
  Se conservan identidades, configuración, paquetes y contadores históricos.
- Se detienen únicamente MLflow original y API/PostgreSQL restaurados T7,
  por sus IDs verificados. Se recuperan esos tres contenedores al terminar.
  Los otros seis servicios continúan activos. Presupuesto: límites restantes
  11.25 GiB más 1 GiB de margen, sin contenedor adicional.
- Construcción única dentro del scheduler existente, con dos hilos,
  estado al 2020-09-09 y `activate=false`, salida nueva. Sin entrenamiento,
  evaluación test, activación, eventos ni cambio de modelo.
- Cohorte privada de 96 casos previamente congelada en T8-A, comprobada por hash.
  HTTP por localhost desde el host: 120 s de calentamiento y 600 s de medición,
  20 solicitudes/s, 64 trabajadores como máximo, timeout HTTP 2 s y retraso
  máximo de lanzamiento 50 ms. El constructor se inicia una vez al comenzar
  la medición. No se prolonga artificialmente ni se repite para llenar la ventana.
- Medición global y solapamiento conservador: p95 <200 ms y errores más omisiones
  <1%. Como criterio **nuevo y descriptivo**, al menos 20 llegadas programadas
  en el intervalo conservador. Con menos y sin otros incumplimientos, resultado
  `inconclusive`, sin reintento; un error medido conserva `failed` aunque haya
  pocas observaciones.
  Veinte observaciones no estiman con precisión la cola poblacional: se publican
  siempre el tamaño y la duración; no hay afirmación estadística de sostenibilidad.
- Recursos: muestreo cada 10 s y al cierre, límites/generaciones iguales,
  cero swap observado y cero nuevos OOM, conservando picos/max históricos.
  El constructor registra también memoria/peak/eventos al inicio y al final.
  Muestreo de host no resuelve picos de pocos segundos por sí solo.

## Relojes y evidencia

Los relojes monotónicos del host y de la VM tienen orígenes distintos. Antes y
después del constructor, tres consultas acotan el offset: `host_before - vm`
y `host_after - vm`. Se toma la consulta con menor RTT por extremo y la envolvente
de ambos extremos, con 100 ms adicionales de margen por lado. Se rechaza ancho
superior a 1 s o desplazamiento de puntos medios superior a 100 ms.

El hijo marca monotónico y UTC inmediatamente antes/después de `build()`;
exige diferencia UTC/monotónico ≤100 ms. El intervalo conservador en host es
`[inicio_vm + offset_max, fin_vm + offset_min)`. El intervalo posible usa los
extremos inversos. Se cuentan llegadas **programadas**, incluyendo omisiones,
en ambos; p95 incorpora espera desde la llegada programada. Esto presupone
deriva interior acotada por el margen observado, no sincronización perfecta.
Se guardan los brackets y las marcas para auditoría. No se afirma que todas las
respuestas terminaron dentro de la construcción: se mide la latencia completa
de las solicitudes cuya llegada cae dentro del intervalo.

El reporte conserva duración interna, duración de la llamada completa,
intervalos posible/conservador, métricas por segmento y globales, recursos,
hashes de código/cohorte/antecedentes y estado agregado antes/después. Trazas
publicables sólo contienen tiempos relativos, segmento y éxito, sin solicitudes,
respuestas, clientes, paths privados ni credenciales. ZIP + SHA-256 van a una
Release privada de GitHub; logs, cohortes, salida H y estado de recuperación
permanecen en mac24. No se modifican evidencias ni reservas anteriores.

## Ejecución y recuperación

`scripts/mac24_h_cycle.py --expected-commit SHA_COMPLETO --preflight-only`
comprueba checkout, antecedentes y salud sin reservar, pausar ni cargar.
Sin esa opción, repite el preflight bajo el bloqueo de trabajos y crea una
reserva exclusiva `.runtime/h-cycle-v1`. Sólo admite una ejecución.

La comprobación de inventario parte del despliegue UI aceptado, no de IDs de UI
anteriores. Se comprueban códigos API/scheduler, DAG pausados sin tareas,
readiness H, estado agregado y reserva T8-C intacta. Se verifica capacidad antes
de detener y nuevamente antes de construir. Mantener AC, tapa abierta y Terminal.

El hijo tiene timeout 540 s y cierre acotado. Ante desconexión/señal, se espera
su registro de salida antes de recuperar auxiliares, sin lanzar otro constructor.
`--restore-only` recupera sólo esta reserva si acredita hijo terminado o nunca
lanzado; no ejecuta carga. Si falta esa evidencia, conserva todo para diagnóstico.
Se intenta recuperar cada auxiliar aunque falle otro; la recuperación requiere
salud conjunta y readiness T7. La publicación fallida no autoriza repetir carga:
`--publish-only` vuelve a publicar el ZIP cerrado sin modificar el ensayo.

El resultado es `failed` ante incumplimiento operativo/latencia, `inconclusive`
ante muestra insuficiente y `passed` sólo para el ciclo observado. Requiere
revisión principal antes de atribuirlo a la entrega. T8-B histórico y la
limitación de concurrencia sostenida permanecen intactos en los tres casos.
