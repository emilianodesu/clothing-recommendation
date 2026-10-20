# Calidad temporal, grafos y ranking para H&M

**Estado:** diseño y planificación, 20 de octubre de 2026. El usuario aceptó ampliar la validación antes de cerrar el motor. No se han implementado ni ejecutado estos experimentos. El [plan de implementación](../plans/2026-10-20-calidad-temporal-grafos-ranking.md) concreta archivos, interfaces, pruebas y entregas.

## Propósito y alcance aprobado

Obtener el mejor resultado defendible entre candidatos evaluados dentro de los recursos locales, combinando recomendaciones y análisis de vínculos. La plataforma reproducible sigue siendo parte central de la entrega; la investigación tendrá un cierre explícito. No se promete un óptimo global.

El usuario acordó tres pasos: evaluar estabilidad temporal, probar recuperación mediante grafos y entrenar un modelo de ordenamiento. Historial28d+popularidad7d pasa de selección para el primer despliegue a **referencia provisional H**. Los experimentos PyTorch y sus archivos permanecen intactos. Se conservan Docker local, CPU, PostgreSQL, GitHub privado, datos Kaggle propios, ausencia de Hopsworks y de nube de pago. No se cambia de framework por preferencia: LightGBM se incorpora para un objetivo de ranking específico.

Los cortes, límites, parámetros y umbrales numéricos siguientes son decisiones técnicas del agente para concretar ese alcance. Deben quedar versionados antes de los primeros resultados nuevos; no son valores óptimos demostrados ni preferencias expresadas por el usuario.

## Evidencia de partida

La [revisión recibida](../../../evidence/mac24-recent-history-review-2026-10-20.json) acredita, para 72,019 compradores y 102,967 artículos, MAP@12 de H = 0.02458902 y Recall@200 = 0.19078554. Popularidad7d obtiene MAP@12 = 0.00660410. Añadir el modelo no demuestra mejora de MAP y reduce NDCG/Recall. De los compradores, 39,605 no tienen compras en los 28 días anteriores y reciben popularidad.

Esto no demuestra robustez en otras semanas ni que esos compradores carezcan de historia más antigua. La primera medición nueva separará esos casos. No se reemplazan ZIP, métricas o banderas históricas; la aceptación final sigue pendiente.

## Entregas y orden

| Entrega | Resultado verificable | Dependencia |
| --- | --- | --- |
| Q1 | Evaluador con cortes explícitos; popularidad P e historial H en tres semanas; paridad con el resultado conocido | Datos T2 existentes |
| Q2 | Grafo de artículos, recuperación por vecinos a escala completa y control académico de PageRank personalizado | Q1 aceptada operativamente |
| Q3 | Dos ablaciones de un único procedimiento LightGBM: ordenar candidatos de H y ordenar candidatos ampliados por el grafo | Q2 válida, aunque su calidad sea peor |
| Q4 | Comparación, selección congelada, ficha del motor y adaptación concreta de T4 | Reportes Q1–Q3 completos o limitaciones explícitas |
| T4–T8 | Paquete/API, eventos, operación, reproducción, test final y rendimiento | Decisión Q4 |

Una métrica peor no detiene Q2/Q3 ni autoriza modificar parámetros. Un fallo operativo detiene la secuencia, se publica y se diagnostica. La calidad se decide después de recibir todos los cortes requeridos. No iniciar T4 mientras se construye esta campaña.

## Protocolo temporal `quality-v1`

| Corte exclusivo de características | Etiquetas de evaluación | Papel |
| --- | --- | --- |
| 2020-08-26 | [2020-08-26, 2020-09-02) | V1 |
| 2020-09-02 | [2020-09-02, 2020-09-09) | V2 |
| 2020-09-09 | [2020-09-09, 2020-09-16) | V3; paridad histórica |
| 2020-09-16 | [2020-09-16, 2020-09-23) | Test reservado; fuera de los comandos de campaña |

