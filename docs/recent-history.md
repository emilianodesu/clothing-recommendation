# T3: validación de compras recientes y respaldo

## Decisión tras revisar los cuatro reportes

La máquina principal descargó los cuatro ZIP de mac24 y verificó inventario, tamaños, SHA-256, package ID, commit limpio, límites, conciliación de épocas y comparación completa. Coinciden con [la evidencia de tres épocas](../evidence/mac24-three-epochs-2026-10-20.json). La auditoría local queda en `artifacts/reviews/mac24-three-epochs-review.json`, fuera de Git.

El candidato de tres épocas consumió 68,023,995 ejemplos, con 790.859 s de job y 4.122 GiB de pico de contenedor. La comparación tardó 353.868 s con 3.857 GiB; no hubo swap ni OOM. Sin embargo, MAP@12 cayó de 0.00576877 a 0.00490391 y NDCG@12 de 0.01120105 a 0.01022287. Los intervalos de ambas diferencias quedan debajo de cero. Recall aumentó de 0.13893485 a 0.14574184, pero popularidad de siete días alcanza 0.16757920. Su MAP@12, 0.00660410, también supera ambos modelos. La mejora de cobertura y la señal de personalización de tres épocas no cumplen el criterio de calidad.

**Decisión del agente, dentro del siguiente paso solicitado:** mantener el checkpoint de una época como referencia de mayor MAP y probar directamente los artículos comprados recientemente. El modelo actual recibe ID y cuatro agregados numéricos del historial; esos agregados no identifican cuáles fueron las compras recientes. Esta es una hipótesis nueva de validación, no un error de infraestructura demostrado. No se aumentarán épocas ni se cambiarán arquitectura, negativos o recursos en esta campaña.

El paso sigue dentro de T3: dos diagnósticos aislados, sin nuevo entrenamiento, sin test y sin T4. La utilidad de cada componente quedará medida antes de decidir cómo servirlo. Los candidatos y reglas se fijan aquí antes de ejecutar H&M; no habrá un barrido automático ni una búsqueda hasta obtener un resultado favorable.

## Protocolo fijo `recent-history-v1`

1. Reproducir íntegramente la evaluación del checkpoint cerrado, validando pesos, pointer, inputs y lock como en los diagnósticos anteriores.
2. Construir un historial por comprador con transacciones en **[2020-08-12, 2020-09-09)**. Sólo artículos del catálogo elegible; sin etiquetas de validación/test para construir recomendaciones. Orden: última fecha descendente, frecuencia de compra en esa ventana descendente y article ID ascendente. Un artículo aparece una vez; máximo 200.
3. Evaluar cuatro métodos sobre los mismos compradores y catálogo. Los relevantes fuera del catálogo permanecen en los denominadores:

| Método en el reporte | Ranking |
| --- | --- |
| `model` | Checkpoint original de una época en H&M; demo original de dos épocas |
| `popular_7d` | Popularidad en [2020-09-02, 2020-09-09), con desempate por article ID |
| `recent_then_popular` | Historial reciente primero; completar con popularidad de siete días |
| `recent_then_model` | Historial reciente primero; completar con ranking del checkpoint y después popularidad |

Se devuelven hasta 200 artículos únicos (`min(200, tamaño del catálogo)`). **Sin historial reciente, ambas variantes usan popularidad**, aunque el ID del comprador exista en el modelo. La segunda variante permite medir el aporte incremental del modelo después del mismo prefijo personal. No se excluyen compras previas: la recompra es la hipótesis que se evalúa. Si el historial llena top12, el modelo no interviene en esas posiciones; el reporte cuenta estos compradores. En esta fase los rankings sólo se calculan dentro del worker: no hay API de servicio.

4. Métricas: MAP@12, NDCG@12 y Recall@200; exposición/cobertura top12/top200; segmentos con historial reciente, sin historial reciente y usuario desconocido para el modelo. Los segmentos vacíos tienen cero usuarios y métricas null, no una puntuación inventada. Se informa el tamaño medio del prefijo retenido, limitado a 200 artículos.
5. **Contraste principal:** `recent_then_popular − popular_7d`. Bootstrap pareado por comprador, 2000 réplicas, semilla 42, intervalo percentil 95%. Secundarios: ambas variantes contra el modelo; variante con modelo contra popularidad y contra la variante con popularidad. Las medias usan todos los 72,019 compradores de validación y 102,967 artículos en H&M full.
6. Control de personalización: misma selección determinista de hasta 5000 compradores, luego conservar sólo quienes tienen historial reciente. Diez permutaciones por variante, semillas 42–51, mediante ciclos sin propietarios en su posición original. Se cambia el historial completo; el ranking del modelo de cada comprador permanece fijo. Reportar cuántos historiales resultaron idénticos aunque el propietario cambió, diferencia normal−permutado y coincidencia top12. También intervalo pareado de normal menos el promedio de diez permutaciones por comprador. Si la muestra tiene menos de dos compradores con historial, el job informa fallo: no puede acreditar este control.

