# Selección del motor y explicación académica

## Criterio confirmado por el usuario

El 20 de octubre de 2026 el usuario confirmó que importa obtener el mejor resultado posible, independientemente del framework. Los experimentos relacionados con el temario pueden permanecer como evidencia académica aunque no formen parte del motor que se despliegue. La explicación debe distinguir por qué se seleccionó un método de las hipótesis sobre sus resultados.

Esto reemplaza el requisito anterior de que el ranking principal fuera necesariamente el modelo PyTorch. Se mantienen ejecución local, presupuesto propio, Docker, portabilidad, datos verificables y comunicación entre Macs por GitHub. No se requiere cambiar de framework para adoptar el candidato actual.

**Cierre de la ampliación del mismo día:** el usuario aprobó ampliar la validación temporal, grafos y ranking. Tras Q4, `recent_then_popular`, reglas `recent-history-v1`, queda **seleccionado como H bajo `quality-v1`**, pendiente de implementación del servicio y aceptación final. Es el candidato preferido con la evidencia actual, no un óptimo global ni garantía de rendimiento futuro. PyTorch y sus checkpoints permanecen como experimentos reproducibles; no se eliminan artefactos ni se reescriben resultados.

El [diseño `quality-v1`](superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md) fija tres cortes, candidatos, recursos, umbrales y límites; el [plan Q1–Q4](superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md) guía el trabajo. Q1, Q2 y Q3 están cerradas operativamente tras descargar y revisar cinco, cinco y diez reportes mac24 respectivamente: [Q1](../evidence/quality-q1-review.json), [Q2](../evidence/quality-q2-review.json) y [Q3](../evidence/quality-q3-review.json). [Q4 está cerrada y auditada](quality-summary.md); ningún candidato nuevo cumple todos los controles deterministas, aunque mejore MAP promedio.

## Evidencia que decide la selección

La [revisión independiente de los ZIP recibidos](../evidence/mac24-recent-history-review-2026-10-20.json) conserva integridad, configuración, cohortes, recursos y contrastes. Los métodos de esta tabla se evaluaron sobre los mismos 72,019 compradores y 102,967 artículos:

| Candidato | MAP@12 | NDCG@12 | Recall@200 | Papel |
| --- | ---: | ---: | ---: | --- |
| Popularidad de 7 días | 0.00660410 | 0.01260462 | 0.16757920 | Referencia y respaldo |
| PyTorch, una época | 0.00576877 | 0.01120105 | 0.13893485 | Experimento académico |
| Historial + PyTorch | 0.02452712 | 0.03533274 | 0.17976476 | Experimento de aporte incremental |
| Historial + popularidad | 0.02458902 | 0.03549285 | 0.19078554 | Motor seleccionado H |

Historial+popularidad mejora MAP frente a popularidad en +0.01798491, IC95% [0.01716408, 0.01882157]: +272.33% relativo. Los intervalos de NDCG y Recall también son positivos. No es una mejora de conversión comercial: no hay experimento online.

Agregar PyTorch al mismo prefijo no demuestra mejora de MAP: delta -0.00006190, IC95% [-0.00012972, 0.00000903]. NDCG y Recall sí se reducen con intervalos negativos. Por eso no se añade inferencia neuronal al recorrido principal de servicio. La variante de tres épocas también se conserva: empeoró MAP/NDCG frente a una época aunque mejoró Recall y cobertura.

## Confirmación temporal Q1 y límite de descubrimiento

La [revisión Q1](../evidence/quality-q1-review.json) confirma que H supera a P en MAP/NDCG/Recall en tres semanas completas. MAP H/P: V1 0.01936354/0.00682845; V2 0.02272145/0.00673213; V3 0.02458902/0.00660410. El promedio no ponderado entre semanas mejora MAP +230.65%, sin que esto constituya un nuevo contraste con intervalo de confianza. V3 coincide con el reporte histórico original.

La ventaja global proviene de la recompra. En la evaluación de artículos no comprados anteriormente, MAP de H es 26.88–30.18% inferior a P. Además, 55–56% de compradores de cada semana carecen de historial28 y conservan exactamente P. Esta evidencia justificó continuar el contraste de grafo/ranking ya fijado, manteniendo H como referencia y sin cambiar ventanas ni umbrales tras observar resultados.

