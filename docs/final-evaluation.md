# T8-C: evaluación final H/P

Estado: componente implementado y validado con datos sintéticos. **No ejecutar todavía sobre H&M.** Falta el lanzador operativo y su revisión; no hay comando mac24 autorizado en esta guía. El test real permanece reservado.

## Alcance cerrado antes de test

Protocolo `final-hp-v1`. H continúa seleccionado por Q4; P es la referencia. No se entrenan modelos, ajustan ventanas ni seleccionan métodos a partir del resultado final. Preparar estado con todas las transacciones estrictamente anteriores a 2020-09-16. Evaluar compras de `[2020-09-16, 2020-09-23)`, sin muestrear compradores. Historial28d y popularidad7d conservan orden, desempates, deduplicación y respaldo existentes.

Catálogo y perfiles se construyen antes de leer etiquetas. Se evalúan compradores con al menos una compra en el periodo; duplicados comprador/artículo cuentan como una etiqueta. Compradores sin historial permanecen en la población y reciben popularidad. Artículos relevantes ausentes del catálogo permanecen en el denominador. Usuarios sin compra de test no se agregan como ceros a la media.

Reportar MAP@12, NDCG@12 y Recall@200 macro por comprador, deltas H−P, cuatro segmentos, repetición/descubrimiento, exposición, catálogo y denominadores. Son resultados descriptivos de generalización: no constituyen una nueva selección ni una prueba de superioridad estadística. Los archivos por comprador permanecen privados; sólo agregados viajan por GitHub.

## Componente y pruebas

`src/hm_recommender/quality/final.py` usa contratos separados para la ventana final. Reutiliza construcción, rankings y métricas de calidad mediante tipos de artefacto explícitos. `FoldSpec` y el constructor ordinario siguen rechazando test. El componente exige una carpeta nueva, conserva la reserva ante fallo y no reanuda automáticamente. Esa protección local **no sustituye un ledger global**.

Las pruebas `tests/integration/test_final_evaluation.py` crean CSV/ZIP sintéticos temporales, sin montar datos H&M. Comprueban extremos temporales, duplicados, compradores nuevos/inactivos, artículos no disponibles, segmentos, orden H frente a P, denominadores y rechazo de reutilización de destino. La regresión V1–V3 conserva las métricas y rankings previos.

## Trabajo operativo siguiente

1. Añadir reserva global exclusiva de test ligada al commit, protocolo, dataset, evidencia Q4 y auditorías T8. Verificar originales y ausencia de ejecuciones previas antes de leer etiquetas.
2. Congelar presupuesto y transición de servicios. No añadir un job pesado sobre los 16.75 GiB de límites activos. Diseñar mantenimiento secuencial con recuperación de IDs exactos, conservando volúmenes, contadores y H activo. No reutilizar sin adaptación la recuperación T8-B, que sólo contempla tres auxiliares.
3. Ejecutar la suite sintética dentro de la imagen nueva antes de reservar test. Registrar digest de imagen, fuentes, límites efectivos, duración y cgroups. Sin red en el worker y datos montados en lectura.
4. Añadir reporte agregado, validador estricto e intercambio GitHub. Ante fallo conservar reserva y evidencia; no reintentar automáticamente ni cambiar criterios mirando test.
5. Entregar un único comando fijado a SHA completo. Tras la ejecución, auditar reporte y conservar resultados adversos. No activar el paquete de test ni modificar el servicio.

## Límite operativo T8-B

La auditoría T8-B conserva `failed`: sólo hubo 101 llegadas durante 5.061 s de worker, inferiores al mínimo fijado. No se repetirá para alargar artificialmente el trabajo. T8-A acredita el servicio a 20 solicitudes/s en su configuración ensayada; T8-B no acredita concurrencia sostenida. La entrega documentará esta limitación y una política de mantenimiento secuencial para trabajos pesados. Esto no cambia el estado del experimento histórico ni convierte T8 completo en aprobado.

Verificación local: 223 pruebas host, tres bootstrap nativas y 106 componentes Docker pasaron; 61 integraciones requieren servicios no iniciados. Dos bootstrap fallaron por certificados dentro de jobs y pasaron en host; el intento conjunto inicial también carecía de Git/evidencia montada para pruebas host. Detalle en `evidence/final-evaluation-local.json`. No equivale a una suite con PostgreSQL ni a ejecución mac24.