- Todo catálogo, vocabulario, popularidad, perfil, arista y característica se construye con `t_dat < cutoff`. Los intervalos de historia incluyen su inicio y excluyen el corte. Ninguna etiqueta entra en recuperación o generación de características.
- No reutilizar perfiles de septiembre 9 en agosto, ni el checkpoint entrenado hasta septiembre 9 en V1/V2. La campaña no vuelve a entrenar dos torres. Sus resultados originales se presentan con su corte original, sin rellenar una tabla temporal con comparaciones contaminadas.
- Reutilizar Parquet de T2 en lectura; escribir snapshots nuevos con fuente/corte/protocolo en su identidad. Los archivos mensuales pueden contener fechas futuras: el filtro temporal precede a toda agregación. No derivar etiquetas, conteos de evaluación o estadísticas de test durante Q1–Q4.
- Catálogo: artículos con primera compra observada antes del corte. Tipo, grupo y color del archivo de artículos se tratan como atributos estáticos; no tenemos sus versiones históricas. No usar metadatos de clientes potencialmente actualizados después del corte. Disponibilidad real e inventario no están observados.
- Evaluación full: todos los compradores de la semana, incluidos desconocidos y compradores cuyo único relevante esté fuera del catálogo. Los relevantes fuera del catálogo siguen en denominadores; separar el techo recuperable por disponibilidad. No excluir compras previas de las recomendaciones.
- Cada método devuelve hasta `min(200, catalog_size)` artículos únicos; top12 se evalúa sobre el mismo resultado. Mismos compradores, catálogo, etiquetas y denominadores por corte. Orden determinista: score descendente y `article_id` ascendente cuando no haya un desempate más específico.
- Pilotos: selección estable por SHA-256 de semilla 42 e ID, como el proyecto actual; hasta 2,000 compradores de evaluación. Su población se declara y nunca sustituye full. Los constructores globales usan todos los eventos de sus ventanas, incluso en el piloto H&M; el piloto acota compradores a recomendar y grupos a entrenar, no finge haber medido memoria del grafo con una muestra pequeña.
- V3 debe reproducir IDs/orden de H y P con el kernel histórico, y métricas completas con `atol=1e-10, rtol=0`. Comparar contra el ZIP original de mac24, no sólo contra decimales redondeados del resumen Git.

## Candidatos y preguntas

| ID | Método | Pregunta |
| --- | --- | --- |
| P | Popularidad7d | Referencia no personalizada |
| H | Historial28d, completado con P | ¿Se mantiene la ventaja actual? |
| G | Historial28d, completado con mezcla fija de vecinos del grafo y P | ¿Las relaciones entre artículos mejoran recuperación y top12? |
| RH | LightGBM ordena exactamente el conjunto de 200 candidatos de H | ¿Aprender el orden ayuda sin ampliar recuperación? |
| RG | El mismo procedimiento LightGBM ordena exactamente los candidatos de G | ¿El conjunto ampliado más ordenamiento aprendido ayuda? |

P/H no se ajustan. G, RH y RG son los tres candidatos adicionales elegibles para promoción, agrupados en dos familias (grafo y ranking). No hay búsqueda de ventanas, pesos, arquitecturas ni frameworks. RH y RG tienen igual Recall@200 que H y G respectivamente: si no coincide, hay un error de población o candidatos. Sólo cambian el orden.

### Grafo y recuperación G

