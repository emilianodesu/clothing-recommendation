# Mantenimiento autorizado para recuperar la imagen UI

Estado: autorizado por el usuario el 2026-10-24 al responder «Continua» a la solicitud explícita de esta ventana. La autorización cubre una preparación con pausa y recuperación de los seis auxiliares indicados; no autoriza despliegue ni campañas. Base: `evidence/ui-recovery-mac24.json`, publicado por mac24 en `ddc8501`; aquí se recalcularon las sumas, sin inspeccionar el inventario privado.

## Alcance de la ventana

Mantener activos API, PostgreSQL y UI del proyecto original, conservando contenedores, imágenes, volúmenes, límites, estado H y eventos. Detener temporalmente sólo estos seis auxiliares, tras resolver sus IDs contra el reporte privado y el inventario actual:

| Proyecto | Servicio | Límite RAM |
| --- | --- | --- |
| Restauración T7 | API | 4 GiB |
| Restauración T7 | PostgreSQL | 0.5 GiB |
| Original | MLflow | 1 GiB |
| Original | Airflow scheduler | 5 GiB |
| Original | Airflow API | 0.75 GiB |
| Original | Airflow DAG processor | 0.5 GiB |

Estos servicios estarán indisponibles durante la ventana. Los DAG deben permanecer pausados y sin ejecuciones; MLflow no debe tener trabajo activo. No detener por coincidencias parciales de nombre, recrear contenedores, borrar volúmenes ni ampliar Colima.

## Recursos calculados

Se retiran temporalmente 11.75 GiB de límites activos; quedan 5 GiB. Constructor de 2 GiB más margen de 1 GiB exige 8 GiB frente a 13.621 GiB efectivos. Tras detener el constructor, probe de 0.5 GiB más margen exige 6.5 GiB. Recalcular con el inventario real antes de cada fase: cualquier servicio adicional puede cambiar la admisión.

Las cuotas CPU de API/UI suman 2.5 y el constructor añade 1. PostgreSQL no tiene cuota CPU: no afirmar que 3.5 sea un límite total garantizado. No cambiar ese límite en esta ventana; comprobar que no haya cargas pesadas y conservar la comprobación de salud de los originales.

## Secuencia para el ejecutor

1. Usar la autorización anterior, checkout limpio y SHA recibido. Validar el reporte privado contra su SHA-256 publicado; resolver proyectos e IDs exactos. Comprobar AC, tapa, espacio, servicios saludables, Airflow pausado/inactivo y ausencia de jobs. Guardar estado inicial y una lista privada de recuperación antes de detener nada.
2. Detener por ID los tres Airflow y MLflow, y API restaurada antes de PostgreSQL restaurado. Registrar cada transición. API/PostgreSQL/UI originales permanecen intactos. Un fallo obliga a recuperar los auxiliares ya detenidos, no a repetir automáticamente.
3. Ejecutar la reconstrucción histórica y el probe aislado de `mac24-ui-recovery.md` con sus presupuestos, huellas y logs. No construir la candidata, desplegar UI, activar paquetes ni repetir T8. Conservar la imagen reconstruida, aunque falle una comprobación posterior, identificada como no aceptada si corresponde.
4. En un bloque de recuperación obligatorio, detener únicamente el constructor/probe temporales antes de devolver auxiliares. Arrancar los mismos contenedores: PostgreSQL restaurado antes de su API; MLflow; Airflow API, DAG processor y scheduler. Comprobar salud y mantener DAG pausados. No usar `compose up` ni reconstruir para reparar la recuperación.
5. Verificar mismos IDs/imágenes/límites de los auxiliares y sus nuevas generaciones esperadas; API/PostgreSQL/UI originales deben conservar también StartedAt y RestartCount. Registrar cualquier fallo de recuperación y no repetir la construcción. Conservar una ruta de recuperación por IDs para una interrupción de SSH.
6. Devolver resumen con capacidad, digest, checks del probe, preservación de originales y recuperación de los seis auxiliares. No considerar terminada la ventana si queda algún auxiliar sin recuperar. La vuelta al inventario inicial restaura también su sobreasignación histórica; no acredita capacidad para nuevos jobs simultáneos.