## Resultado Q2: mejor orden inicial, menor recuperación

Los tres folds completos de G mejoran MAP y NDCG frente a H. La media simple entre semanas pasa de 0.02222467 a 0.02366238 en MAP (+6.47%) y de 0.03300888 a 0.03533201 en NDCG (+7.04%). Recall@200 baja de 0.19526924 a 0.18669113 (−4.39%), incumpliendo el máximo de pérdida relativa del 1% fijado antes de ejecutar. G no puede sustituir H bajo la regla vigente; Q4 confirmó esa exclusión con un intervalo positivo de mejora MAP.

La caída de Recall se concentra en historia de 29–90 días: −19.13%/−19.70%/−22.57% en V1/V2/V3. En ese segmento mejora MAP; en historia reciente28 mejoran ambas métricas. Sin historia90, G conserva H. La cobertura top12 casi se duplica, pero esa mayor variedad no implica mayor recuperación de compras relevantes. La [guía Q2](graph-quality.md) detalla métricas, costes y límites de la auditoría.

El control PPR converge para todos los compradores con semillas de su muestra; usar la personalización correcta mejora las tres métricas respecto a diez permutaciones. No muestra una ventaja consistente de MAP frente a G en esa misma muestra y no permite seleccionar un motor full. Acredita un experimento de PageRank personalizado acotado, no superioridad universal ni causalidad.

Q3 conservó RH y RG conforme al protocolo: el primero reordena H y el segundo G. **Ordenar el mismo pool mantiene Recall@200:** RH mejora MAP sin sacrificar la recuperación de H; RG hereda el incumplimiento de G y conserva valor como ablación académica. No se ampliará el pool ni se relajará el umbral después de ver estos resultados. No repetir Q1/Q2 ni usar test.

## Resultado Q3: mejora global con regresiones por segmento

La [auditoría de los diez ZIP Q3](../evidence/quality-q3-review.json) confirma entrenamiento y evaluación temporal completos, parámetros congelados, referencias y recursos. RH mejora MAP medio de 0.02222467 a 0.02311949 (+4.03%) y NDCG +5.34%, conservando Recall. RG alcanza MAP 0.02544361 (+14.48% frente a H, +7.53% frente a G) y NDCG +16.67% frente a H, pero mantiene la pérdida de Recall −4.39% de G.

El control por segmentos impide promover RH: en V1 pierde MAP −27.66% para compradores sin compras previas y −16.19% con sólo historia anterior a 90 días; en V3 pierde −11.62% y −9.93% respectivamente. Todos esos segmentos superan 1,000 compradores y exceden la pérdida máxima del 5% fijada antes de medir. RG también incumple segmentos (V1 −22.24%/−19.24%; V3 sin historial −6.48%). V2 mejora esos segmentos; el promedio global oculta esa variación temporal.

Aprender el orden aporta señal, pero también cambia el respaldo que H conserva para usuarios sin historia90. Es un mecanismo compatible con las regresiones observadas, no un diagnóstico causal de sobreajuste ni una comparación universal entre frameworks. La [guía Q3](ranker-quality.md) incluye las ablaciones, recompra/descubrimiento, cobertura y costes. Las seis corridas full sumaron 44.58 minutos, con cgroup máximo 3.667202 GiB y sin swap/OOM.

H queda seleccionado. G falla Recall; RH falla segmentos; RG falla ambos. Q4 completó los intervalos pareados por comprador y la decisión formal sin cambiar esos criterios. Un intervalo global positivo no vuelve elegible un candidato que ya incumple un límite determinista. No se propone otro entrenamiento, fallback o búsqueda de parámetros a partir de estos resultados.

## Cierre Q4: mejora promedio significativa y selección bajo restricciones

La [auditoría Q4](../evidence/quality-decision-mac24.json) reconcilia el reporte full con los doce ZIP fuente y quince evaluaciones. Con 190,944 compradores únicos, tres semanas y 2,000 réplicas agrupadas por comprador, los IC98.3333% del delta MAP son G [0.00126171, 0.00162986], RH [0.00062544, 0.00114128] y RG [0.00294479, 0.00348630]. Todos son positivos, al igual que sus mejoras puntuales en las tres semanas. La incertidumbre global no explica ni elimina los incumplimientos de Recall/segmentos.