1. Usar transacciones de `[cutoff-90d, cutoff)`. Una cesta de coocurrencia es `(customer_id, floor((t_dat-(cutoff-90d))/7d))`; no llamarla carrito real, porque el dataset no trae ID de pedido.
2. Conservar artículos únicos por cesta. Limitar a 50 por última compra descendente, frecuencia descendente, ID ascendente. Registrar cestas y artículos descartados por ese límite. El apoyo de un artículo es el número de cestas retenidas que lo contienen. `cooc(i,j)` cuenta cestas retenidas con ambos; sin autorrelaciones.
3. Peso `w(i,j)=cooc(i,j)/sqrt(support(i)*support(j))`. Retener por origen los 50 vecinos de mayor peso, empate por ID. La poda produce un grafo dirigido aunque la coocurrencia original sea simétrica. Mantener también nodos aislados del catálogo. Particionar la acumulación en disco; evitar una matriz catálogo×catálogo densa o materializar todas las parejas en RAM.
4. Semillas del usuario: hasta 50 artículos únicos de sus 90 días, con el mismo orden de recencia/frecuencia/ID. Peso de semilla `log1p(frequency90)*2**(-recency_days/14)`, normalizado para sumar uno. Puntuar vecinos por suma de peso de semilla por peso de arista; ordenar y recuperar hasta 200. Las aristas y las semillas se construyen sin etiquetas.
5. G conserva únicamente el **prefijo de artículos comprados en 28 días**, ordenado como H, hasta 200; no conserva la salida de H ya completada con popularidad. Para completar los espacios restantes, fusionar las listas de vecinos y P mediante `sum(1/(60+rank))`, con rangos desde 1, contribución cero si falta en una lista e ID ascendente en empate. Deduplicar contra el prefijo. Si no hay vecinos, G debe coincidir exactamente con H. Artículos con score de grafo cero sólo entran mediante P.
6. El grafo usa todos los eventos elegibles de su ventana, con las podas declaradas. No llamarlo entrenamiento sobre los dos años completos: T2 procesa todo H&M y la evidencia PyTorch ya conserva entrenamiento masivo; cada nuevo método documenta las filas realmente usadas.

### PageRank personalizado: control académico acotado

Aplicar a una cohorte fija de hasta 5,000 compradores por corte, elegida por hash entre todos los compradores antes de separar por disponibilidad de semillas. No usar su resultado para elegir motor: no es una evaluación full. Comparar H, G y la variante PPR en esa misma cohorte; informar usuarios sin semillas y respaldos.

Por usuario, conservar semillas y completar hasta 512 nodos con los vecinos de mayor score directo. Ejecutar PageRank en ese subgrafo inducido usando las aristas dirigidas ponderadas, `alpha=0.85`, distribución de reinicio proporcional a las semillas, masa de nodos colgantes hacia esa misma distribución, arranque en semillas, máximo 100 iteraciones y residuo L1 global entre iteraciones menor o igual a `1e-6`. No multiplicar la tolerancia por número de nodos. No es PageRank exacto del grafo completo.

La lista PPR reemplaza sólo la fuente de vecinos del paso 5; prefijo, fusión y popularidad permanecen iguales. Registrar nodos, aristas, iteraciones, residuo y truncación. Una falta de convergencia usa G como respaldo **declarado**, se cuenta y no se presenta como PageRank convergente. Prueba de referencia en grafos pequeños con solución lineal independiente; control por diez permutaciones de listas derivadas de semillas entre usuarios con semillas, sin propietarios fijos (semillas 42–51). Reutilizar las listas PPR ya calculadas: en un grafo fijo equivale a sustituir las semillas por las del donante; mantener el prefijo28d del receptor y popularidad fijos. Así se controla el aporte personalizado de esa fuente sin recalcular PageRank diez veces. Reportar métricas normales y deltas frente al promedio permutado, sin usar ese contraste para promoción. Con menos de dos usuarios con semillas registrar control no aplicable. No exportar identidades.

Este alcance permite cubrir PageRank/caminatas sin comprometer una matriz usuario×catálogo. Ampliar PPR a full o añadir comunidades sería una campaña posterior, no una selección a partir de mejores métricas de la muestra.

### Ordenamiento con LightGBM

Cada fold externo se entrena desde cero con las cuatro semanas completas anteriores. Los candidatos y características de **cada semana de entrenamiento** se reconstruyen al inicio de esa semana. Grupo de ranking = comprador y semana; etiqueta binaria = compró ese candidato en los siete días siguientes. No inyectar positivos ausentes del recuperador.

| Fold externo | Inicios de semanas de entrenamiento | Validación interna para early stopping |
| --- | --- | --- |
| V1 | 2020-07-29, 08-05, 08-12, 08-19 | 2020-08-19 |
| V2 | 2020-08-05, 08-12, 08-19, 08-26 | 2020-08-26 |
| V3 | 2020-08-12, 08-19, 08-26, 09-02 | 2020-09-02 |