La autorización concedida cubre únicamente esta preparación con mantenimiento y recuperación. Actualizar la UI activa será un paso separado después de revisar la imagen recuperable. No se ha ejecutado esta ventana.

## Entrega del resultado

Conservar el reporte privado y publicar únicamente su resumen agregado, sin clientes, credenciales, rutas privadas ni inventarios completos, en `evidence/ui-recovery-maintenance-mac24.json`. Registrar `procedure_commit`, estado, etapa, imagen recuperada por digest, fuente histórica, límites, resultado de imports/hash/health, preservación original y recuperación individual de los seis roles. Separar la aprobación de la imagen de la recuperación de servicios: ambas deben constar. Si hay fallo, publicarlo también sin cambiarlo a passed.

Después de recuperar los servicios y cerrar el reporte, actualizar esta guía con el resultado, hacer commit y push de esos archivos en el repositorio privado. No modificar código, dependencias o umbrales durante el intento. Si el push falla, conservar el commit local y devolver el resumen al operador. El JSON no debe enviarse al empaquetador de ZIP históricos. No sobrescribir `evidence/ui-recovery-mac24.json`, que conserva blocked_capacity.

## Ejecución desde Terminal por SSH

Está implementado `scripts/mac24_ui_recovery.py`; no es necesario pasar instrucciones a otro agente. Tras actualizar un clon limpio al SHA entregado, ejecutar:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
python3 scripts/mac24_ui_recovery.py --expected-commit SHA_COMPLETO --publish
```

El adaptador mantiene el contexto `colima-hm-project`, toma el bloqueo de jobs pesados, exige el inventario previo y la huella del reporte privado, comprueba DAG pausados/inactivos y ausencia de runs MLflow activos, y registra el estado antes de cualquier pausa. Rechaza un segundo intento si existe `.runtime/ui-recovery-terminal-v1/`. Usa un constructor nuevo con límites comprobados y un probe sin red externa, puertos, datos ni secretos. Los parámetros del constructor siguen la [documentación oficial de Docker](https://docs.docker.com/build/builders/drivers/docker-container/).

Mantener la sesión abierta, AC y tapa abierta. La salida anuncia etapas y actividad cada 20 segundos durante procesos largos; logs completos en esa carpeta privada. `--publish` confirma el repositorio privado y publica sólo `evidence/ui-recovery-maintenance-mac24.json` después de recuperar los auxiliares, también si la construcción falló. No sobrescribe el reporte blocked_capacity ni modifica tags compartidos.

Si la conexión se interrumpe, el adaptador intenta finalizar la construcción y recuperar auxiliares. Una pérdida de energía o terminación forzada no permite garantizar ese bloque. En ese caso, inspeccionar el estado guardado y ejecutar **únicamente**:

```bash
python3 scripts/mac24_ui_recovery.py --restore-only --expected-commit SHA_DEL_INTENTO --publish
```

Este modo comprueba huellas de los scripts, detiene sólo sus recursos temporales y recupera por los IDs guardados. No reconstruye ni repite el probe. Conserva el resultado failed si el intento original falló; la recuperación es un hecho separado. No borrar la carpeta de estado. Si falla el push, el resumen y cualquier commit local quedan conservados; publicar ese commit no requiere repetir mantenimiento.

Limitación de validación local: las pruebas host simulan el motor remoto para comprobar selección, recuperación y fallos. El flujo completo de Buildx/Streamlit todavía debe ejecutarse en mac24 bajo esta ventana; no se acredita por la sola existencia del script.

Las pruebas del adaptador cubren fallos de pausa parcial, construcción, recuperación individual, retirada del probe y pérdida de stdout SSH. El contexto histórico se crea con permisos legibles para el usuario no privilegiado del contenedor, dentro de una carpeta privada en el host; secretos y logs conservan permisos privados. Un fallo al retirar un probe ya detenido se registra sin bloquear la recuperación de servicios.
