# T4-B: API, activación, reinicio y rollback

Esta entrega implementa el servicio de H y la comprobación reproducible de su
ciclo de versiones. **T4-B está cerrada tras revisar el seguimiento H&M v2 de
mac24**, con acceso desde localhost, identidad, reinicio y rollback comprobados.
No equivale a aceptación final ni a haber cumplido las metas de carga. Véase
[arquitectura y contrato HTTP](architecture.md).

La corrección **v2** añade una red de publicación exclusiva para API y comprueba
HTTP desde el host. Su reproducción H&M en mac24 pasó y está
[auditada](../evidence/serving-host-review-mac24.json). El ensayo v1 conserva sus
resultados; ambos procedimientos y prompts son históricos, no deben repetirse
salvo petición explícita. El siguiente desarrollo del plan es T5.

## Evidencia local

El clon limpio `881e0c2` pasó 121 pruebas científicas/servicio (incluidas 28 de
integración T4-B). Tras corregir también el apagado en `d926c11`, pasaron
79 de host y ocho de bootstrap Docker: 208 pruebas distintas en total.
La integración externa MLflow quedó omitida en la suite científica; no hubo
cambios en tracking. `verify --suite serving` pasó desde las imágenes sin montar
código del host. La revisión final detectó dos fallos, ambos corregidos con
regresiones observadas fallar y pasar: opciones de CLI y pérdida de propietario
PostgreSQL entre comprobación y escritura. Al limpiar el ensayo se corrigió
además `down` para incluir todos los perfiles Compose; se comprobó que elimina
API/red y conserva el volumen PostgreSQL.

El ensayo `serving-dev-20261021T194554Z-65bdaa1d` pasó con dos paquetes sintéticos
de 24 artículos y 32 compradores históricos: cinco comprobaciones de 41
solicitudes cada una, incluyendo filtros y un comprador desconocido. Duró
11.615 s, con pico API de 140,636,160 bytes y sin swap/OOM. Conservó las huellas
de respuestas tras reinicio y rollback. El [reporte privado](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-serving-dev-20261021T194554Z-65bdaa1d)
se publicó y verificó por descarga; [evidence/serving.json](../evidence/serving.json)
conserva identidades, recursos, revisión y decisiones. Estos resultados no
acreditan capacidad con H&M ni latencia bajo carga.

## Arranque local

Usar un clon limpio con la revisión recibida y configuración local preparada por
T1. En mac24 conservar Colima `hm-project` 14 GiB/4 CPU. No cambiar sus recursos ni
el ledger Q4; el presupuesto de API es distinto del perfil de jobs `mac24`.
Mantener el prefijo SSL y `DOCKER_CONTEXT=colima-hm-project` en los comandos.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project docker compose --env-file .env --file compose.yaml build app-check jobs
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite serving
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py up --profile serve --skip-build
```

`verify` exige la imagen reconstruida: ejecuta unitarias, integración PostgreSQL,
HTTP real y host. `up` aplica migraciones sin modificar las anteriores, inicia
PostgreSQL/API y espera liveness. Sin versión activa, readiness y recomendaciones
devuelven 503. `API_PORT` en `.env` permite elegir un puerto libre; no regenerar
credenciales ni copiar `.env` de otra Mac.

Para uso manual, las rutas son relativas al `ARTIFACTS_DIR` configurado:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py prepare-release --directory data-jobs/RUN_T4A/work/release
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py activate-release --release-id h_SHA256_COMPLETO
curl -sS http://127.0.0.1:8000/health/ready
curl -sS http://127.0.0.1:8000/recommend -H 'Content-Type: application/json' -d '{"customer_id":"unknown-demo","k":12}'
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py rollback-release
```

La preparación devuelve `release_id`; usar ese valor exacto. Rollback requiere
dos versiones previamente activadas. Esos comandos manuales ilustran operación;
para el ensayo mac24 usar directamente el comando siguiente, que controla orden
y recoge evidencia sin mostrar recomendaciones individuales.

## Ensayo mac24 con reportes cerrados

**Procedimiento histórico, ya ejecutado.** No repetirlo para resolver el acceso
del host: falta corregir y ampliar esa comprobación. Véase la revisión al final.

Conservar paquetes T4-A originales, datos/features/checkpoints, Q1–Q4, ZIP y
ledger. Guardar inventario privado de originales antes/después. Se reutilizan:

- Referencia sintética: `release-mac24-20261021T190539Z-02a850f8`.
- Candidato H&M: `release-mac24-20261021T190717Z-f0a2d073`.