Primero ajustar con las tres primeras semanas y detener con la cuarta; luego entrenar desde cero con las cuatro y exactamente el número de iteraciones elegido internamente. No usar V1/V2/V3 para early stopping de su propio modelo. Que V1 pase a ser pasado de V2 es válido; cada fold simula información disponible en su fecha.

Características numéricas v1, ordenadas y versionadas: frecuencia usuario–artículo 28/90d; recencia de ese par (91 si ausente en 90d); compras totales de usuario 28/90d; recencia de usuario (91 si ausente); popularidad de artículo 7/28/90d; tendencia `count7/(count28/4+1)`; fracción de compras del usuario en 90d por tipo/grupo/color del candidato (0 sin historia); score directo del grafo; posición en historia, vecinos y popularidad (201 si ausente de top200); tres indicadores de pertenencia a esas fuentes. Son 20 columnas, sin IDs numéricos arbitrarios. Atributo ausente se representa como categoría ausente consistente. No hay variables de precio ni demografía en v1.

Para ajuste/refit: usar todos los grupos de las semanas elegibles; conservar todos los positivos presentes y hasta 20 negativos por grupo, elegidos por hash de semilla/semana/usuario/artículo. Excluir del ajuste grupos sin positivos o sin negativos y reportar sus cantidades y positivos no recuperados. La evaluación interna usa los 200 candidatos de **todos** sus compradores; la externa también, sin excluir grupos sin aciertos. No interpretar scores como probabilidades calibradas.

Configuración única para RH/RG: objetivo `lambdarank`, `label_gain=[0,1]`, `metric='None'` y evaluador personalizado `project_ndcg_at_12`, `lambdarank_truncation_level=15`, hasta 300 árboles, `learning_rate=0.05`, `num_leaves=31`, `min_data_in_leaf=100`, `max_bin=127`, `lambda_l2=1`, `feature_fraction=1`, `bagging_fraction=1`, `bagging_freq=0`, semilla 42 en todas las opciones aleatorias, CPU con 2 hilos, `deterministic=true`, `force_col_wise=true`, early stopping de 30 iteraciones. El evaluador interno usa todos los relevantes del comprador en el IDCG, incluso fuera del pool/catálogo, y devuelve cero si no hay aciertos; promedia sobre todos los compradores de esa semana. No usar la NDCG nativa sobre candidatos para decidir iteraciones. MAP@12 continúa siendo la métrica externa de selección. Los valores pueden causar underfitting: se documenta el resultado, no se ajustan mirando el fold externo.

Los grupos se escriben contiguos en Parquet y se convierten a un dataset binario LightGBM acotando copias. Esta biblioteca no garantiza caber en RAM por recibir Parquet: medir construcción, ajuste y refit en el piloto. Guardar localmente modelos, esquema, mejores iteraciones, identidad de entradas y predicciones para auditoría. Reportes GitHub sólo agregados.

## Métricas, decisión y regla de cierre

MAP@12 principal; NDCG@12, Recall@200, cobertura top12/top200, concentración de exposición, repetición frente a descubrimiento, duración, RAM, disco y respaldos como controles. No llamar MAP porcentaje de precisión ni convertir una mejora offline en conversión comercial.

Segmentos disjuntos por historia previa: reciente28d; sólo historia de 29–90d; sólo historia anterior a 90d; sin compras previas. Añadir análisis de objetivos comprados previamente frente a nuevos para el usuario, y de disponibilidad del artículo. Los conjuntos de relevantes segmentados mantienen denominadores explícitos; no reemplazan la métrica global.

Promoción de G/RH/RG contra H (decisión técnica fijada antes de campaña):

