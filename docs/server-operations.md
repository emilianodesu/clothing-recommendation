# Operación del servidor del equipo

Usar la ruta restaurada `DEST`, sus credenciales y su lock de imágenes. No usar los scripts `mac24_*` ni copiar IDs/overrides de otra máquina. [Instalación](server-deployment.md) · [Contrato del respaldo](reproducibility.md).

## Estado, usuarios y recuperación básica

```sh
python3 "$DEST/scripts/server.py" doctor
python3 "$DEST/scripts/server.py" check
python3 "$DEST/scripts/server.py" check-public --user ana
python3 "$DEST/scripts/project.py" status
```

Los servicios tienen `restart: unless-stopped`; Docker debe arrancar con el sistema. Un servicio detenido explícitamente puede requerir arranque manual. Investigar antes de recrear contenedores. No imprimir `docker inspect` completo o `docker compose config` sin filtrar: contienen variables secretas. `check` devuelve sólo identidades, versión y estado.

Para alta/rotación, repetir `server.py user --domain DOMINIO --name USUARIO` y luego `publish`. Para retirar una cuenta, añadir `--remove` y luego `publish`; no permite eliminar la última. Un cambio en disco no afecta al gateway hasta recargarlo. La recarga puede cerrar WebSockets: avisar al equipo y recargar navegador. Basic Auth puede conservar credenciales en el navegador; cerrar la sesión privada o el navegador después de usar equipos compartidos. Para desactivar todo acceso, detener únicamente el gateway de esta instalación; no retirar el requisito de autenticación.

Los logs de Caddy están limitados por rotación Docker; no se habilitan access logs con rankings o credenciales. Inspeccionar sus errores mediante `docker logs --tail 100 ID_GATEWAY` sin publicar la salida completa. Las claves TLS/estado ACME viven en volúmenes privados, separados del backup de aplicación.

## Respaldos

```sh
python3 "$DEST/scripts/server.py" backup
```

Admite RAM sumando las cuotas activas + 5 GiB jobs + 1 GiB de margen. Un host de 8 GiB puede servir, pero no ejecutar este respaldo con todos esos límites: no pausar servicios automáticamente. Coordinar una ventana o usar capacidad suficiente. El backup lógico bloquea escrituras mientras copia; lecturas siguen disponibles, pero una escritura puede esperar o agotar timeout. El cliente debe reintentar con el mismo event_id. El servidor base no inicia Airflow; si se añadió después, el respaldo pausa los DAG y restaura su estado previo mediante el procedimiento existente.

Guardar ZIP 0600 y SHA-256 en almacenamiento privado fuera del disco del servidor. Propuesta operativa: una copia diaria y otra antes de actualizar, con siete diarias y cuatro semanales, ajustadas al espacio y uso reales. **No hay un cron ni retención destructiva instalados automáticamente.** El responsable debe programarlos y verificar una restauración; no borrar copias basándose sólo en antigüedad. No afirmar backup recuperable sin probarlo.

El ZIP incluye tablas permitidas, paquetes registrados y consultas de paridad; no incluye `.env`, claves TLS, ZIP Kaggle, MLflow, Qdrant ni checkpoints. Conservar por separado las credenciales operativas y volúmenes de Caddy en un almacén privado autorizado. Para recuperar en un host nuevo se generan nuevas contraseñas, se importa el ZIP y se emite TLS de nuevo si procede. No copiar indiscriminadamente configuración Docker del host anterior.

## Actualización y rollback

El flujo automatizado es de instalación nueva; **no es un actualizador in situ**. La instalación está fijada por IDs en `.runtime/server-images.json`. No modificar tags, borrar ese archivo o ejecutar builds sobre el proyecto activo para actualizarlo.

