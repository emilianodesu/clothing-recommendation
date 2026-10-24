# T7: paquetes portables y recuperación local

> Estado actual: para el nuevo servidor Linux con HTTPS usar [server-deployment.md](server-deployment.md). Las referencias a futuros T5–T8 o test reservado en esta guía describen etapas históricas ya superadas; no reabren campañas. El repositorio puede ser público, los datos y respaldos no.

**E6/T7 cerradas tras auditar el ensayo H&M mac24 y ajustar el alcance con el usuario.** La prueba con otra cuenta de SO fue omitida por decisión explícita; no está verificada ni es requisito de cierre. T8 y test siguen reservados. No se necesita Kaggle, MLflow original, nube ni reentrenamiento para servir un paquete ya exportado. El dataset y experimentos históricos permanecen en su instalación original.

## Exportar/importar un paquete

Dentro de jobs (rutas bajo `/artifacts`):

```sh
hmrec release export /artifacts/serving/h-ID /artifacts/package.zip
hmrec release import /artifacts/package.zip /artifacts/imported/package
```

El ZIP es privado: contiene perfiles, aunque no credenciales. Está separado del formato de reportes pequeños para GitHub. Importar no activa el paquete; activar requiere la orden administrativa existente. Se comprueban inventario, SHA-256, schema2, reglas, corte, conteos, orden y referencias. Se rechazan archivos extra, duplicados, rutas anidadas/absolutas, enlaces y ZIP mayores de 2 GiB descomprimidos. Una exportación/importación no sobrescribe destinos existentes. Sólo se publican directorios después de verificarlos.

## Respaldar una instalación activa

Desde su raíz:

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py backup
```

Requiere las imágenes del código actual ya construidas y PostgreSQL activo. Si Airflow está activo, pausa los dos DAG, espera hasta 30 minutos a que no queden ejecuciones queued/running y restaura sus estados iniciales al salir. Un bloqueo advisory serializa el respaldo con trabajos del pipeline; bloqueos SHARE de las tablas esperan las escrituras en curso y bloquean las nuevas mientras se copian datos/paquetes. Las lecturas siguen disponibles; una escritura HTTP durante esa ventana puede esperar o agotar su timeout: reintentar con el mismo event_id tras el respaldo. Los bloqueos se liberan al salir, también ante error o desconexión. No se detiene PostgreSQL ni se reinician contadores.

El respaldo lógico usa PostgreSQL `COPY ... CSV`, tablas permitidas y huellas de migraciones. Incluye registros de versiones, versión activa/anterior, streams, semilla, eventos, perfiles, objetivos/cursor y todos los paquetes registrados; excluye contraseñas, roles, SQL ejecutable, datos crudos, pesos, MLflow, Qdrant y metadatos Airflow. Es un respaldo de la aplicación H, no de todos los experimentos. Para restaurar éstos se conservan los originales. Se incluyen seis consultas privadas de referencia para comparar IDs, orden, motivos y fallback.

La salida muestra ruta, tamaño y SHA-256. El ZIP permanece en `artifacts/backups/`, permisos 0600; máximo admitido 20 GiB. **No subirlo con github-handoff.py**: ese transporte sólo acepta reportes agregados. Conservarlo en almacenamiento privado autorizado; no incorpora cifrado propio ni pretende protección frente a otro proceso de la misma cuenta.

## Restaurar en un destino nuevo

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py restore /ruta/backup.zip '/ruta/Instalación restaurada' --sha256 SHA256_DEL_RESPALDO --port 18024
```

El destino debe ser absoluto, inexistente y no tener contenedores/volúmenes previos. Requiere espacio libre para tres veces el ZIP más 1 GiB. Se copian únicamente fuentes inventariadas, se generan contraseñas nuevas y se crean volúmenes propios. No se copia `.env`, `.git`, tokens ni configuración Docker. El inventario SHA-256 permite que esa instalación vuelva a ejecutar restore sin Git; cambios de código requieren un nuevo inventario obtenido de un checkout conocido.