Ambos ZIP deben estar en `artifacts/handoff` o `artifacts/incoming` y sus paquetes
en `artifacts/data-jobs/RUN_ID/work/release`. El comando verifica cada archivo
contra el reporte T4-A. Si faltan paquetes, detenerse: los ZIP no los sustituyen.
No repetir Q1–Q4 ni reconstruir H&M para eludir faltantes.

Después de reconstruir, verificar e iniciar el servicio, ejecutar **una vez**:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py serving-check --machine-id mac24 --expected-commit SHA_COMPLETO --reference-run-id release-mac24-20261021T190539Z-02a850f8 --candidate-run-id release-mac24-20261021T190717Z-f0a2d073
```

Es un comando **en primer plano**: mantener la terminal/agente y el equipo
despiertos hasta el cierre. No tiene `resume` ni reintento automático. El límite
total es 7,200 s, con operaciones individuales acotadas. La secuencia es:

1. Validar clon, código de API/probe, paquetes y ZIP T4-A.
2. Preparar/activar demo y comprobar HTTP.
3. Preparar/activar H&M y comprobar HTTP.
4. Reiniciar el contenedor API, recuperar H&M desde PostgreSQL y comprobar HTTP.
5. Hacer rollback a demo y comprobar HTTP.
6. Reactivar H&M, comprobar HTTP y registrar recursos; H&M queda sirviendo.

Rechaza sustituir una versión activa ajena a esos dos paquetes. La comprobación
usa hasta 128 compradores con historial reciente, 128 conocidos sin historial
reciente y uno desconocido; añade casos filtrados. Compara todos los campos
deterministas contra el paquete y exporta sólo agregados/huellas. No evalúa
calidad ni mide carga sostenida. Los contadores de requests y tiempos del probe
son diagnóstico, sin afirmar p95 bajo 20 solicitudes/s.

El ZIP anunciado usa `serving-handoff`; valida igualdad tras reinicio/rollback,
versión final, código y límites efectivos de 4 GiB/2 CPU/2 hilos, sin swap/OOM.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

Exporta y publica también fallos válidos: lector 1 indica ejecución fallida;
lector 2 indica ZIP inválido y bloquea publicación. Un fallo deja el servicio en
el estado alcanzado; inspeccionarlo antes de cualquier nueva operación. No
reintentar ni cambiar recursos/código para ocultarlo. Un corte abrupto puede
dejar sólo la carpeta incompleta; conservarla y reportar el corte, sin fabricar
un resultado cerrado. No borrar volúmenes ni paquetes para recuperar.

Tras finalizar, actualizar `evidence/mac24-serving-t4b.json` con commit, run_id,
Release, versiones, muestra, paridad, recursos, duración y estado final. Registrar
suite y preservación como comprobaciones locales de mac24. Hacer commit/push
después del cierre; no marcar aceptación final ni iniciar T5/test.

## Detener y recuperar

`python3 scripts/project.py down` detiene el proyecto conservando volúmenes y
paquetes. `up --profile serve --skip-build` recupera el puntero persistido. Si
se pierde la sesión de base o hay confirmación incierta, reiniciar la API
recupera el estado confirmado. Si la versión está corrupta, conservar evidencia
y restaurar sus archivos desde una copia verificada; no editar hashes para
hacerla pasar. El contrato de backup/restore portátil se implementará en T7.

El servicio no necesita datos crudos ni Qdrant/MLflow en ejecución; inicialmente
cada Mac conserva sus propios paquetes. Los reportes por GitHub no contienen
los paquetes de servicio, transacciones, perfiles, predicciones ni credenciales.

## Resultado mac24 (2026-10-21)

Ensayo único `serving-mac24-20261021T200054Z-cce4d43e` en
`d1898dd3e471f208c814888250915b42150e842d`: **passed**, reporte validado y
[publicado con verificación por descarga](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-serving-mac24-20261021T200054Z-cce4d43e).
La [evidencia mac24](../evidence/mac24-serving-t4b.json) registra las fuentes
T4-A originales, versiones, hashes, muestras y recursos.

Las imágenes app-check/jobs se reconstruyeron; suite serving: 60 unitarias,
28 de integración y 79 host aprobadas (167; un aviso). El ensayo hizo 877
solicitudes: demo/rollback 41 cada una, H&M/reinicio/final 265 cada una.
H&M muestreó 257 compradores; todos los campos deterministas coincidieron,
incluidos filtros. Las huellas se conservaron tras reinicio y rollback.
Duración: 24.693 s. Pico API observado: 1,000,263,680 bytes, sin swap/OOM,
con límites efectivos 4 GiB/2 CPU/2 hilos. No es una medición de carga T8.

H&M quedó activo y readiness dentro del contenedor confirmó su identidad.
**Limitación posterior al cierre:** el host no pudo conectar a
`http://127.0.0.1:8000/health/ready`. Docker registra el binding solicitado
127.0.0.1:8000, pero `NetworkSettings.Ports` devuelve `8000/tcp: null`.
El ensayo usa HTTP interno y su resultado no prueba accesibilidad desde el host.
La causa raíz queda pendiente de diagnóstico de publicación Docker/Colima;
no se cambió configuración ni se reinició/reintentó para ocultar el resultado.

