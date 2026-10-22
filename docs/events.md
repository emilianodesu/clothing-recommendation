# T5: interfaz, eventos y perfiles operativos

H sigue siendo historial28d+popularidad7d. T5 incorpora Streamlit, eventos durables y actualización de perfiles en PostgreSQL. El paquete inicial y `/recommend` permanecen congelados; cada entorno tiene un `stream_id` y usa `/streams/{stream_id}/recommend`. El intento H&M fue revisado y conserva estado fallido por memoria PostgreSQL; T5/E4 siguen abiertas. No confundir la implementación local con cierre remoto o aceptación T8.

## Demo reproducible sin Kaggle

Usa un clon limpio del repositorio con Docker/Compose preparado. No necesita credenciales Kaggle, pesos, Qdrant, MLflow ni paquetes Python científicos en el host. Usa Python 3.9+ y tus credenciales Git propias.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima python3 scripts/project.py demo
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima python3 scripts/project.py verify --suite events
```

En mac24 el contexto es `colima-hm-project`; Linux puede usar su contexto Docker local. `demo` genera `.env` propio, construye imágenes bloqueadas, aplica migraciones, inicia PostgreSQL/API/UI, genera sintéticos, prepara y activa H, inicia un entorno separado y ejecuta el recorrido HTTP. Rechaza un árbol con cambios y no reemplaza una versión ajena ya activa. Para probarla junto al servicio H&M usa otro clon con puertos libres; no copies `.env` ni volúmenes. `--skip-build` sólo es apropiado si las imágenes corresponden al código; el ensayo verifica su huella.

Al terminar, abre [la interfaz](http://127.0.0.1:8501). Selecciona cliente y artículo, registra una compra y pulsa **Actualizar recomendaciones**. La fecha visible es un reloj de simulación: se consideran compras estrictamente anteriores a ella. Vistas y clics se conservan sin alimentar el historial de compras. La API está en `127.0.0.1:8000`; ambos puertos son configurables en `.env`. PostgreSQL no publica puerto. `python3 scripts/project.py down` detiene servicios conservando datos.

## Contratos y reloj

`POST /events` recibe `event_id` UUID, `stream_id`, `release_id`, `customer_id`, `article_id`, `kind` (`purchase`, `view`, `click`), `origin` (`synthetic` o `simulation`) y `event_time` con zona horaria. Devuelve **201** por inserción, **200** por repetición exacta, **409** por ID/payload o versión incompatible y **503** cuando no se confirmó la escritura. Tras 503 se reintenta el mismo UUID y payload; el servidor puede haber confirmado la transacción antes de perder la respuesta. La interfaz conserva ambos en su sesión hasta recibir confirmación. Una pestaña cerrada pierde ese estado de sesión, aunque no los eventos ya guardados.

La semilla usa conteos diarios de los últimos 28 días, estrictamente anteriores al corte del paquete. Se comprueban el SHA del manifiesto T2 y el de la fuente. Los datos posteriores de H&M nunca se incorporan a la simulación, aunque se avance su reloj: sólo se añaden sus interacciones explícitas. El origen se determina por la huella de H&M, no por el nombre del directorio. No se mezcla este entorno con las tablas de evaluación Q1–Q4/test.

`received_at` lo asigna PostgreSQL después de adquirir el bloqueo del entorno; `event_time` determina el día UTC de la compra. `aggregate_events(release_id, received_before, stream_id=..., data_as_of=...)` reconstruye conteos, historial y popularidad en una transacción. El límite inferior de 28/7 días es inclusivo; el corte superior es exclusivo. La fecha y el cursor de recepción nunca retroceden. Un evento futuro queda registrado y entra al avanzar el reloj; uno vencido no resucita el historial. Repetir la misma publicación no incrementa revisión. Un fallo conserva la publicación anterior y permite reintentar.

Una solicitud lee metadatos, popularidad e historial en una sola instantánea PostgreSQL. Las reglas H, filtros antes de top200, desempates y k≤12 son los mismos que T4. Una compra no necesariamente cambia el primer resultado: depende del historial existente. La prueba usa un comprador sintético nuevo para observar el cambio de fallback a historial personal.

`POST /admin/streams/{id}/refresh` exige Bearer local y `{ "data_as_of": "2020-09-09" }`. La interfaz envía la credencial desde su servidor, sin exponerla al navegador. La instalación es local, de un operador; no implementa autorización por comprador ni un servicio multiusuario público. El perfil `serve` inicia PostgreSQL/API/UI; programar actualizaciones pertenece a T6.

## Imágenes y recursos

Ante 409 u otro rechazo permanente, la interfaz deshabilita el reintento e indica **Cerrar este intento**. Esta acción sólo descarta el payload pendiente de esa sesión; no revierte un evento ya guardado. Ante 503 o pérdida de respuesta, mantiene el mismo UUID/payload y permite reintentar. Estos controles siguen disponibles aunque cambie la versión o falte un entorno activo.

La API consulta sólo el JPEG solicitado dentro del ZIP local de Kaggle; no extrae todo el archivo. Usa ruta canónica de artículo, rechaza duplicados/tamaños excesivos y devuelve una imagen de sustitución cuando no existe. La caché temporal elimina los menos recientemente usados y está limitada por `HMREC_IMAGE_CACHE_BYTES` (64 MiB por defecto, máximo 512 MiB). Se pierde al recrear el contenedor. La demo sintética usa sustituciones.

| Componente | RAM máxima | CPU | Observación |
| --- | ---: | ---: | --- |
| API | 4 GiB | 2 | Dos hilos, estado H precargado |
| PostgreSQL | 512 MiB | Sin cuota CPU | Eventos, semilla y perfiles persistentes |
| Streamlit | 512 MiB | 0.5 | Sólo HTTP hacia API |
| Semilla `events-job` | 3 GiB | 1 | Dos hilos; datos/artefactos montados en lectura |
| Construcción de sintéticos `jobs` | 5 GiB | 3 | Sólo en `demo`; secuencial |

Mac24 conserva Colima 14 GiB/4 CPU. Los 3 GiB de semilla consideran el pico previo T4-A de 2.248 GiB, pero deben medirse con H&M. La publicación reconstruye el entorno completo y bloquea temporalmente sus inserciones; las lecturas siguen viendo la versión anterior. No se declara procesamiento incremental ni baja latencia bajo carga hasta medir T8. No ejecutes entrenamientos o campañas en paralelo.

## Ensayo H&M y devolución de evidencia

El [prompt mac24 T5](mac24-t5-prompt.md) conserva el procedimiento ya ejecutado; no repetirlo. Se reutilizó el paquete full activo y su dataset local; los ZIP de reportes no sustituyen esos artefactos privados. La instrumentación y el [nuevo prompt diagnóstico](mac24-t5-memory-prompt.md) están preparados; consulta [su contrato](events-memory.md).

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py up --profile serve
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite events
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py events-check \
  --machine-id mac24 --expected-commit SHA_COMPLETO \
  --source-run-id release-mac24-20261021T190717Z-f0a2d073
```