La regla previamente fijada conserva H cuando ningún candidato es elegible. RG logra el mayor MAP medio; seleccionar H no significa negar esa ventaja ni afirmar que sea el mejor algoritmo posible. Se prioriza el conjunto de requisitos acordados, con compras anteriores permitidas y el respaldo de popularidad intacto. H mantiene también su limitación de descubrimiento descrita arriba. La selección exploratoria está cerrada; test y aceptación final no se ejecutaron.

Los intervalos se calcularon en mac24. Aquí se verificaron contrato, fuentes, huellas y coherencia de deltas/criterios, sin recibir métricas individuales ni recalcular réplicas. La [guía Q4](quality-summary.md) registra recursos y la congelación del procedimiento.

## Qué podemos explicar y con qué certeza

| Observación o mecanismo | Interpretación respaldada | Límite |
| --- | --- | --- |
| El historial contiene los artículos concretos comprados recientemente | El motor aprovecha una señal de repetición de compra que se evalúa directamente; no excluye compras anteriores | No demuestra que 28 días sea la mejor ventana posible ni explica la intención del comprador |
| Cambiar el propietario del historial empeora métricas | En 2,224 compradores con historial, el historial correcto aporta MAP frente al promedio de diez permutaciones: IC95% [0.03871746, 0.05247017] | Es un control de personalización, no un experimento causal |
| En el diagnóstico histórico V3, 39,605 compradores no tienen historial reciente | H mantiene exactamente popularidad en ese segmento; su mejora global proviene del historial28 | Q2 sí personaliza parte de ese segmento con historia29–90: mejora MAP, pero pierde Recall; sin historia90 mantiene H |
| Popularidad7d supera a popularidad histórica y al modelo aislado en esta validación | La popularidad reciente es una referencia exigente y útil en este corte temporal | La estacionalidad o cambios de catálogo son hipótesis; no están aislados causalmente |
| La torre de usuario recibe ID y cuatro agregados numéricos, no la lista de artículos recientes | El modelo actual no recibe explícitamente la misma señal que utiliza la regla seleccionada; su representación es una explicación plausible de la diferencia | No prueba que todas las redes neuronales sean inferiores ni atribuye la diferencia al framework |
| Tres épocas reducen MAP/NDCG | Más entrenamiento no resolvió la relevancia en esta configuración | No basta para diagnosticar sobreajuste, un problema concreto del optimizador o el número óptimo de épocas |
| RH/RG mejoran el MAP global, pero pierden en algunos segmentos/cortes | La regla de selección debe considerar heterogeneidad temporal y arranque en frío, además de promedios | No demuestra que LightGBM sea peor en general ni autoriza ajustar un respaldo después de ver los folds |

La descripción de la torre se puede contrastar con [el contrato de evaluación](evaluation.md) y [su implementación](../src/hm_recommender/model/two_tower.py); el ranking reciente está en [recent_history.py](../src/hm_recommender/model/recent_history.py).

El diagnóstico full consumió 132.132 s y 2.568 GiB de pico de contenedor, sin swap ni OOM. Incluye replay y controles: no representa el coste de una recomendación online ni permite afirmar que la nueva API será más rápida. Su latencia, errores y memoria se medirán al construirla.

## Experimentos del temario que conservamos

| Subtema | Evidencia real | Cómo se presenta |
| --- | --- | --- |
| 4.1 Retroalimentación y arranque en frío | Compras como señal implícita, usuarios desconocidos y respaldo popular | Implementado; no hay retroalimentación explícita de calificaciones |
| 4.2 Contenido | Categoría, grupo y color en la torre de artículos y afinidades de contenido entre las 20 características Q3 | Componentes híbridos evaluados; no llamarlos un recomendador puro basado en contenido |
| 4.3 Filtrado colaborativo | Aprendizaje de interacciones usuario–artículo | Experimento PyTorch entrenado con H&M, aunque no gane |
| 4.4 Factores latentes | Embeddings de 64 dimensiones y producto punto | Representaciones latentes aprendidas; no equivale a haber implementado SVD |
| 4.5 Evaluación | Cortes temporales, métricas, segmentos, cobertura, bootstrap y permutaciones | Parte central de la selección y de la explicación de resultados negativos |
| 4.6 Netflix | Comparación conceptual con datos de calificaciones | Contexto del informe, pendiente de desarrollo bibliográfico |
| 3.1, 3.3, 3.4 y 5.5, grafos | Pesos/similitud y vecinos evaluados en tres folds completos; PageRank personalizado y caminatas con reinicio evaluados en muestras acotadas | Q2 cerrada con resultados y recursos verificados; no implementa HITS, análisis espectral completo o comunidades ni supone una red social observada. Qdrant y Airflow no acreditan esos subtemas |
| 1 y 2 | No se implementaron PCA, NMF, t-SNE, Apriori o FP-Growth | No declarar cobertura por usar embeddings, frecuencias o historial |

