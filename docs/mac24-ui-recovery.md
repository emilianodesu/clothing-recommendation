# Preparar recuperación de UI en mac24, sin despliegue

## Resultado mac24 y siguiente decisión (2026-10-24)

La preparación en `9b2dbf5b1cd8a84fed5f2c16d956f59841c3305c` terminó en **`blocked_capacity`**, etapa `capacity_admission`. Ver [evidencia agregada](../evidence/ui-recovery-mac24.json); el reporte completo y las instantáneas permanecen privados en mac24. Su huella SHA-256 está en la evidencia para correlacionarlos; este resumen no sustituye una auditoría independiente del reporte privado.

La VM dispone de 14,624,956,416 bytes (13.621 GiB) y 4 CPU. Los nueve contenedores activos suman 17,985,175,552 bytes (16.75 GiB) de límites de RAM y 8.5 CPU de cuotas; no son consumo instantáneo. La admisión exige 21,206,401,024 bytes, incluidos constructor de 2 GiB y margen de 1 GiB: faltan **6,581,444,608 bytes (6.129 GiB)**. Ningún contenedor activo tiene memoria sin límite. Disco libre: host 161.058 GiB y VM 64.162 GiB; ambos superan los 10 GiB requeridos.

Se verificaron AC, tapa abierta, ausencia de contenedores de jobs pesados, ambos DAG pausados y cero ejecuciones Airflow activas. El inventario coincide con el anterior y permaneció idéntico durante las consultas; API/PostgreSQL/UI saludables. La huella UI coincide con la revisión histórica indicada abajo.

No se creó constructor, worktree ni contenedor probe; no hay imagen recuperada ni comprobaciones de imports/health de Streamlit. No se modificaron servicios, límites ni tags; `deployment_started=false`. El guard de reposo temporal terminó al cerrar la preparación.

**Decisión pendiente:** diseñar y autorizar un mantenimiento secuencial con auxiliares identificados, capacidad recalculada y recuperación explícita, conservando API/PostgreSQL/UI originales. Esta evidencia no autoriza detener servicios, ampliar Colima, desplegar ni repetir automáticamente la preparación. Los pasos siguientes son el procedimiento de referencia, detenido antes de construir.

## Objetivo y antecedente

Preparar una imagen ejecutable de recuperación antes de reemplazar UI. El [resumen recibido](../evidence/ui-inventory-review-mac24.json) declara inventario estable, pero Docker no reconoce los metadatos de la imagen UI. No se ha descargado el reporte privado: no atribuir a esta revisión inspección directa de IDs, límites o capas.

La huella UI recibida se comprobó independientemente contra Git:

- Revisión histórica: `6130943641e599121119660693ed276cf3b3fbca`.
- Archivo: `src/hm_recommender/ui/app.py`.
- SHA-256: `5a59c1c6014f5c5fc4980783f398cff5b45c116d52ac09c1951feb12450fa4d8`.

La reconstrucción recupera ese código con sus archivos de dependencias, no los bytes exactos de la imagen perdida. La coincidencia de un archivo no acredita identidad de todo el entorno histórico. No copiar la imagen reconstruida en la Mac principal ni sus credenciales.

## Instrucción al agente de mac24

El usuario autoriza preparar la imagen y realizar la prueba aislada descrita aquí. **No desplegarla, detener servicios existentes, ampliar Colima, cambiar límites de servicios ni ejecutar campañas.** No se requiere una nueva cuenta. Trabajar en el clon original limpio, `main`, SHA completo recibido; usar `SSL_CERT_FILE=/etc/ssl/cert.pem` y contexto explícito `colima-hm-project` en todas las órdenes Docker.