Los 3,065 archivos inventariados, incluidos paquetes originales y ledger,
permanecen iguales. Calidad final y test siguen pendientes; no se inició T5.
La evidencia ya fue [auditada en la Mac principal](../evidence/serving-review-mac24.json).

## Revisión y bloqueo del acceso local

El ZIP coincide exactamente con el resultado/environment versionado, commit,
huella de código, lock y ambos reportes T4-A previamente revisados. Los conteos,
versiones, límites y huellas tras reinicio/rollback son coherentes. El resultado
sintético también coincide con el ensayo de la Mac principal. Las 167 pruebas
y preservación de 3,065 originales son declaraciones de mac24, cuyos inventarios
y archivos privados no se transfirieron.

La revisión histórica v1 identificó dos pendientes: la API sólo se conecta a `backend`,
declarada `internal: true`, y `serving-check` no prueba la ruta desde el host.
Un experimento mínimo independiente en la Mac principal (Docker 29.5.2) comparó
el mismo servidor HTTP e imagen con redes internal/bridge: ambos respondieron
dentro del contenedor; internal dejó `8000/tcp: null`, mientras el control bridge
publicó un puerto en localhost y respondió HTTP 200 desde macOS. Se eliminaron
sólo los contenedores y redes desechables. Esto reproduce el mecanismo local;
la corrección posterior y su reproducción mac24 revisada se documentan abajo.

**Estado al revisar v1: T4-B abierta.** La corrección v2 descrita abajo conserva PostgreSQL en
su red privada y verifica publicación, readiness, recomendaciones e identidad
desde el host tras reinicio/rollback; su reproducción mac24 ya fue revisada.
El reporte v1 conserva su resultado válido de HTTP interno.
No iniciar T5, repetir campañas ni abrir test para resolver este fallo de red.

## Corrección v2 y comprobación desde el host

La API participa en backend privada y `api-publication` bridge, publicada sólo
en `127.0.0.1:${API_PORT}`. PostgreSQL permanece exclusivamente en backend sin
puertos publicados. `verify --suite serving` incluye una instalación desechable
con el Compose real: exige HTTP desde macOS, binding loopback y backend privada
para PostgreSQL. Sus propios volúmenes se eliminan; no toca los del proyecto.

El contrato `history-serving-v2` conserva las cinco muestras internas y añade
`host_checks` para referencia, candidato, reinicio, rollback y estado final.
Cada check del host verifica publicación efectiva, `/health/live` y código,
`/health/ready` y versión, y dos recomendaciones: comprador desconocido sin
filtros y con un filtro vacío. Compara el digest con la referencia calculada
desde el paquete por el probe interno. No instala librerías científicas en macOS
ni envía perfiles privados; sólo guarda agregados y huellas. El cliente usa
127.0.0.1, ignora proxies y rechaza redirecciones.

Una comprobación host ausente, fallida o de otra versión impide `passed`.
Los reportes v1 siguen siendo legibles y sólo acreditan HTTP interno. No se
convierten ni editan los ZIP anteriores. Los tiempos siguen siendo un diagnóstico
secuencial, sin medir p95 ni 20 solicitudes/s. Para H&M se esperan 877 consultas
internas de recomendaciones y diez adicionales desde el host; ninguna usa test.

