# Selección del motor y explicación académica

## Criterio confirmado por el usuario

El 20 de octubre de 2026 el usuario confirmó que importa obtener el mejor resultado posible, independientemente del framework. Los experimentos relacionados con el temario pueden permanecer como evidencia académica aunque no formen parte del motor que se despliegue. La explicación debe distinguir por qué se seleccionó un método de las hipótesis sobre sus resultados.

Esto reemplaza el requisito anterior de que el ranking principal fuera necesariamente el modelo PyTorch. Se mantienen ejecución local, presupuesto propio, Docker, portabilidad, datos verificables y comunicación entre Macs por GitHub. No se requiere cambiar de framework para adoptar el candidato actual.

**Selección técnica para el primer despliegue:** `recent_then_popular`, protocolo `recent-history-v1`. Es el mejor candidato medido en esta validación, no un óptimo global ni una garantía de rendimiento futuro. PyTorch y sus checkpoints permanecen como experimentos reproducibles; no se eliminan artefactos ni se reescriben resultados.

## Evidencia que decide la selección

La [revisión independiente de los ZIP recibidos](../evidence/mac24-recent-history-review-2026-10-20.json) conserva integridad, configuración, cohortes, recursos y contrastes. Los métodos de esta tabla se evaluaron sobre los mismos 72,019 compradores y 102,967 artículos:

| Candidato | MAP@12 | NDCG@12 | Recall@200 | Papel |
| --- | ---: | ---: | ---: | --- |
| Popularidad de 7 días | 0.00660410 | 0.01260462 | 0.16757920 | Referencia y respaldo |
| PyTorch, una época | 0.00576877 | 0.01120105 | 0.13893485 | Experimento académico |
| Historial + PyTorch | 0.02452712 | 0.03533274 | 0.17976476 | Experimento de aporte incremental |
| Historial + popularidad | 0.02458902 | 0.03549285 | 0.19078554 | Motor seleccionado para implementar |

Historial+popularidad mejora MAP frente a popularidad en +0.01798491, IC95% [0.01716408, 0.01882157]: +272.33% relativo. Los intervalos de NDCG y Recall también son positivos. No es una mejora de conversión comercial: no hay experimento online.

Agregar PyTorch al mismo prefijo no demuestra mejora de MAP: delta -0.00006190, IC95% [-0.00012972, 0.00000903]. NDCG y Recall sí se reducen con intervalos negativos. Por eso no se añade inferencia neuronal al recorrido principal de servicio. La variante de tres épocas también se conserva: empeoró MAP/NDCG frente a una época aunque mejoró Recall y cobertura.

## Qué podemos explicar y con qué certeza

| Observación o mecanismo | Interpretación respaldada | Límite |
| --- | --- | --- |
| El historial contiene los artículos concretos comprados recientemente | El motor aprovecha una señal de repetición de compra que se evalúa directamente; no excluye compras anteriores | No demuestra que 28 días sea la mejor ventana posible ni explica la intención del comprador |
| Cambiar el propietario del historial empeora métricas | En 2,224 compradores con historial, el historial correcto aporta MAP frente al promedio de diez permutaciones: IC95% [0.03871746, 0.05247017] | Es un control de personalización, no un experimento causal |
| 39,605 compradores no tienen historial reciente | El respaldo popular mantiene exactamente las métricas de popularidad en ese segmento; la mejora global proviene del segmento con historial | No se ha mejorado la personalización de ese 55% aproximado de compradores |
| Popularidad7d supera a popularidad histórica y al modelo aislado en esta validación | La popularidad reciente es una referencia exigente y útil en este corte temporal | La estacionalidad o cambios de catálogo son hipótesis; no están aislados causalmente |
| La torre de usuario recibe ID y cuatro agregados numéricos, no la lista de artículos recientes | El modelo actual no recibe explícitamente la misma señal que utiliza la regla seleccionada; su representación es una explicación plausible de la diferencia | No prueba que todas las redes neuronales sean inferiores ni atribuye la diferencia al framework |
| Tres épocas reducen MAP/NDCG | Más entrenamiento no resolvió la relevancia en esta configuración | No basta para diagnosticar sobreajuste, un problema concreto del optimizador o el número óptimo de épocas |

La descripción de la torre se puede contrastar con [el contrato de evaluación](evaluation.md) y [su implementación](../src/hm_recommender/model/two_tower.py); el ranking reciente está en [recent_history.py](../src/hm_recommender/model/recent_history.py).

El diagnóstico full consumió 132.132 s y 2.568 GiB de pico de contenedor, sin swap ni OOM. Incluye replay y controles: no representa el coste de una recomendación online ni permite afirmar que la nueva API será más rápida. Su latencia, errores y memoria se medirán al construirla.

