# Mantenimiento propuesto para recuperar la imagen UI

Estado: pendiente de autorización del usuario. No ejecutar pausas por la sola existencia de este documento. Base: `evidence/ui-recovery-mac24.json`, publicado por mac24 en `ddc8501`; aquí se recalcularon las sumas, sin inspeccionar el inventario privado.

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

## Secuencia para el futuro ejecutor

1. Exigir autorización explícita para esta ventana, checkout limpio y SHA recibido. Validar el reporte privado contra su SHA-256 publicado; resolver proyectos e IDs exactos. Comprobar AC, tapa, espacio, servicios saludables, Airflow pausado/inactivo y ausencia de jobs. Guardar estado inicial y una lista privada de recuperación antes de detener nada.
2. Detener por ID los tres Airflow y MLflow, y API restaurada antes de PostgreSQL restaurado. Registrar cada transición. API/PostgreSQL/UI originales permanecen intactos. Un fallo obliga a recuperar los auxiliares ya detenidos, no a repetir automáticamente.
3. Ejecutar la reconstrucción histórica y el probe aislado de `mac24-ui-recovery.md` con sus presupuestos, huellas y logs. No construir la candidata, desplegar UI, activar paquetes ni repetir T8. Conservar la imagen reconstruida, aunque falle una comprobación posterior, identificada como no aceptada si corresponde.
4. En un bloque de recuperación obligatorio, detener únicamente el constructor/probe temporales antes de devolver auxiliares. Arrancar los mismos contenedores: PostgreSQL restaurado antes de su API; MLflow; Airflow API, DAG processor y scheduler. Comprobar salud y mantener DAG pausados. No usar `compose up` ni reconstruir para reparar la recuperación.
5. Verificar mismos IDs/imágenes/límites de los auxiliares y sus nuevas generaciones esperadas; API/PostgreSQL/UI originales deben conservar también StartedAt y RestartCount. Registrar cualquier fallo de recuperación y no repetir la construcción. Conservar una ruta de recuperación por IDs para una interrupción de SSH.
6. Devolver resumen con capacidad, digest, checks del probe, preservación de originales y recuperación de los seis auxiliares. No considerar terminada la ventana si queda algún auxiliar sin recuperar. La vuelta al inventario inicial restaura también su sobreasignación histórica; no acredita capacidad para nuevos jobs simultáneos.

La autorización solicitada cubre únicamente esta preparación con mantenimiento y recuperación. Actualizar la UI activa será un paso separado después de revisar la imagen recuperable. No se ha ejecutado esta ventana.
