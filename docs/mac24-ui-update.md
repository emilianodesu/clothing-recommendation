# Corrección UI en mac24: inventario previo

La corrección de metadatos está implementada y verificada en la demo local (`e478f71`); todavía no se ha desplegado en mac24. El inventario ya se recibió; el siguiente paso es [preparar una imagen de recuperación](mac24-ui-recovery.md). El procedimiento de inventario de abajo se conserva como antecedente, no hay que repetirlo automáticamente. No repetir campañas, test final, carga ni los lanzadores T8 históricos.

## Comando del agente de mac24

Desde el clon limpio en `main`, actualizar por fast-forward al SHA completo recibido. Conservar `SSL_CERT_FILE=/etc/ssl/cert.pem` y ejecutar:

```bash
python3 scripts/mac24_ui_inventory.py --expected-commit SHA_COMPLETO_RECIBIDO
```

El script fija explícitamente el contexto `colima-hm-project`. Si Colima está detenida, informa del fallo y no la arranca. Consulta todos los contenedores del motor y sus identidades, imágenes, salud y límites; exige un API, PostgreSQL y UI saludables del proyecto configurado. Lee la huella del archivo UI con `sha256sum` dentro del contenedor y la compara con el checkout. Repite el inventario para detectar cambios durante la lectura.

No construye imágenes, descarga capas, crea contenedores, cambia tags, reinicia servicios ni escribe eventos. Sólo escribe su propio reporte en `.runtime/ui-inventory-*/`. No imprime variables de entorno ni configuración Docker completa.

Devolver aquí la línea JSON final de resumen y el estado de salida. El reporte privado incluye identidades de contenedores y etiquetas de proyectos; conservarlo localmente. No es un ZIP del protocolo de intercambio y no debe pasarse a `github-handoff.py`. Para trabajo a través del agente remoto, éste puede devolver el resumen agregado al operador; no copiar `.env` ni clientes.

`passed` acredita únicamente el inventario estable. **No acredita despliegue, capacidad para construir, compatibilidad API/UI ni imagen de recuperación ejecutable.** `ui_image_metadata_available=true` sólo indica que Docker reconoce la imagen: una capa puede faltar, como sucedió en la demo local.

## Decisión después de recibir el inventario

1. Comparar fuentes y revisar el contrato UI/API vigente. Si la UI ya coincide, verificar ese despliegue en vez de reconstruirlo.
2. Si difiere, validar una imagen de recuperación ejecutable y revisar recursos activos antes de construir. No cambiar límites ni detener auxiliares automáticamente. Si la imagen antigua está incompleta, conservar el servicio e identificar un checkout histórico verificable para recuperación.
3. Construir la candidata con tag propio, código fijado y CA local; comprobar identidad y regresión sintética en un entorno con recursos disponibles.
4. Preparar un override privado que cambie únicamente `ui`, preservando la configuración original, puertos y recursos. Aplicar sólo UI con `--no-deps --no-build --pull never`, en un paso posterior explícito. No reemplazar el tag compartido de API.
5. Verificar salud, identidad/generación de API/PostgreSQL y cabecera en navegador; cualquier escritura de demostración debe quedar limitada a un stream sintético. Conservar reporte e imagen/override de recuperación.

Estos puntos describen el trabajo posterior; el inventario no los ejecuta. Los valores observados en la Mac principal no sustituyen la evidencia de mac24.
