# Actualización UI de mac24

El lanzador `scripts/mac24_ui_deploy.py` implementa el [plan de cambio exclusivo de UI](mac24-ui-deployment-plan.md). Está probado localmente; **aún no se ejecutó en mac24**. No repetir las preparaciones históricas ni ningún T8.

## Ejecución

Desde Terminal, conectar por SSH a `emilianodesu@192.168.100.78`. Mac24 debe estar conectada a AC, con tapa abierta, Colima `hm-project` activo y los nueve servicios esperados saludables. Mantener Terminal abierta hasta el cierre.

En el repositorio de mac24, usar el SHA completo entregado por la Mac principal, con `main` limpio y actualizado. Conservar estas variables:

```sh
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
```

La ejecución autorizada es una vez:

```sh
python3 scripts/mac24_ui_deploy.py --deploy --expected-commit SHA_COMPLETO --publish
```

Opcionalmente, `--preflight-only` con el mismo SHA termina antes de reservar, descargar, construir o detener servicios. `--deploy` repite esas comprobaciones antes de actuar. No usar `--preflight-only` después de reservar; la carpeta existente evita repetir el despliegue.

El lanzador valida el reporte y estado privado de recuperación, imagen histórica, configuración, energía, salud, recursos, H vigente, Airflow pausado y ausencia de trabajos. Descubre el plugin Buildx efectivo mediante Docker y verifica imágenes públicas con configuración anónima aislada antes de cualquier pausa.

Después detiene sólo los seis auxiliares registrados (API/PostgreSQL restaurados T7, MLflow y tres servicios Airflow), construye con 2 GiB/1 CPU y comprueba la candidata. La regresión específica de UI corre sin red ni datos, con 512 MiB/0.5 CPU. La API, PostgreSQL y UI originales permanecen activos durante la preparación. Sólo después de aprobar se reemplaza UI, con el mismo binding localhost y sus límites originales. No modifica el tag compartido `hmrec-app:0.1.0`.

Las comprobaciones de conservación usan IDs/imágenes/generaciones de contenedores, H vigente y una huella de conteos de once tablas más metadatos de streams/puntero/targets. No equivalen a un checksum de cada fila. El render contra API es de lectura, sin registrar eventos ni refrescar perfiles. La comprobación visible en un navegador sigue separada.

## Fallos y recuperación

- Un fallo de registro público no pausa servicios. Un fallo de construcción o regresión conserva la UI anterior y recupera auxiliares.
- Si falla la candidata al aplicarla o al volver los auxiliares, se aplica la imagen histórica verificada. El resultado conserva `failed`, aunque la recuperación termine bien.
- HUP/INT/TERM durante recuperación se difieren hasta terminar las operaciones acotadas. No puede garantizarse recuperación ante apagado, SIGKILL o pérdida del motor Docker.
- Si el cierre informa `recovery_pending`, conservar todo y ejecutar únicamente con el mismo código:

```sh
python3 scripts/mac24_ui_deploy.py --restore-only --expected-commit SHA_COMPLETO --publish
```

Ese modo no construye ni repite la regresión; vuelve a comprobar la UI actual y recupera auxiliares por sus IDs. Rechaza una recuperación ya completada. No borrar `.runtime/ui-deploy-v1`, modificar reservas, ampliar recursos ni repetir `--deploy`.

Los logs, configuración y estado permanecen privados en `.runtime/ui-deploy-v1/`. El resumen agregado es `summary.json`; con recuperación completa, `--publish` agrega sólo `evidence/ui-deployment-mac24.json` a GitHub privado. Si falla únicamente la publicación, no repetir el despliegue: conservar `publish-commit.log`/`publish-push.log` y revisar Git. El reporte requiere revisión principal antes de declarar actualización aceptada.

## Operaciones posteriores

El override vigente se conserva en `.runtime/ui-deploy-v1/active-override.json`, con la imagen por ID que pasó las comprobaciones. **Una recreación UI sin este override podría volver al tag compartido anterior.** Para futuras consultas de configuración o estado, incluirlo junto con el Compose original, `.env` y proyecto de esta instalación. No copiar credenciales/rutas de otra computadora.

Este comando de consulta, dentro del repositorio y con la configuración cargada por el lanzador, ilustra los archivos; no inicia servicios:

```sh
docker --context colima-hm-project compose --env-file .env   --file compose.yaml --file .runtime/ui-deploy-v1/active-override.json ps ui
```

Conservar ambas imágenes por ID, worktree de construcción, logs, fallos anteriores y reporte. La restauración de auxiliares vuelve al inventario histórico de límites; no acredita capacidad simultánea sostenida ni cambia el resultado T8-B.
