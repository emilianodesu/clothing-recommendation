# Validación de un ciclo real de H — h-cycle-v1

**Cerrada tras revisión principal (24 de octubre):** [auditoría](../evidence/h-cycle-review-mac24.json).
`h-cycle-mac24-20261024T204022Z-10bcd520` aceptado para un único ciclo breve.
No volver a ejecutar carga, `--publish-only` ni `--restore-only`. Los comandos
de preparación y publicación siguientes se conservan como antecedentes.

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
Release de GitHub con la visibilidad del repositorio (público o privado);
logs, cohortes, salida H y estado de recuperación
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

## Continuación de publicación del 24 de octubre

El operador reportó `h-cycle-mac24-20261024T204022Z-10bcd520` como passed en
`a3858fd56fb945f78f8893e181d3b8cd2b8782e7`. Falló después de cerrar el ZIP,
al comprobar que el repositorio fuera privado. El usuario cambió el repositorio
a público y autorizó continuar sin esa restricción. La publicación posterior
terminó y el ZIP ya se descargó y auditó en la Mac principal; esta sección
conserva el procedimiento histórico que resolvió el bloqueo.

Actualizar al commit de la corrección y ejecutar **sólo**:

```bash
python3 scripts/mac24_h_cycle.py --expected-commit SHA_NUEVO --publish-only
```

Este modo verifica checkout limpio, que el commit original sea antecesor del
actual, SHA-256 del ZIP, inventario y coincidencia de origen/ejecución/estado.
Publica los bytes ya cerrados y mantiene la Release vinculada al commit original.
No lee `.env`, no consulta Docker, no requiere alimentación AC, no inicia carga
ni modifica la reserva. Admite repositorios públicos y privados; conserva la
comprobación del repositorio de destino y las verificaciones de integridad.

Si existe una subida parcial, comprueba los adjuntos presentes y sube únicamente
los faltantes. No borra ni reemplaza adjuntos. El mensaje passed del lanzador
no sustituye la revisión principal, completada a continuación.

## Resultado revisado

El ZIP de 395,998 bytes, SHA-256
`89cf1092e45bb0d14f385159469440103ca0a193b408dce79d1cfb65a5e783f8`,
coincide con el checksum y los metadatos de GitHub. El tag apunta al commit
original `a3858fd`, aunque la publicación se completó con la corrección posterior.
La huella del runner y la configuración de construcción se recalcularon desde Git.

| Medida | Resultado |
| --- | --- |
| Ventana principal | 600 s a 20 solicitudes/s, 12,000/12,000 correctas |
| Errores / omisiones | 0 / 0 |
| p95 / p99 global | 14.151 / 15.203 ms |
| Construcción interna | 6.821 s; envoltura instrumentada 6.823 s |
| Intervalo conservador | 6.580 s, 132 solicitudes correctas, 44 por segmento |
| p95 conservador | 8.909 ms |
| Intervalo posible | 7.065 s, 141 solicitudes correctas |
| Incertidumbre de calibración | 242.535 ms |
| Capturas de recursos | 296: 74 por API, PostgreSQL, UI y scheduler |
| Pico scheduler | 2.451 GiB de 5 GiB; incluye Airflow y constructor |
| Swap / nuevos OOM / nuevos eventos max | 0 observados / 0 / 0 |

Se recalcularon cuentas, cuantiles y segmentos desde las 12,000 filas de la
traza, además de los intervalos de reloj y contadores. El pico histórico de
PostgreSQL, 536,879,104 bytes frente a 536,870,912, permanece sin crecimiento:
no se declara margen de memoria. Los hashes agregados antes/después coinciden.
El runner reporta recuperación saludable de PostgreSQL/API restaurados y MLflow;
sus identidades completas y las tablas privadas no se exportaron. La conservación
de reservas y paridad de respuestas privadas son declaraciones del runner.

Se acepta el ciclo breve observado. No se activó el nuevo paquete H, no hubo
entrenamiento ni evaluación test. **T8-B histórico conserva failed y la
concurrencia sostenida sigue sin acreditarse.** No se infiere que construir H
mejore la latencia: las ventanas comparadas tienen duraciones y muestras distintas.