1. Elegir commit nuevo y revisar migraciones/contrato; conservar commit e imágenes anteriores. Leer el alcance del cambio. No cambiar modelo mirando el test ya consumido.
2. Crear un respaldo privado y comprobar su hash. Guardar los reportes vigentes. Reservar la ventana de mantenimiento y el tratamiento de escrituras; no activar una segunda copia escribible con datos divergentes.
3. Construir imágenes en motor dedicado/admitido; restaurar a instalación nueva. Si sólo existe un servidor, planificar explícitamente la pausa/conservación del anterior o disponer de otro motor; `build` no detiene cargas por sí solo.
4. Verificar migraciones compatibles, paridad, fuentes, imágenes, UI y fotografías. El restaurador actual exige las mismas huellas de migraciones: un cambio de esquema necesita un plan de migración revisado, no ignorar la discrepancia.
5. Cambiar el gateway/DNS al destino validado durante la ventana; registrar el cambio. Conservar origen y respaldo. Nunca arrancar dos gateways sobre 80/443 a la vez.
6. Si falla antes de nuevas escrituras, regresar al servicio anterior preservado. Si ya se aceptaron escrituras, resolver su traslado antes de volver: un rollback de imagen o DNS no revierte/espeja la base de datos.

Para volver únicamente a un paquete H previamente registrado se conserva `project.py rollback-release`, pero **no es rollback de infraestructura ni de esquema**. Su uso requiere decidir el estado de los streams; no ejecutarlo como reparación genérica. Nunca restaurar el backup sobre tablas existentes para aparentar recuperación: usar otra ruta/volúmenes y conservar evidencias.

## Incidencias conocidas

| Síntoma | Primera comprobación |
| --- | --- |
| Docker no responde | Servicio/contexto/socket local y permisos; no copiar el socket Colima de mac24 |
| Construcción rechazada | Motor ocupado, RAM/disco o checkout sucio; conservar cargas, no quitar el control |
| Restore rechaza hash/tablas/migraciones | Archivo autorizado, integridad, destino realmente nuevo y commit compatible |
| HTTP 502 | Salud UI/API y red externa `api-publication`; no publicar la API directamente |
| TLS no se emite | A/AAAA, 80/443, NAT/CGNAT, reloj del host y almacenamiento de Caddy |
| UI sin imágenes | ZIP opcional ausente/incompleto; sustituto esperado; no implica pérdida del catálogo |
| UI sin streams | Backup incompleto para esta demo; no publicar hasta revisar la fuente |
| WebSocket desconectado | Prueba de navegador, auth, proxy intermedio, recarga de configuración |
| Backup sin RAM | Cuotas activas + job; acordar recursos/ventana en lugar de aumentar límites a ciegas |
| Usuario revocado aún conectado | Aplicar `publish`, cerrar sesión/socket y volver a comprobar sin credenciales |

## Informe de despliegue que devuelve el receptor

Crear un resumen revisable, por ejemplo `evidence/server-deployment-YYYY-MM-DD.json`, con:

- Commit fuente completo, Linux/distribución, arquitectura, versiones Docker/Compose/Buildx, CPU/RAM/disco y límites efectivos.
- SHA-256 del backup y su fecha/alcance; no ruta personal, contenido, clientes o respuestas individuales.
- IDs de imágenes y release H activo, código API, readiness y comprobación de puertos internos.
- HTTPS válido, 401 sin credenciales, rechazo de credencial incorrecta, acceso autenticado, redirección HTTP y navegador/WebSocket desde otra red.
- Fotografías disponibles o sustitutos, política de backups acordada y comprobación de recuperación.
- Estado por comprobación (`passed`, `failed`, `not_run`) y limitaciones. No llamar “desplegado” a sólo construir imágenes ni trasladar la latencia de mac24 al servidor.

No incluir contraseña, hash de autenticación, token, clave TLS, `.env`, datos/backup ni capturas con compradores. Los JSON privados generados ayudan a revisar, pero no se publican automáticamente. Las auditorías académicas históricas permanecen intactas.