`events-check` es de primer plano: mantener la terminal y el equipo despiertos. Crea un entorno nuevo por intento; no borra ni avanza otros. Comprueba paridad inicial para hasta tres compradores existentes y uno nuevo, una compra, un reintento, un conflicto, una vista, un clic, una compra tardía, publicación repetida, preservación de `/recommend`, acceso desde localhost, salud UI y persistencia tras reiniciar API. La automatización UI con pérdida de respuesta se prueba aparte con Streamlit AppTest y HTTP/PostgreSQL reales. Su estado saludable por sí solo no demuestra interacción de navegador.

Guarda `events-...zip` bajo `artifacts/handoff/`, también ante un fallo controlado. Contiene agregados, huellas, recursos y etapas; nunca compradores, solicitudes, perfiles, imágenes ni modelos. Un cierre abrupto puede dejar una carpeta incompleta: conservarla e informar, sin asumir éxito ni relanzar automáticamente. El máximo del protocolo es 7,200 s, cada llamada HTTP hasta 600 s y preparación de semilla hasta 1,800 s. Un fallo conserva los datos operativos para diagnóstico; no modifica límites para hacerlo pasar.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

La Mac principal descarga el run publicado con `github-handoff.py download RUN_ID` y valida el ZIP antes de cerrar T5. No hay SSH ni activación automática del otro agente. La selección H y test siguen intactos.