## Experimentos del temario que conservamos

| Subtema | Evidencia real | Cómo se presenta |
| --- | --- | --- |
| 4.1 Retroalimentación y arranque en frío | Compras como señal implícita, usuarios desconocidos y respaldo popular | Implementado; no hay retroalimentación explícita de calificaciones |
| 4.2 Contenido | Categoría, grupo y color en la torre de artículos | Componente de un modelo híbrido; no llamarlo un recomendador puro basado en contenido |
| 4.3 Filtrado colaborativo | Aprendizaje de interacciones usuario–artículo | Experimento PyTorch entrenado con H&M, aunque no gane |
| 4.4 Factores latentes | Embeddings de 64 dimensiones y producto punto | Representaciones latentes aprendidas; no equivale a haber implementado SVD |
| 4.5 Evaluación | Cortes temporales, métricas, segmentos, cobertura, bootstrap y permutaciones | Parte central de la selección y de la explicación de resultados negativos |
| 4.6 Netflix | Comparación conceptual con datos de calificaciones | Contexto del informe, pendiente de desarrollo bibliográfico |
| 3 y 5, grafos | Todavía no hay PageRank, HITS o comunidades implementados | Extensión académica posible, con protocolo y presupuesto propios; Qdrant y Airflow no acreditan esos subtemas |
| 1 y 2 | No se implementaron PCA, NMF, t-SNE, Apriori o FP-Growth | No declarar cobertura por usar embeddings, frecuencias o historial |

La comparación, sus fallos y sus límites son parte del resultado académico. No hace falta introducir cada experimento en producción para demostrar que se implementó y evaluó correctamente. El informe final enlazará código, configuración, dataset/corte, commit, reportes y recursos; no sólo una gráfica de puntuaciones.

## Reglas para mantener una selección honesta

1. Conservar fijo el candidato actual durante el empaquetado y el servicio. No ajustar ventanas o desempates a partir de la prueba de despliegue.
2. Un candidato futuro se compara con el motor seleccionado, además de popularidad, sobre la misma población, corte, catálogo y denominadores. Fijar hipótesis y configuración antes de ejecutarlo.
3. MAP@12 es la métrica principal acordada para esta campaña; NDCG, Recall, cobertura, segmentos y coste son controles de decisión. Documentar cualquier compromiso entre métricas; una pérdida menor de entrenamiento no acredita una mejor recomendación.
4. No elegir un método por el nombre del framework, su complejidad o su presencia en el temario. Tampoco afirmar que el método más simple siempre gana.
5. Reservar test para la evaluación final del procedimiento congelado. Los reportes actuales conservan `quality_accepted=false`: selección de despliegue en validación y aceptación final son decisiones distintas. Con la estrategia congelada se reconstruirá el estado usando sólo datos anteriores al corte de test; no se reentrena una regla que no tiene pesos.

## Consecuencias para el despliegue

El [plan actualizado](superpowers/plans/2026-10-18-plataforma-local-hm.md) conserva T4–T8 y reemplaza la dependencia obligatoria de inferencia neuronal por un motor versionado de historial y popularidad. El objetivo industrial permanece: pipeline reproducible, publicación atómica, API, eventos, observabilidad, rollback y reproducción en otra Mac.

- **Paquete portable:** catálogo, historiales anteriores al corte, popularidad ordenada, reglas, manifiesto y evidencia. Sin pesos ni vectores obligatorios.
- **Servicio:** FastAPI carga una versión verificada y responde con su identidad, estrategia, orden y motivo de recomendación. PostgreSQL conserva versiones y, en T5, eventos.
- **Qdrant:** queda disponible para experimentos vectoriales, pero no será requisito del perfil `serve` de este motor. Sus volúmenes y la infraestructura existente se conservan.
- **Separación:** las imágenes y dependencias científicas actuales siguen funcionando. Reducir dependencias de servicio es trabajo de implementación, no una capacidad ya probada.
- **Intercambio:** código y reportes continúan por GitHub privado. Los ZIP actuales sólo contienen reportes. Inicialmente cada Mac reconstruye el paquete con su descarga propia de Kaggle; no se publican perfiles ni modelos mediante el protocolo actual.

El siguiente entregable es **T4-A: paquete de servicio y prueba de paridad con el diagnóstico**, seguido de **T4-B: API y activación**, antes de la demo y eventos de T5. Los comandos y contratos futuros del plan todavía no están implementados. No hace falta ejecutar otra campaña de mac24 para confirmar esta decisión; la próxima ejecución allí comprobará el código de T4 cuando esté listo.
