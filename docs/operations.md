# Operación local T6

T6 añade Airflow 3.3.2 con LocalExecutor: API, scheduler y procesador de DAG. No instala Celery/Redis, no monta el socket Docker y no entrena. El runtime de Airflow vive en `/opt/airflow-env`; las tareas usan `/opt/hmrec/.venv/bin/hmrec`, el mismo paquete y lock del motor. Constraints oficiales y hashes de ambas resoluciones quedan en Git. [Arquitectura oficial](https://airflow.apache.org/docs/apache-airflow/3.3.2/core-concepts/executor/local.html).

## Preparación explícita

Parte de un clon limpio, `.env` propio y PostgreSQL ya iniciado. `pipeline init` no lo arranca ni recrea; aplica la migración aditiva 004 al esquema de aplicación e inicializa la base `hm_airflow` ya provisionada. Conserva datos, credenciales, servicios y el paquete seleccionado. Construye jobs antes de Airflow, porque la imagen de Airflow incorpora el entorno de trabajos local. Para desarrollo, usa un proyecto Compose y puertos propios: 8000 puede estar ocupado.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline init
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite pipeline --existing-services
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline up
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline status
```

El API de recomendaciones debe actualizarse a esta versión para exponer las métricas; sólo `mac24_t6.py` automatiza esa sustitución controlada de API tras las pruebas. No recrea PostgreSQL ni UI. Los volúmenes y originales permanecen. Los perfiles base `/recommend` no se modifican. Una migración fallida detiene la preparación; no borres tablas ni repitas automáticamente un ensayo.

Airflow publica su UI sólo en `127.0.0.1:${AIRFLOW_PORT}`. Usuario `admin`; contraseña local en `ARTIFACTS_DIR/airflow/simple_auth_manager_passwords.json`, permisos privados. No subir ese archivo ni sus logs. Las claves de ejecución se derivan con propósitos distintos de la credencial local; no se copian entre equipos. SimpleAuthManager se limita a esta instalación local; no exponer esta UI en Internet.

Presupuestos: scheduler+tarea 5 GiB/2 CPU; API Airflow 768 MiB/0.5 CPU; procesador DAG 512 MiB/0.5 CPU. API H conserva 4 GiB/2 CPU, UI/PostgreSQL 512 MiB. VM mac24 14 GiB/4 CPU. Los límites de los contenedores no prometen que todos puedan alcanzar sus máximos simultáneamente; comprobar picos, swap y OOM. Un pool global de un slot, parallelism=1 y bloqueo advisory PostgreSQL serializan las tareas T6. El ensayo host también usa el bloqueo de lanzamiento de trabajos; no ejecutar entrenamientos manuales a la vez.

## Construcción y publicación manual

`hm_build_release` no tiene calendario. Procesa `/artifacts/pipeline/build.json`, preparado localmente, con campos `dataset_dir`, `output_dir`, `data_as_of`, `synthetic`, `activate` y opcionales `source_zip`, `evidence_archive`. Las rutas deben pertenecer a `/data` o `/artifacts`; el corte T6 permanece **2020-09-09**. No usa test. Si hay ZIP fuente, llama a la ingesta idempotente T2; si ya existe dataset verificado, lo reutiliza. Construye y verifica el paquete H usando T4. La tarea siguiente sólo publica si `activate=true` estaba presente al construir. Un recibo liga ejecución exacta de Airflow, configuración, manifiesto y versión: editar el JSON exige reconstruir antes de publicar. Los archivos finales se publican atómicamente; los reintentos conservan particiones verificadas. Una construcción fallida no habilita la tarea de publicación.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline trigger hm_build_release
```

En mac24 el ensayo usa `activate=false`: materializa una nueva versión auditable sin reemplazar H activo ni invalidar el stream T5. La activación automática se comprueba con sintéticos aislados. La selección del motor, ventanas y desempates permanecen congelados.

## Perfiles, reintentos y recuperación

Ambos DAG nacen pausados. Registra explícitamente **un stream ya sembrado** de la versión activa; no se siembra H&M cada cinco minutos ni se seleccionan todos los streams históricos. La fecha de simulación queda fija. Sólo se procesan eventos recibidos antes del watermark elegido. Perfil y cursor se confirman en la misma transacción y con el bloqueo de stream que usan las inserciones de eventos. Un fallo revierte ambos.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline register --stream-id UUID_DEL_STREAM
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline enable-refresh
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py pipeline pause
```

`hm_refresh_profiles` se programa cada cinco minutos, `catchup=false`, un run activo y un reintento un minuto después. Cada ejecución consume como máximo una ventana de recepción de cinco minutos y 1,000 eventos. Salta huecos sin eventos; conserva el backlog para ejecuciones siguientes sin una tormenta de trabajos atrasados. Si más de 1,000 eventos comparten exactamente la misma marca, falla sin adelantar el cursor. El agregado T5 todavía reconstruye tablas del stream: no promete actualización incremental ni frescura menor de diez minutos hasta medirla con H&M.

Los trabajos de construcción/refresh escriben JSON agregados en `artifacts/pipeline/reports`, incluyendo duración, filas/eventos, error de tipo y RSS. Airflow conserva estado y número de reintentos. Las tareas publican sólo conteos y versión, sin clientes en XCom (`do_xcom_push=false`). `/metrics` añade histograma de latencia HTTP, códigos por clase y contador de respaldo para `/recommend`; no tiene etiquetas de cliente, stream ni URL libre. Los contadores son locales al proceso y se reinician con API: no son métricas durables ni benchmark T8.

## Ensayo y evidencia

`pipeline-check` exige preparación completa, clon limpio y SHA exacto. Comprueba el DAG de construcción, verifica el paquete, registra dos compras idempotentes en el stream, observa dos ejecuciones **programadas** y detiene/reinicia sólo el scheduler para comprobar un evento pendiente. Compara `/recommend` antes/después, captura recursos de seis servicios antes, inmediatamente antes/después del reinicio y al finalizar (24 capturas con generaciones y contadores), y pausa ambos DAG al terminar, también ante fallo controlado. No borra eventos ni paquetes. No se reintenta el ensayo automáticamente.

El contrato `pipeline-v1` exige frescura recepción→publicación menor de 600 s para ambos eventos; si se excede, conserva el fallo. Guarda un ZIP agregado con inventario/huellas, identidad del build exacto, fuente/corte/Q4 y comparación del código y DAG instalados en los tres servicios Airflow contra el clon. La suite y el inventario privado de originales se reportan por separado. Un corte abrupto puede dejar sólo carpeta/log: no interpretarlo como aprobado ni volver a lanzar. Un DAG pausado puede tener una tarea aún en ejecución; inspecciona el estado antes de detener procesos.

El [procedimiento mac24](mac24-t6-prompt.md) ya se ejecutó y es histórico. T6/E5 están cerradas tras la revisión principal descrita abajo. T7, T8, evaluación final y carga siguen pendientes.

## Validación local del 23 de octubre

[Reporte sintético](../evidence/pipeline-local.json): `pipeline-development-20261023T054340Z-360a4df3`, commit `9ec0d67b435dc75f5efa708949d0b06b2ba71d85`. Ensayo de 89.021 s, dos ciclos programados, frescura 3.611 s y 65.850 s, 24 capturas sin swap/OOM capturados. Se verificaron build exacto, código/DAG instalados, idempotencia, reinicio, PostgreSQL y `/recommend` inmutables, y pausa final. La fase de construcción/publicación sintética previa duró 4.832 s y está ligada en el ZIP. El pico del scheduler fue 502,562,816 bytes; no extrapolar estos valores a H&M.

Pasaron las suites pipeline y bootstrap; después, 175 pruebas host y el ensayo congelado cubrieron las correcciones del lanzador. La evidencia separa commits y conjuntos de pruebas. Dos ensayos previos fallaron antes de sembrar por incompatibilidades del recolector y del helper HTTP; sus ZIP permanecen publicados, sin reinterpretarlos. La revisión estática resolvió las observaciones de generaciones, procedencia del build y código instalado. Esa evidencia local precede a la revisión H&M descrita abajo.

## Reinicio de Colima antes de T6 (23 de octubre)

La VM `hm-project` estaba detenida, conservando 14 GiB/4 CPU/100 GiB. El usuario inició el mismo perfil. El contenedor e imagen PostgreSQL coinciden con T5; su nuevo arranque es `2026-10-23T19:15:26.34851292Z`. La API conserva H y el corte 2020-09-09. Las consultas aportadas por el usuario coinciden con revisión 3, cuatro eventos procesados/almacenados, 972,591 filas semilla y 1,069,693 compras entre 2020-08-12 y 2020-09-08. [Registro y límites](../evidence/postgres-recovery-mac24.json).

`pipeline-v2` registra esa transición explícitamente. El adaptador sólo acepta la pareja exacta de identidades registrada en `pipeline_recovery.py` y el ZIP T5 con su huella aprobada. Antes de reservar T6 repite las consultas parametrizadas en una transacción de sólo lectura y snapshot consistente; vuelve a comprobarlas antes de los eventos. Si cambia el arranque, contenedor, imagen, fuente o cualquier agregado, se detiene. No hay flag para ignorar reinicios, ni se borran reservas. El caso sin cambio de generación conserva la comprobación estricta original.

El ZIP v2 conserva `postgres_transition` y usa las capturas nuevas como inicio del intervalo T6. No resta contadores entre generaciones ni transforma los ceros posteriores al reinicio en una conclusión sobre T5. Los ZIP v1/T5 permanecen intactos y legibles. Estos controles no certifican igualdad fila por fila. La prueba sintética anterior valida el flujo T6; las pruebas nuevas validan esta continuación, no una corrida H&M.

Seguridad: consultas de sólo lectura con parámetros y timeout, salida limitada a agregados e identidades, sin credenciales ni clientes. La excepción sólo aplica al reinicio auditado; cambios distintos se rechazan.

## Revisión principal H&M, 23 de octubre

[Auditoría](../evidence/pipeline-review-mac24.json) de `pipeline-mac24-20261023T193510Z-532c902f`: ZIP verificado de 6,441 bytes, código/runner/DAG recalculados desde Git y fuente T5 comprobada. T6/E5 se aceptan bajo `pipeline-v2`. Dos compras simuladas, idempotencia, dos ciclos programados y recuperación de evento pendiente tras reiniciar scheduler; frescura 30.465/276.191 s, por debajo de 600 s. Ensayo de 327.475 s. Construcción H vinculada a ejecución/recibo y fuentes T2/Q4 congeladas, sin activar el paquete nuevo. H anterior y recomendaciones base conservados; ambos DAG pausados.

Las 24 capturas conservan límites y generaciones; cero swap/OOM capturados. PostgreSQL registra 73,327 eventos `max` nuevos y pico 536,879,104 bytes (8 KiB sobre 512 MiB). Se acepta funcionamiento bajo el protocolo operativo, no margen de memoria ni carga sostenida. El cambio de generación previo de Colima se documenta por identidad y agregados; no se comparan contadores con T5. La recuperación no acredita igualdad fila por fila.

La suite pipeline con salida cero, 3,380 archivos preservados y capacidad VM son declaraciones del adaptador mac24. Aquí se verificaron reportes, huellas y contratos; no se recibieron tablas privadas ni el paquete completo. La prueba de dos eventos no demuestra una garantía general de frescura. No repetir T6; siguen T7 y T8 a solicitud, manteniendo test reservado.

## Respaldo y recuperación T7

Consultar [reproducibilidad](reproducibility.md) para backup, restauración aislada y controles offline. El ensayo sintético pasó; H&M requiere [una ejecución mac24](mac24-t7-prompt.md) y revisión. No se modifica el servicio original ni se reanuda Airflow automáticamente en el destino.