Referencias de implementación: [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest) y [estado de sesión](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state). Versiones exactas en `uv.lock`.

## Verificación local

Dos ensayos desde clones limpios terminaron `passed`, con configuración PostgreSQL propia y sin Kaggle. El último (`events-development-20261022T042237Z-5238dc2c`, código `6130943641e599121119660693ed276cf3b3fbca`) comprobó cuatro interacciones, deduplicación/conflicto, publicación repetida y persistencia tras reiniciar API en 4.263 s, excluyendo construcción y arranque. Son sintéticos, no una medición de capacidad H&M. La [evidencia agregada](../evidence/events-local.json) identifica cada revisión y reporte.

Pasaron 133 pruebas científicas/integración, 93 del host, ocho de bootstrap Docker y una de publicación API/UI (235 casos distintos); la prueba separada de MLflow no se ejecutó. `verify --suite events` también pasó contra la imagen final instalada. Chrome mostró las 12 recomendaciones y controles; la compra/reintento/conflicto se verificó con AppTest y HTTP/PostgreSQL reales. La revisión independiente tuvo dos correcciones comprobadas RED→GREEN y no dejó menores aplazados. [Decisiones y límites de revisión](t5-decisions.md). La reproducción H&M permanece pendiente.

## Intento H&M mac24 (2026-10-21, America/Mexico_City)

Una única ejecución en `ee27bf7911ff38d15aae66922d716cec7426b9ab`:
`events-mac24-20261022T043907Z-ddcd2e42` (run_id UTC), **failed**.
[ZIP válido publicado y verificado por descarga](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-events-mac24-20261022T043907Z-ddcd2e42);
[evidencia agregada](../evidence/events-mac24.json). El lector devuelve 1:
fallo de ejecución válido, no ZIP inválido. No se reintentó.

La suite events pasó 194 pruebas: 62 unitarias, 28 de integración serving,
una de publicación, diez de eventos/UI y 93 host. La semilla del paquete H&M
T4-A original incorporó 972,591 filas diarias y 1,069,693 compras, del
2020-08-12 al 2020-09-08. Cuatro solicitudes de paridad inicial y cuatro
eventos completaron duplicado 200, conflicto 409, cambio de perfil, exclusión
de vistas/clics, orden tardío y publicación idempotente. El endpoint base y
la fuente permanecieron iguales; persistencia tras reinicio fue verdadera.

La validación final lanzó ValueError porque el pico acumulado PostgreSQL
fue 537,141,248 bytes, frente a su límite 536,870,912; el contrato exige
pico estrictamente inferior. La fase reportada sigue siendo restart aunque
la comprobación de persistencia ya había pasado. El contador es de la vida
del contenedor existente: no se atribuye todo ese pico exclusivamente a T5.
Sin swap ni OOM; no se aumentaron límites ni cambió código para aprobar.
Duración registrada 46.814 s. Picos: semilla 700,850,176 bytes, API
763,113,472, UI 67,051,520. Los recursos efectivos completos están en la evidencia.

