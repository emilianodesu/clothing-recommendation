# Cobertura del temario y límites de la entrega

La evidencia describe una red comercial comprador–artículo y una proyección de coocurrencia artículo–artículo. No constituye una red social de amistades. Los experimentos académicos se conservan aunque no sean el motor elegido. Q4 está cerrada y H seleccionado tras la [auditoría](../evidence/quality-decision-mac24.json); API, publicación y recuperación fueron auditadas en T4–T7; T8-A y T8-C están cerradas. T8-B conserva su limitación; la entrega documental/visual y las verificaciones posteriores de UI están disponibles en el [inventario vigente](delivery-status.md).

| Subtema | Implementación y evidencia | Alcance real |
| --- | --- | --- |
| 1.1 Método de potencias | Iteración de PageRank personalizado Q2, residual y convergencia | Aplicación iterativa del operador de transición con reinicio; no un estudio separado de valores/vectores propios |
| 1.2 SVD, 1.3 PCA, 1.4 NMF, 1.5 t-SNE | No implementados | Herramientas opcionales; embeddings PyTorch no acreditan estos métodos |
| 2.1 Mercado-canasta | Cestas semanales por comprador en grafo Q2 | Proxy explícito; el dataset no identifica pedidos reales |
| 2.2–2.6 Apriori, memoria/pasadas, FP-Growth, PrefixSpan | No implementados como algoritmos del temario | Procesamiento particionado y límites de memoria de ingeniería, no equivalencia con minería frecuente |
| 3.1 PageRank | PPR acotado Q2, convergencia/fallback y diez permutaciones | Control de hasta 5,000 compradores; no candidato promovido por una muestra |
| 3.2 HITS | No implementado | Pendiente si se requiere cubrir cada subtema |
| 3.3 Pesado de vínculos | Coocurrencia ponderada, decaimiento temporal, normalización y poda Q2 | Análisis de vínculos comerciales con reglas fijas |
| 3.4 Caminatas aleatorias / análisis espectral | Transiciones y reinicio de PPR | Caminatas cubiertas; clustering/descomposición espectral no implementados |
| 4.1 Retroalimentación y arranque en frío | Compras implícitas, segmentos por historial, respaldos de popularidad | No ratings explícitos; casos sin historial conservados en evaluación |
| 4.2 Basada en contenido | Torre de artículos PyTorch y afinidades de atributos en LightGBM | Componentes de contenido; sin benchmark separado de recomendador exclusivamente por contenido |
| 4.3 Filtrado colaborativo | Interacciones comprador–artículo y recuperación por coocurrencia G | Grafo de co-compra; no user-kNN como experimento separado |
| 4.4 Factor latente | Dos torres PyTorch, embeddings y producto escalar T3 | Representaciones latentes; no afirmar que se ejecutó ALS/SVD/NMF |
| 4.5 Evaluación | Cortes temporales, MAP/NDCG@12, Recall@200, segmentos, exposición, ablaciones y bootstrap Q4 | Test único T8-C cerrado; selección offline no equivale a impacto online |
| 4.6 Desafío Netflix | No reproducido | Contexto teórico, dataset y evaluación distintos |
| 5.1 Redes como grafos | Grafo comercial Q2, nodos/aristas/poda/aislados | Transferencia de representación; no relaciones sociales observadas |
| 5.2–5.4 Agrupamiento y comunidades (incluidas traslapadas) | No implementados | No inferir comunidades a partir de vecinos recuperados |
| 5.5 Similitud entre nodos | Pesos de coocurrencia y vecinos de artículos Q2 | Similitud basada en comportamiento, no prueba de estructura social |

La cobertura se concentra en 3.1, 3.3, caminatas de 3.4, sistemas de recomendación 4.1–4.5 y similitud 5.5. No se afirma cubrir todo el temario. Si la evaluación exige comunidades, debe acordarse un alcance adicional explícito; no añadirlo ahora para alterar la campaña congelada.

## Evidencia y explicación académica

- [Q1](../evidence/quality-q1-review.json): H supera a popularidad en tres semanas.
- [Q2](../evidence/quality-q2-review.json): G mejora MAP medio, pierde Recall; PPR es control acotado con prueba de personalización.
- [Q3](../evidence/quality-q3-review.json): RH conserva el pool y mejora MAP pero perjudica segmentos; RG ordena mejor un pool con menor recuperación. Mejor media no basta para promoción.
- [Selección](model-selection.md) y [Q4](quality-summary.md): criterios predefinidos, incertidumbre pareada y preferencia por coste entre resultados equivalentes.
- [T3 y tres épocas](three-epochs.md): el modelo latente no superó las referencias temporales recientes. Más épocas no mejoraron MAP; conservar el resultado negativo y evitar atribuirlo sólo a PyTorch.

API, eventos, programación y recuperación fueron auditados en T4–T7. T8-A acreditó 20 solicitudes/s y p95 14.099 ms en localhost; T8-C cerró test. T8-B conserva failed por solapamiento insuficiente: no se acredita concurrencia sostenida. Véanse [informe final](final-report.md) e [inventario de entrega](delivery-status.md).