1. Full completo y válido en los tres cortes, con fuente, protocolo y conjunto de compradores compatibles. Métodos incompletos no se promueven.
2. Mejora relativa de al menos 2% en la media simple de los tres MAP semanales; mejora puntual en al menos dos cortes y ninguna caída relativa superior a 2% en un corte.
3. Intervalo del delta agregado de MAP con límite inferior positivo. Bootstrap pareado por **cliente**, 2,000 réplicas, semilla 42, manteniendo juntas sus observaciones de distintas semanas; cada réplica recalcula medias por semana y luego la media de semanas. Para los tres candidatos contra H, intervalos bilaterales de nivel `1-0.05/3` (98.3333%), ajuste Bonferroni conservador. Intervalos secundarios 95% exploratorios. No tratar tres semanas ni filas repetidas como observaciones independientes.
4. NDCG y Recall medios no caen más de 1% relativo frente a H. En cada segmento de historia/corte con al menos 1,000 compradores, MAP no cae más de 5% relativo; si la referencia es cero, exigir delta no negativo. Informar los segmentos pequeños sin ocultarlos, pero sin usarlos como umbral estable.
5. Cumplir presupuesto operativo de la campaña. La latencia del servicio todavía se valida en T8: no se deduce del tiempo batch.

Entre elegibles elegir mayor MAP medio. Si la diferencia absoluta es menor o igual a `0.0001`, elegir menor tiempo total de preparación+entrenamiento+ranking en los tres cortes; luego menor pico de memoria; luego orden estable G, RH, RG. Medir componentes por separado: excluir controles PPR y bootstrap, atribuir al método el coste de construcción de sus cachés aunque se hayan reutilizado; no favorecer un método por haber recibido caché precalentada. Esto es una política de selección, no prueba de equivalencia estadística. Si ninguno cumple, conservar H y explicar fallos o resultados inconclusos. No cambiar umbrales después de observar los resultados.

Q4 y el bootstrap se ejecutan en mac24, que conserva tablas locales por comprador/fold/método con métricas, segmento, número de relevantes y huellas de rankings/etiquetas. Q1–Q3 no eliminan esas tablas al exportar reportes. La Mac principal verifica agregados e identidades sin afirmar que recalculó intervalos con datos que no recibió. Q1–Q3 disponen de 23.5 horas full acumuladas y Q4 de hasta 30 minutos, dentro del presupuesto total de 24 h; si faltan tablas o presupuesto, reportar bloqueo sin fabricar intervalos desde medias.

V3 ya fue usada para decisiones previas; V1/V2 son backtests adicionales, no nuevos tests independientes. La selección completa sigue siendo exploratoria. Las ablaciones y controles apoyan mecanismos, no causalidad. En Q4 congelar protocolo, datos, código, parámetros y procedimiento de refit. El test final se ejecuta en T8 una sola vez para el elegido y las referencias P/H predeclaradas; estado reconstruido al 2020-09-16. Para RH/RG, desplazar cuatro semanas e inner split con el mismo procedimiento. Un test decepcionante se reporta; no dispara otra búsqueda ni elegir otro ganador mirando test.

## Recursos, fallos y comunicación

- Desarrollo aquí: perfil `base`, 5 GiB/3 CPU/2 hilos, Docker `colima`. mac24: Colima `hm-project` 14 GiB/4 CPU; job `mac24` 10 GiB/3 CPU/2 hilos. Un trabajo pesado a la vez; originales montados en lectura.
- Presupuesto de ingeniería propuesto: máximo 2 h por job full y 24 horas acumuladas de jobs full de esta campaña; máximo 2 horas acumuladas de pilotos H&M. Son topes, no estimaciones de duración. Q1: tres jobs (P/H); Q2: tres (G+control PPR); Q3: seis (RH/RG por corte). Verificar disco libre inicial de al menos 20 GiB y detener escrituras grandes si queda menos de 5 GiB; no borrar datos para conseguir espacio.
- Timeout, falta de disco, OOM o error de integridad conservan reporte de fallo y parciales propios; nunca devuelven `passed`. Para continuar después de una reparación, usar intento nuevo y declarar el motivo. Cambiar parámetros por coste constituye protocolo nuevo; no comparar como si fuera el mismo. Un límite alcanzado no acredita calidad de los cortes que no terminaron.
- Primera entrega ejecutable: Q1 con demo sintética → piloto H&M V3 → full V1/V2/V3. No entrenamiento nuevo ni grafo en ese prompt. Después de revisar reportes se entregan commits y prompts separados para Q2 y Q3. No repetir T1/T2 ni las campañas históricas.
- Host siempre `SSL_CERT_FILE=/etc/ssl/cert.pem`. Sin SSH, AirDrop, claves nuevas ni monitor automático. Código por Git; reportes cerrados por Releases privadas, también fallos, con validación y descarga de comprobación. Cada Mac conserva sus datos y produce sus modelos/grafos localmente.
- Reporte nuevo `quality-handoff`, `quality-v1`, con commit limpio, hash de lock/config/dataset, fold, etapa, semilla, catálogo/población/denominadores, métricas, recursos, flags de prueba y estado final. Informes completos por etapa: `passed` sólo acredita integridad/ejecución, no promoción. Predicciones por cliente, grupos, grafos y modelos permanecen fuera del ZIP.

