# Descarga de H&M reiniciada desde cero

**La nueva descarga terminó y pasó los 16 controles de validación.**

Finalización: 30 de septiembre de 2026 a las 22:28, Ciudad de México
(`2026-10-01T04:28:26Z`). ZIP de 30,810,293,747 bytes, 105,104 archivos, 105,100
imágenes decodificadas sin errores y 31,788,324 transacciones leídas y comprobadas.
Las siete celdas del notebook terminaron sin errores. No faltan archivos respecto
al inventario oficial ni falló la comprobación CRC. El SHA-256 coincide con la
copia anterior, cuya evidencia se conserva en el historial.

El catálogo tiene 105,542 artículos y 1,371,980 clientes. Los 442 artículos sin
imagen pertenecen a la fuente original: no son archivos perdidos en la descarga.
La prueba valida los datos; no equivale a entrenar el modelo de PyTorch ni medir
la latencia de servicio.

El [resumen versionado](../evidence/dataset-validation-summary.json) conserva
conteos e integridad sin muestras de identificadores. El resultado detallado,
notebook y registro del reinicio son archivos locales fuera de Git; los pasos
de consulta siguientes describen esa ejecución histórica, no requisitos para
instalar la infraestructura.

El 1 de octubre de 2026 a las 04:03 UTC se eliminó el ZIP anterior, de 30,810,293,747 bytes, a petición del usuario y se inició una descarga nueva. No se conservaron fragmentos del ZIP para esta descarga.

## Consultar el estado actual

- `estado_descarga.json`: `running` significa descarga activa; `finished`, transferencia terminada; `failed`, error.
- `resultado_validacion.json`: sólo `status: validated_complete_and_usable` junto con `complete_and_usable: true` confirma que **esta nueva copia** terminó y pasó todos los controles.
- `../notebooks/validacion_hm_temporal.ipynb`: recibirá las salidas de esta ejecución cuando finalice.
- `reinicio_descarga.json`: evidencia del borrado y arranque desde cero.

El notebook valida automáticamente el inventario, el CRC de todos los archivos, todas las imágenes y todas las filas de los CSV después de terminar la transferencia. El ZIP queda en `../data/kaggle_hm/h-and-m-personalized-fashion-recommendations.zip`.

Los resultados anteriores se conservan en `history/20261001T040306Z/`. Esos resultados históricos no acreditan la integridad de la nueva copia. El inventario oficial completo puede reutilizarse durante 24 horas; el archivo del dataset se volvió a descargar desde cero.
