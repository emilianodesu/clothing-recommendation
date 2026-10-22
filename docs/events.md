# T5: interfaz, eventos y perfiles operativos

H sigue siendo historial28d+popularidad7d. T5 incorpora Streamlit, eventos durables y actualización de perfiles en PostgreSQL. El paquete inicial y `/recommend` permanecen congelados; cada entorno tiene un `stream_id` y usa `/streams/{stream_id}/recommend`. La reproducción H&M en mac24 está pendiente; no confundir la implementación local con cierre remoto o aceptación T8.

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

Sigue [el prompt mac24 T5](mac24-t5-prompt.md). Se reutiliza el paquete full activo y su dataset local; falta de artefactos privados bloquea el ensayo. No se recuperan desde los ZIP de reportes.

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