## Consecuencias para T4 y cobertura académica

T4-A/T4-B anteriores quedan como diseño aplicable si gana H. Q4 debe adaptar paquete, dependencias y paridad al ganador real antes de implementarlos. Si gana G/RG, transportar también grafo/esquema y, si corresponde, modelo; conservar las garantías de activación atómica, identidad, rollback, arranque en frío y credenciales locales. El comportamiento de actualización T5 también debe seguir el método elegido. No inventar contrato genérico de plugins antes de conocer esa necesidad.

| Temario | Evidencia prevista; estado actual |
| --- | --- |
| 3.1 PageRank | Q2 control personalizado con convergencia, referencia pequeña y limitación de subgrafo; pendiente |
| 3.3 Pesado de vínculos | Q2 coocurrencia normalizada, poda y efectos medidos; pendiente |
| 3.4 Caminatas aleatorias | Q2 reinicio personalizado y propagación; no acredita todo el análisis espectral; pendiente |
| 4.1 | Implícita y arranque en frío ya existentes; ampliar segmentos temporales |
| 4.2 | Atributos en híbrido existente y afinidades en Q3; cobertura parcial, sin motor puro por contenido |
| 4.3–4.4 | Experimentos PyTorch existentes: colaboración y representaciones latentes; Q2 añade vecinos colaborativos |
| 4.5 | Evaluación existente más temporal, ablaciones, selección y test final pendientes |
| 4.6 | Discusión bibliográfica de Netflix en informe, sin pretender usar ese dataset |
| 5.5 | Similitud entre nodos aplicada a red comercial en Q2; no equivale a red social observada |
| 5.1–5.4 | No comprometer redes sociales, comunidades o comunidades traslapadas con este dataset/protocolo |
| 1.1 | Iteración de potencias en control PageRank si se implementa y explica; no todo el tema 1 |
| Resto de 1 y 2; 3.2 | Fuera de alcance; no llamar Apriori/FP-Growth a contar coocurrencias |

## Referencias primarias

- [Resultados y límites del proyecto](../../model-selection.md) y [contrato de evaluación existente](../../evaluation.md).
- [LightGBM: LGBMRanker](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html): interfaz de ranking y agrupación. [Parámetros](https://lightgbm.readthedocs.io/en/stable/Parameters.html): LambdaRank, CPU, determinismo y truncación. La versión instalable se comprobará y fijará en el lock antes de ejecutar Q3; estas páginas no prueban compatibilidad de nuestra imagen.
- [NetworkX: PageRank](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.link_analysis.pagerank_alg.pagerank.html): pesos, personalización, nodos colgantes e iteración. Referencia conceptual; no obliga a cargar todo H&M como objetos NetworkX.
- Leskovec, Rajaraman y Ullman, *Mining of Massive Datasets*, bibliografía del curso: análisis de vínculos y recomendaciones. La explicación final distinguirá algoritmo estándar de aproximaciones y límites propios del proyecto.