Los intervalos están condicionados al checkpoint, reglas y permutaciones fijados. Se reutiliza validación después de experimentos previos: los resultados son exploratorios, no evidencia final sobre test ni incertidumbre entre semillas de entrenamiento. Las métricas secundarias no llevan corrección por pruebas múltiples. No son pruebas causales.

## Criterios para revisar después

- La variante con popularidad debe mejorar MAP@12 frente a popularidad sola, con límite inferior del intervalo principal mayor que cero. Revisar NDCG, Recall, cobertura y segmentos antes de aceptarla.
- Incorporar el modelo al siguiente diseño sólo si aporta mejora de MAP frente a la variante sin modelo; no basta superar al modelo original. Si pierde calidad, conservar esa evidencia y no forzar su uso como componente ganador.
- Revisar si asignar el historial correcto aporta relevancia frente a las permutaciones. Cambiar rankings sin mejorar relevancia no acredita personalización útil.
- `passed` acredita ejecución/integridad; `quality_accepted` permanece false. El agente de la máquina principal decidirá tras recibir ambos reportes. No continuar automáticamente a otro experimento, test o T4.

## Implementación y reproducción

Config: `configs/diagnostics/recent-history.json`. Lógica: `src/hm_recommender/model/recent_history.py`; reutiliza métricas, bootstrap y el worker/lanzador/transporte existentes. `--protocol recent-history` exige un único `--source-run-id`, es incompatible con `--reference-run-id` y queda registrado en environment/result. No cambia las configuraciones de entrenamiento ni las dependencias.

La fuente debe ser un ZIP T3 aprobado en `artifacts/handoff/`, con checkpoint local intacto. El contenedor conserva red deshabilitada, datos/artefactos en lectura y únicamente el reporte nuevo en escritura. Se conserva el límite de un trabajo pesado. El lector y `status` rechazan un resultado aprobado si falta la validación de historial requerida. Los ZIP contienen agregados, no IDs de compradores, transacciones, rankings individuales o pesos.

## Verificación local antes de mac24

La [evidencia local](../evidence/local-recent-history.json) registra 96 pruebas únicas aprobadas: 56 host, 16 unitarias y 24 de integración (datos, entrenamiento, diagnósticos y bootstrap). El código probado es `dd90fd12860e30bd89aef389bea82a397c8cd21a`. Las pruebas de la imagen final se ejecutaron sin montar código desde el host.

El primer intento falló en la verificación porque la imagen no incluía el lector importado por la nueva prueba; su reporte fallido se conservó y publicó. Tras incluir ese archivo en la imagen, pasaron los dos diagnósticos oficiales locales:

| Comprobación en dev16 | Compradores | Tiempo del job | Pico del contenedor |
| --- | ---: | ---: | ---: |
| Demo sintética | 33 | 4.329 s | 0.460 GiB |
| Piloto H&M existente | 2000 | 12.181 s | 1.008 GiB |

Ambos reprodujeron el modelo original, completaron los cinco contrastes y los diez controles por variante, sin swap ni OOM. Los 19 archivos de los experimentos originales conservaron su SHA-256. Los tres ZIP se publicaron por Releases privadas con descarga verificada; sus enlaces están en la evidencia.

En el piloto, MAP@12 fue 0.00619763 para popularidad7d, 0.02215935 para historial+popularidad y 0.02138851 para historial+modelo. La mejora principal tuvo intervalo [0.01118998, 0.02074775]; el aporte incremental del modelo tuvo intervalo [-0.00163704, -0.00009828]. Esto no sustituye la medición de mac24: el piloto usa otro checkpoint y sólo 2000 compradores. No se modificaron las reglas después de observarlo; calidad y comportamiento full siguen pendientes.

## Revisión de los reportes de mac24

La [revisión de la máquina principal](../evidence/mac24-recent-history-review-2026-10-20.json) verificó ambos ZIP contra su inventario, SHA-256, package ID y evidencia publicada. También concilió los reportes originales de entrenamiento, configuración, replay, cinco contrastes, diez controles por variante, medias ponderadas de segmentos, recursos y aislamiento. Los 19 archivos originales intactos son una comprobación informada por mac24; los pesos no se transfirieron ni se recalcularon sus hashes aquí.

Full: 72,019 compradores, 102,967 artículos, 132.132 s y 2.568 GiB de pico de contenedor, sin swap ni OOM. Estos recursos corresponden al diagnóstico offline; no prueban latencia de API.

| Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | ---: | ---: | ---: |
| Modelo de una época | 0.00576877 | 0.01120105 | 0.13893485 |
| Popularidad7d | 0.00660410 | 0.01260462 | 0.16757920 |
| Historial+modelo | 0.02452712 | 0.03533274 | 0.17976476 |
| Historial+popularidad | 0.02458902 | 0.03549285 | 0.19078554 |

