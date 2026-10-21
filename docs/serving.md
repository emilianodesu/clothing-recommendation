# T4-B: API, activación, reinicio y rollback

Esta entrega implementa el servicio de H y la comprobación reproducible de su
ciclo de versiones. La reproducción H&M en mac24 está pendiente; no equivale a
aceptación final ni a haber cumplido las metas de carga. Véase
[arquitectura y contrato HTTP](architecture.md).

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
La evidencia necesita revisión, incluida la limitación de acceso del host.