Las imágenes `hmrec-jobs:0.1.0`, `hmrec-app:0.1.0` y PostgreSQL deben estar precargadas. El lanzador compara el código instalado con las fuentes, registra los IDs y usa `--pull never --no-build`. Para otra máquina, construir el mismo commit con sus locks o transferir imágenes con `docker save`/`docker load` mediante un canal privado autorizado y verificar SHA-256 del archivo y arquitectura. No copiar configuración Docker o credenciales. ARM64 está probado localmente; AMD64 sigue sin prueba física.

Se reservan dos redes /24 libres entre las redes Docker existentes en `10.253.0.0/16`, backend interna y publicación localhost. Se registra la asignación; no se eliminan redes históricas ni se cambian las del original. Revisar compatibilidad con redes/VPN propias al elegir el entorno; este rango sirve al destino local. Si no hay rango libre, se detiene.

La copia local del respaldo vuelve a validarse contra el SHA autorizado antes de iniciar PostgreSQL. Restore exige esquema/migraciones idénticos y tablas vacías; COPY conserva valores, verifica conteos y estado/cursor. Se inicia sólo PostgreSQL/API; no se reactiva automáticamente Airflow, UI ni otros experimentos. El éxito requiere readiness con la versión esperada, código correcto y seis consultas HTTP idénticas, excluyendo request_id. Los volúmenes originales no se montan en el destino.

Ante error, conservar destino, ZIP y logs. No borrar la reserva ni reutilizar un destino parcialmente restaurado; inspeccionarlo y planificar explícitamente otro. Nunca usar `prune`, `down -v` o reemplazar `.env` para aparentar éxito.

## Ensayo y alcance

`verify --suite portability --existing-services` usa esquemas sintéticos aislados: transferencia, corrupción, bloqueo de escrituras con lecturas disponibles, rollback de errores y restauración de perfiles/eventos. El ensayo `scripts/portability_check.py` añade otro proyecto Compose/ruta con espacios, credenciales nuevas, igualdad de recomendaciones, preservación de estado del original y operación offline: desconecta temporalmente sólo la red externa de la API destino, verifica que conserva exclusivamente backend interna y consulta la API real; al terminar reconecta la publicación. Esas consultas no evalúan test ni calidad del recomendador.

La segunda ruta en una misma Mac **no equivale a otro equipo ni a otro usuario de SO**. Las credenciales PostgreSQL sí son distintas. La reproducción física del flujo se hará en mac24; el transporte de un respaldo H&M a otra máquina queda pendiente de autorización/canal privado, no se disfraza con el envío de reportes.

Sólo un ZIP agregado `portability-v1` viaja por GitHub. Código, imagen, fuente T6, respaldo, estado y consultas se registran mediante huellas/conteos; ni clientes ni eventos individuales salen por ese canal. El respaldo real permanece privado. [Procedimiento mac24](mac24-t7-prompt.md).

La validación ampliada local de bootstrap usa `HMREC_TEST_EXPLICIT_NETWORKS=1` cuando la VM acumuló redes históricas y agotó los pools predeterminados; el indicador afecta exclusivamente a proyectos temporales de esa suite. No limpia ni modifica redes originales.

## Revisión H&M mac24

La [auditoría](../evidence/portability-review-mac24.json) acepta el ensayo portability-v1 del 23 de octubre: 96.531 s, 918,506,419 bytes de respaldo, seis consultas iguales tras restaurar y seis sin red externa. Se verificaron huellas de ZIP/código/runner y controles agregados; el respaldo privado no se descargó aquí. El reporte fallido anterior permanece intacto. La reproducción del flujo ya ocurrió físicamente en mac24, con otra ruta y nuevas credenciales de base de datos. El usuario retiró el criterio de ejecutar bajo otro usuario de SO; se conserva como limitación no probada, sin bloquear el cierre T7/E6. No repetir los adaptadores ni iniciar T8 automáticamente.