**Candidato recomendado: historial+popularidad.** Su mejora de MAP frente a popularidad es +0.01798491, IC95% [0.01716408, 0.01882157], equivalente a +272.33% relativo. NDCG y Recall también mejoran con intervalos positivos. Cubre 18,370 artículos en top12 (17.84% del catálogo). Entre 32,414 compradores con historial, MAP es 0.04670710; los otros 39,605 reciben exactamente el respaldo popular y conservan sus métricas. En los 2,224 compradores del control, asignar el historial correcto mejora MAP frente al promedio de permutaciones, IC95% [0.03871746, 0.05247017].

Añadir el modelo al mismo prefijo da un delta MAP de -0.00006190, IC95% [-0.00012972, 0.00000903]: no demuestra mejora ni una pérdida concluyente de MAP. Sí reduce NDCG y Recall con intervalos negativos. Conforme al criterio fijado, no se recomienda incorporar ese componente al ranking principal por calidad.

La selección en validación cumple el criterio previsto; no equivale a aceptación final sobre test. Se mantienen inmutables los reportes con `quality_accepted=false`. La validación reutilizada es exploratoria. No se inicia otra campaña ni se evalúa test como parte de esta revisión.

### Implicación para el siguiente paso

El ganador es una regla personalizada de historial y popularidad, no un modelo entrenado. El entrenamiento PyTorch masivo y su evaluación siguen siendo entregables válidos, pero no se puede afirmar que aporten la mejora observada. Esto afecta el objetivo original de usar el modelo entrenado como motor principal: esa parte aún no está resuelta.

El usuario resolvió esa decisión de alcance: [priorizar calidad independientemente del framework](model-selection.md) y conservar los experimentos académicos. El plan T4 ya define un paquete portable de catálogo, historiales, popularidad, reglas y manifiesto, seguido de API, activación/rollback, filtros y medición de latencia/reproducción entre Macs. Los artefactos PyTorch permanecen como referencia experimental y Qdrant no será dependencia obligatoria del motor seleccionado. El plan está actualizado; la implementación del servicio sigue pendiente. No se prescribe otro ajuste de entrenamiento a partir de estos resultados.

## Reproducir en mac24, sólo cuando se solicite

Lee AGENTS.md y fija el SHA completo recibido, con el clon limpio. Conserva cambios y trabajos existentes. No actualices código durante una corrida. Reutiliza dataset, features y checkpoints; no repitas T1/T2/entrenamiento. Colima `hm-project`: 14 GiB/4 CPU; `--resources mac24`: 10 GiB/3 CPU/2 hilos. Mantén la Mac despierta durante el worker; no basta envolver sólo `start` con `caffeinate`. Conserva `SSL_CERT_FILE=/etc/ssl/cert.pem` y TLS.

### 1. Comprobación demo

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --protocol recent-history \
  --source-run-id train-mac24-20261020T004703Z-a09df9b2 \
  --expected-commit SHA_COMPLETO_RECIBIDO
```

Conserva el nuevo run_id. `started` sólo indica worker iniciado. Espera hasta estado final, exporta y publica:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job status --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

Exige estado passed, verificación 0, `history_validation.completed=true`, `model_replay_verified=true`, ambos controles con diez permutaciones y todas las comparaciones. Publica también fallos operativos y detente; no cambies código o presupuesto para ocultarlos.

### 2. H&M con la referencia de una época

Si la demo pasó, repite los comandos con:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --protocol recent-history \
  --source-run-id train-mac24-20261020T004913Z-9d2fb726 \
  --expected-commit SHA_COMPLETO_RECIBIDO
```

Espera, exporta, valida y publica el segundo ZIP aunque las métricas no mejoren. No hace falta MLflow para este diagnóstico. Si faltan ZIP originales pero existen pesos, recupera sólo los reportes siguiendo [diagnostics.md](diagnostics.md). Si faltan pesos, informa el bloqueo. No hay resume del diagnóstico: conserva/exporta una interrupción y devuelve el estado antes de reintentar. Nunca inicies otro job si desconoces el estado del anterior.

Antes y después, comprueba SHA-256 de los archivos de los dos experimentos originales; devuelve el resultado agregado. Sólo al terminar ambos jobs puedes actualizar `docs/diagnostics.md` y `evidence/mac24-recent-history-2026-10-20.json` con métricas, intervalos, población, controles, memoria/tiempo/swap/OOM, commit de ejecución y Releases. No subas clientes ni pesos. Mantén la precisión completa en los ZIP; el resumen Git puede redondear a ocho decimales y expresar memoria en GiB.

Devuelve los dos run_id, sus URLs y el commit publicado. GitHub no despierta automáticamente a la máquina principal: el usuario avisa cuando estén listos. No configures SSH ni nuevos canales.