La comparación, sus fallos y sus límites son parte del resultado académico. No hace falta introducir cada experimento en producción para demostrar que se implementó y evaluó correctamente. El informe final enlazará código, configuración, dataset/corte, commit, reportes y recursos; no sólo una gráfica de puntuaciones.

## Reglas para mantener una selección honesta

1. Conservar fijo H como referencia en Q1–Q4. Q4 congela el ganador antes del empaquetado; no ajustar ventanas o desempates a partir de la prueba de despliegue.
2. Un candidato futuro se compara con el motor seleccionado, además de popularidad, sobre la misma población, corte, catálogo y denominadores. Fijar hipótesis y configuración antes de ejecutarlo.
3. MAP@12 es la métrica principal acordada para esta campaña; NDCG, Recall, cobertura, segmentos y coste son controles de decisión. Documentar cualquier compromiso entre métricas; una pérdida menor de entrenamiento no acredita una mejor recomendación.
4. No elegir un método por el nombre del framework, su complejidad o su presencia en el temario. Tampoco afirmar que el método más simple siempre gana.
5. Reservar test para la evaluación final del procedimiento congelado. Los reportes actuales conservan `quality_accepted=false`: selección en validación y aceptación final son decisiones distintas. Se reconstruirá el estado usando sólo datos anteriores al corte de test; si gana un ranker se hará el refit predeclarado, mientras que una regla sin pesos sólo requiere preparar su estado.

## Consecuencias para el despliegue

El [plan de plataforma](superpowers/plans/2026-10-18-plataforma-local-hm.md) conserva T4–T8. Q4 confirma el diseño de historial y popularidad para H, con preparación de estado sin entrenamiento. El objetivo industrial permanece: pipeline reproducible, publicación atómica, API, eventos, observabilidad, rollback y reproducción en otra Mac. La siguiente lista describe el diseño disponible para H, no requisitos invariables de los candidatos de grafo/ranking:

- **Paquete portable:** catálogo, historiales anteriores al corte, popularidad ordenada, reglas, manifiesto y evidencia. Sin pesos ni vectores obligatorios.
- **Servicio:** FastAPI carga una versión verificada y responde con su identidad, estrategia, orden y motivo de recomendación. PostgreSQL conserva versiones y, en T5, eventos.
- **Qdrant:** queda disponible para experimentos vectoriales, pero no será requisito del perfil `serve` de este motor. Sus volúmenes y la infraestructura existente se conservan.
- **Separación:** las imágenes y dependencias científicas actuales siguen funcionando. Reducir dependencias de servicio es trabajo de implementación, no una capacidad ya probada.
- **Intercambio:** código y reportes continúan por GitHub privado. Los ZIP actuales sólo contienen reportes. Inicialmente cada Mac reconstruye el paquete con su descarga propia de Kaggle; no se publican perfiles ni modelos mediante el protocolo actual.

Q1–Q4 y T4-A están cerradas. La [auditoría T4-A](../evidence/releases-review-mac24.json) confirma los reportes demo/H&M y su paridad con Q1-V3; el [paquete portable H](releases.md) queda preparado. Sigue **implementar T4-B: API, activación y rollback**, y después T5–T8. Conservar artefactos, experimentos y presupuesto; no repetir campañas. La [cobertura académica](course-coverage.md) distingue lo implementado de lo pendiente. El servicio tendrá que medirse; el tiempo offline no acredita latencia, despliegue o aceptación final.