En un clon limpio con el SHA recibido, mantener `.env` y paquetes originales:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project docker compose --env-file .env --file compose.yaml build app-check jobs
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite serving
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py up --profile serve --skip-build
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py serving-check --machine-id mac24 --expected-commit SHA_COMPLETO --reference-run-id release-mac24-20261021T190539Z-02a850f8 --candidate-run-id release-mac24-20261021T190717Z-f0a2d073
```

`up` recrea la API cuando cambia su imagen/red y recupera el puntero existente;
no borra volúmenes. Si preparación/verificación falla, conservar diagnóstico y
detenerse. El ensayo sigue siendo una única ejecución en primer plano con
presupuesto de 7,200 s. Validar/publicar también un fallo cerrado mediante
`read-handoff.py` y `github-handoff.py upload`. No reintentar automáticamente.
No regenerar datos ni modificar recursos para ocultar fallos. Conservar inventario
privado de originales antes/después, incluido el ZIP v1 y ledger.

Al finalizar registrar `evidence/mac24-serving-host-t4b.json`, separado de la
evidencia v1, y actualizar esta guía. El [prompt v2](mac24-t4b-host-prompt.md)
reúne las instrucciones; no usar el prompt histórico ni iniciar T5/test.

### Verificación local de v2

La [evidencia](../evidence/serving-host.json) registra 216 pruebas distintas
aprobadas: 121 científicas en imagen sin montajes de código, 86 de host, ocho de
infraestructura Docker y una regresión de publicación con Compose real. La prueba
externa de MLflow sigue omitida; la de publicación se ejecutó desde el host.
La revisión independiente del cambio no encontró defectos accionables.

El ensayo limpio `serving-dev-20261022T030938Z-a1c0f662`, commit
`9ef2ddba975ac028cf97a8364db7e9cf9cf750bb`, pasó con dos paquetes sintéticos:
205 consultas internas y diez desde localhost, cinco etapas, reinicio/rollback
y huellas correctas. Duró 10.894 s, sin swap/OOM. El ZIP se publicó y verificó
por descarga privada. Esto acredita la corrección local; no sustituye el ensayo
H&M v2 en mac24 ni mide carga T8.

## Resultado mac24 v2 (2026-10-21, America/Mexico_City)

Ensayo único `serving-mac24-20261022T032542Z-59d9bfe4`, protocolo
`history-serving-v2`, en `58c408e829b1809c5edd95bb6d10ebe219bb8ac2`: **passed**.
El run_id usa UTC. [Reporte validado y publicado](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-serving-mac24-20261022T032542Z-59d9bfe4);
[evidencia agregada](../evidence/mac24-serving-host-t4b.json).

Se reconstruyeron app-check/jobs y pasaron 175 pruebas: 60 unitarias, 28 de
integración, una regresión Docker de publicación desde el host y 86 de host.
Hubo avisos de invariantes sparse de PyTorch y ResourceWarning de Python en la
prueba que rechaza redirecciones; ninguna prueba falló.

Los paquetes T4-A originales se reutilizaron sin cambios. Cinco etapas comprobaron
877 recomendaciones internas y diez desde macOS, con paridad, versiones y código
correctos. El binding efectivo fue exclusivamente 127.0.0.1:8000; readiness,
reinicio y rollback pasaron. H&M quedó activo; una consulta adicional de readiness
desde macOS confirmó la misma versión al cierre. Esto resuelve en esta ejecución
la limitación de acceso del host observada en v1; su reporte permanece intacto.

Duración 20.607 s; pico API 1,090,260,992 bytes (1.015 GiB), sin swap/OOM.
Límites API efectivos 4 GiB/2 CPU/2 hilos, con Colima 14 GiB/4 CPU. La suite
y la preservación local de 3,072 archivos, incluido ledger y reporte v1, se
registran por separado del ZIP. Sólo se publicaron agregados y huellas.

La recepción inicial quedó pendiente de revisión; la auditoría siguiente registra
su cierre. `quality_accepted=false` y `test_evaluated=false` se conservan. No mide
carga T8 ni inicia automáticamente el desarrollo T5.

## Revisión principal y cierre T4-B

La [auditoría v2](../evidence/serving-host-review-mac24.json) valida inventario,
tamaños, SHA-256 y contrato del ZIP; concilia exactamente la evidencia versionada
y el entorno, código/lock del commit ejecutado y ambos paquetes T4-A originales.
Las cinco huellas de respuesta interna coinciden con v1; las cinco del host
coinciden con las referencias calculadas desde el paquete. La referencia
sintética también coincide con el ensayo local v2. Las versiones se mantienen
correctas tras reinicio y rollback; H&M quedó activo en el estado final reportado.

**T4B-01 y T4B-02 resueltos; T4-B cerrada operativamente.** El puerto efectivo
y las solicitudes reales desde el host están incluidos en el reporte v2. El ZIP
v1 conserva su SHA y resultado original. La revisión no repitió trabajos, cambió
H ni evaluó test. La muestra host cubre dos casos de comprador desconocido por
etapa; el muestreo interno añade compradores conocidos y filtros. No representa
una prueba exhaustiva de todas las solicitudes ni de carga sostenida.

Las 175 pruebas independientes, la preservación de 3,072 archivos y la consulta
posterior de readiness son declaraciones separadas de mac24. No se transfirieron
su inventario privado ni paquete H&M; aquí se verificaron reportes/huellas, sin
recalcular recomendaciones privadas ni conectarse al servicio remoto.

No hay otra ejecución mac24 pendiente. Sigue T5 del plan: interfaz, eventos
idempotentes y actualización temporal de perfiles; T6–T8 conservan operación,
recuperación, carga y aceptación final. Conservar los originales y ambos prompts
como procedimientos históricos.