1. Leer `AGENTS.md`, esta guía y el reporte privado `.runtime/ui-inventory-f5c6fc2580044621a6e599e8804e2510/report.json`. Comprobar que su protocolo, commit, estado y huellas corresponden al resumen auditado. Volver a consultar identidades/generaciones de todos los contenedores y registrar una instantánea nueva. Exigir API/PostgreSQL/UI saludables; si difieren de la captura anterior, registrar la diferencia y detener esta preparación para revisión, sin intentar reparar nada.
2. Comprobar AC, tapa abierta, ausencia de jobs pesados y Airflow pausado e inactivo usando los controles existentes de lectura. Mantener el equipo despierto durante la preparación. No ejecutar un lanzador T5–T8 para obtener estos controles.
3. **Admitir recursos antes de construir:** sumar `HostConfig.Memory` de TODOS los contenedores en ejecución, incluidos otros proyectos. Un límite cero significa no acotado; no tratarlo como consumo cero. Se requiere `suma + 2 GiB de constructor + 1 GiB de margen <= MemTotal`. Registrar CPUs y límites además; no usar el RSS instantáneo como garantía de capacidad. Revisar espacio libre tanto en host como en VM para imágenes/caché: mínimo 10 GiB libres en cada uno como presupuesto de esta preparación. Si falta margen o no puede medirse, detenerse con `blocked_capacity`, devolver suma, capacidad, margen faltante y servicios activos por rol. **No apagarlos automáticamente.** Los 14 GiB configurados no garantizan ese margen.
4. Crear una carpeta privada nueva `.runtime/ui-recovery-UUID/` (modo 700), con logs y reporte. Crear un worktree **detached** nuevo dentro de ella en la revisión histórica exacta. No cambiar la rama del clon principal, modificar el worktree histórico existente ni copiar `.env`, datos o artefactos. Verificar de nuevo SHA-256 del archivo UI. Generar el archivo CA exclusivamente desde el almacén local confiable, como hace `bootstrap.prepare_configuration`, sin invocar esa función completa: no se necesita configurar servicios para construir.
5. Utilizar un constructor Buildx `docker-container` NUEVO, exclusivo de esta preparación, con memoria y memoria+swap de 2 GiB, cuota de 1 CPU, y sin cambiar el builder predeterminado. Verificar sus límites efectivos antes de construir; si Buildx no admite estos controles, detenerse, no usar el constructor predeterminado sin límites. Registrar versión Buildx y digest efectivo de BuildKit. La construcción usa el Dockerfile histórico, `uv.lock`, secreto `build_ca`, plataforma `linux/arm64` y tag único `hmrec-ui-recovery:UUID`. Usar `--load` para disponer de ella en el motor. No cambiar `hmrec-app:0.1.0`, no reconstruir jobs ni usar `compose up`.
6. Guardar log privado, salida y digest de la imagen. Detener únicamente el constructor nuevo al terminar, también si falla; conservar su identidad, logs y caché para diagnóstico. No hacer `prune`. Recalcular recursos antes del probe: `suma + 512 MiB + 1 GiB <= MemTotal`.
7. Ejecutar un contenedor temporal con nombre único, la imagen por digest, red `none`, sin puertos, sin mounts de datos/socket/credenciales, rootfs de sólo lectura, `/tmp` tmpfs de 128 MiB, usuario 10001, memoria=memoria+swap=512 MiB y CPU=0.5. No pasar `.env`. Arrancar Streamlit con la orden de UI histórica (puerto 8501 sólo interno). Dentro del mismo contenedor comprobar imports `streamlit`/`httpx`, huella del archivo y `http://127.0.0.1:8501/_stcore/health` hasta 90 s. Registrar versiones Python/Streamlit/httpx, salida del probe, digest y límites efectivos. Si muere, falla el hash o no llega a saludable, conservar logs y estado como failed. El healthcheck acredita arranque, no paridad funcional ni acceso a H&M.
8. Detener y retirar únicamente ese contenedor temporal por su ID exacto; conservar la imagen de recuperación y sus referencias. Comparar contra la instantánea inicial todos los servicios existentes: ID, imagen, StartedAt, RestartCount, estado y límites. Si alguno cambió, no declarar preservación. No borrar el worktree ni el reporte.
9. Terminar aquí, sin construir la UI corregida ni cambiar el despliegue. Devolver el resumen definido abajo. Un fallo no autoriza reintentar automáticamente, modificar dependencias, arrancar Colima, parar auxiliares o limpiar Docker.

### Ejemplo de límites del constructor

Usar un nombre nuevo por preparación; los valores son el presupuesto de este procedimiento, no una ampliación de recursos de servicios:

```bash
docker --context colima-hm-project buildx create \
  --name NOMBRE_UNICO --driver docker-container \
  --driver-opt memory=2g,memory-swap=2g,cpu-period=100000,cpu-quota=100000
```

Inspeccionar primero las opciones que admite la versión instalada. Inicializar sólo ese builder y comprobar el contenedor creado. Construir usando explícitamente `--builder NOMBRE_UNICO`; no usar `--use`. La CA se pasa mediante `--secret id=build_ca,src=RUTA_CA_LOCAL`. Todas las rutas del build deben proceder del worktree histórico, incluidas contexto y Dockerfile.

## Resumen que se devuelve

Guardar JSON privado y devolver sólo agregados/huellas:

- estado `passed`, `failed` o `blocked_capacity`, etapa y causa;
- SHA del código del procedimiento, SHA histórico, fuente UI;
- capacidad VM, suma de límites activos, presupuestos constructor/probe y espacio libre;
- digest de imagen recuperable, versiones y límites efectivos del constructor/probe;
- imports, hash UI y health de Streamlit, indicando que no se comprobó integración API;
- preservación de servicios, limpieza de recursos temporales, `deployment_started=false`;
- ruta local del reporte para el operador, nunca `.env`, tokens, clientes, predicciones o contenido de bases.

No llamar `github-handoff.py` con este JSON: no es uno de sus protocolos ZIP. El operador puede devolver el resumen por el canal de conversación existente. El siguiente paso, tras revisión, será preparar la candidata y el cambio exclusivo de UI. No declarar que la recuperación o el despliegue ya se ejecutaron por existir esta guía.

## Propuesta tras revisión principal

Se recalcularon las sumas del reporte agregado: coinciden con el bloqueo. Se preparó un [plan de mantenimiento de seis auxiliares](mac24-ui-maintenance-plan.md), pendiente de autorización del usuario. Mantiene API/PostgreSQL/UI originales y permite construir con 8 GiB de presupuesto total, incluido margen. No ejecutarlo sin autorización; no modifica el resultado `blocked_capacity` ni acredita una imagen recuperada.