Inventario local antes/después: 3,079 archivos intactos, incluidos originales,
reportes históricos y ledger. API H&M y [UI](http://127.0.0.1:8501) quedaron
saludables y disponibles; se conservó el entorno simulation para diagnóstico.
El siguiente paso es revisar el contrato y la evidencia de memoria en la Mac
principal. T5 no está cerrada; no se entrenó, evaluó test ni inició T6.

## Revisión en la Mac principal

La [auditoría del intento H&M](../evidence/events-review-mac24.json) verifica el ZIP cerrado de 4,013 bytes, sus huellas e inventario, el código `ee27bf7`, `uv.lock` y la cadena hacia el reporte T4-A original. `read-handoff.py` devuelve 1, como corresponde a un fallo válido. El resultado y el entorno publicados en Git coinciden exactamente con el ZIP. Al reconstruir sólo en memoria la entrada al validador antes del fallo, la única condición falsa es la comparación del pico PostgreSQL contra el límite. No se modificó ni reclasificó el reporte original.

| Componente | Pico reportado, MiB | Límite, MiB |
| --- | ---: | ---: |
| Semilla | 668.383 | 3,072 |
| API | 727.762 | 4,096 |
| UI | 63.945 | 512 |
| PostgreSQL | 512.258 | 512 |

El exceso es de 270,336 bytes (264 KiB; 0.0504%). Linux permite que `memory.max` se exceda temporalmente; `memory.peak` representa la vida del cgroup salvo reinicio explícito del contador para su descriptor. Por ello, el exceso no demuestra que Docker haya ignorado el límite. `memory.events:max` cuenta aproximaciones al límite, no procesos eliminados por OOM. [Documentación oficial del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files).

Eso tampoco demuestra que 512 MiB sean suficientes con margen. Mac24 declaró posteriormente 45,963 eventos `max` y cero `oom`/`oom_kill`; son contadores acumulados fuera del ZIP, sin medición inicial para atribuirlos a T5. El ZIP sólo registra cero swap y `State.OOMKilled=false`. No contiene desglose de caché, memoria anónima, presión ni eventos por fase. El origen temporal de esa presión queda sin resolver.

Las cuatro comprobaciones iniciales de paridad, cuatro eventos y persistencia tras reiniciar están completas en el reporte. La fase `restart` es imprecisa: el código había terminado la persistencia cuando falló la validación final. Los 46.814 s incluyen semilla y reinicio; no son latencia de recomendación. Las 194 pruebas y preservación de 3,079 archivos son declaraciones de mac24: aquí no se recibieron sus inventarios privados ni se recalcularon perfiles H&M. La salud de UI tampoco sustituye una prueba de navegación completa.

### Siguiente paso de ingeniería

Preparar instrumentación y pruebas antes de una nueva ejecución remota:

1. Capturar identidad de contenedor/cgroup, generación de reinicio, límites efectivos y tiempos por fase. Registrar `memory.current`, pico acumulado, swap y `memory.events` antes/después de semilla, replay y reinicio; calcular diferencias sólo dentro del mismo cgroup.
2. Añadir agregados de `memory.stat` y `memory.pressure` para distinguir caché, memoria anónima y presión. No exportar filas SQL, clientes ni secretos; no reiniciar PostgreSQL sólo para borrar el historial del contador.
3. Separar `final-validation` de `restart` y guardar componente/criterio fallido en el reporte cerrado. Cubrir por regresión un pico histórico alto, presión nueva, OOM, swap y cambio de cgroup.
4. Mantener el contrato v1 y su ZIP fallido intactos. Versionar explícitamente cualquier nuevo contrato y justificar sus criterios antes de medir; la información actual no permite elegir entre ajustar recursos o aceptar el comportamiento observado. Preparar después un prompt para una sola corrida, con presupuesto PostgreSQL conservado de 512 MiB durante el diagnóstico.

**T5/E4 permanecen abiertas.** Los pasos de instrumentación anteriores están implementados en `events-memory-v1`; sigue [la guía de diagnóstico](events-memory.md) y [su nuevo prompt mac24](mac24-t5-memory-prompt.md). No repetir el prompt histórico. Conservar el entorno y las fuentes originales. T6, entrenamiento y test no se iniciaron.
