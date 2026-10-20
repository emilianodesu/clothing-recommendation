# T3: experimento controlado de tres épocas

## Objetivo y decisión fijada

El diagnóstico físico de mac24 (`evidence/mac24-t3-diagnostics-2026-10-19.json`) confirmó reproducción e infraestructura, pero popularidad de siete días superó al modelo en MAP@12, NDCG@12 y Recall@200. Permutar IDs produjo poca pérdida agregada. La hipótesis siguiente es entrenamiento insuficiente después de una sola época; aún no está demostrada.

El usuario autorizó preparar este experimento y continuar por GitHub. No hay SSH ni nuevas claves o canales. Test permanece reservado y T4 no se inicia.

El candidato H&M cambia **únicamente epochs de 1 a 3**. Conserva semilla 42, batch 4096, learning rate 0.05, ocho negativos, temperatura 0.1, arquitectura, datos, catálogo, vocabulario y cortes. Parte desde cero en otro experiment_id; no reanuda el checkpoint de una época cambiando su configuración. Consumirá 68,023,995 ejemplos elegibles: 22,674,665 por época. El algoritmo de entrenamiento y el modelo no se modificaron para este experimento.

## Entregables implementados

- `configs/training/hm-full-3epochs.json`: candidato completo, todos los usuarios de entrenamiento y validación.
- `configs/training/demo-3epochs.json`: comprobación operativa pequeña. Su referencia demo existente tiene **dos** épocas; esta comparación 2→3 prueba el procedimiento y no sustituye el experimento H&M 1→3.
- `configs/diagnostics/epochs.json`: muestra determinista de 5000 usuarios, semilla 42, 2000 réplicas bootstrap y diez permutaciones.
- `diagnose-job start --source-run-id CANDIDATO --reference-run-id REFERENCIA`: el mismo worker aislado, ahora vinculado a dos ZIP de entrenamiento cerrados y sus checkpoints. Sin red; datos y modelos en lectura; únicamente el reporte nuevo en escritura.
- Comparación estricta: configuraciones iguales excepto epochs, mismos inputs/locks y conciliación de filas. Reproduce todas las métricas originales de ambos checkpoints antes de comparar. Un cambio de datos, corrupción o comparación incompleta impide reportar éxito.
- Resultados agregados en el ZIP `diagnostic-handoff`, con identidad de ambos entrenamientos, recursos, comparación y diagnóstico habitual. No exporta filas por usuario, IDs de clientes, rankings ni pesos.

Los perfiles anteriores y `diagnose-job` sin referencia siguen disponibles. No se añadieron dependencias. La prueba de entrenamiento sigue usando fixtures sintéticos dentro de `/tmp`; no modifica los checkpoints originales.

## Protocolo de validación

La comparación normal usa los mismos 72,019 compradores de validación y 102,967 artículos del catálogo. Los relevantes fuera del catálogo permanecen en el denominador. Se calcula popularidad de siete días sólo con compras anteriores a `2020-09-09`. Validación: `[2020-09-09, 2020-09-16)`; test no se usa para métricas ni ajustes.

Se publican tres contrastes pareados: candidato menos referencia, candidato menos popularidad de siete días y referencia menos popularidad de siete días. Cada uno contiene MAP@12, NDCG@12 y Recall@200 por media y diferencia. El bootstrap remuestrea **usuarios completos con reemplazo y los mismos índices para ambos lados**: 2000 réplicas, semilla 42, intervalo percentil 2.5–97.5%. Sólo retiene hasta 16 poblaciones de índices por lote. Los intervalos son condicionales a los modelos entrenados; no estiman variación entre semillas de entrenamiento ni prueban causalidad. Las métricas secundarias son descriptivas; no se aplica corrección por pruebas múltiples.

Para personalización se selecciona la misma muestra de hasta 5000 compradores del diagnóstico anterior y después se excluyen IDs desconocidos. En H&M full se esperan 4583 conocidos y 417 excluidos. Se permutan sólo IDs conocidos (historial conservado), usando diez ciclos sin posiciones fijas, semillas 42–51. Ambos modelos reciben las mismas permutaciones y compradores. Cada réplica informa la diferencia normal menos permutado y coincidencia top 12. También se calcula un intervalo pareado para normal menos el promedio de las diez permutaciones por usuario; refleja incertidumbre entre usuarios condicionada a esas permutaciones. Se conservan además los controles individuales de ID cero, ID permutado e historial permutado del diagnóstico habitual; sus resultados no son el contraste repetido restringido a conocidos.

**Decisión posterior, sin aprobación automática:** MAP@12 del candidato debe mejorar frente al modelo actual y popularidad de siete días; revisar que el intervalo del contraste principal candidato−popularidad de siete días quede por encima de cero. NDCG y Recall no deberían retroceder frente al modelo actual. Revisar concentración/cobertura y que la asignación correcta de IDs aporte relevancia frente a permutarlos. Una ganancia de cobertura sola no basta. `passed` sólo acredita ejecución/integridad y `quality_accepted` permanece false. Un resultado plano o peor sigue siendo evidencia válida: no ajustar semillas, épocas o presupuesto hasta obtener uno favorable.

## Ejecución en mac24

Lee primero AGENTS.md. Usa el SHA completo recibido en un clon limpio y conserva cambios existentes. No actualices código durante los trabajos. Mantén mac24 despierta, Colima `hm-project` de 14 GiB/4 CPU y un único trabajo pesado. Todos los jobs usan `--resources mac24` (10 GiB/3 CPU/2 hilos). No es necesario descargar datos, repetir T1/T2 ni repetir pilotos anteriores.

Cada `start` anuncia un run_id. `started` significa que el worker quedó desacoplado; consulta hasta estado final. Si se pierde conexión, usa el run_id existente y no lances otro a ciegas. Un `caffeinate` limitado al comando `start` no mantiene despierta la Mac durante todo el worker.

### 1. Entrenamiento demo de tres épocas

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --config demo-3epochs --expected-commit SHA_COMPLETO_RECIBIDO
```

Espera con `train-job status --run-id RUN_DEMO_3`. Después exporta mediante `train-job export --run-id RUN_DEMO_3`, valida el ZIP con `scripts/read-handoff.py RUTA.zip` y publica con `scripts/github-handoff.py upload RUTA.zip`. Usa el prefijo SSL en todas las órdenes. Si falla, publica su evidencia y detente.

### 2. Comparación demo

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --source-run-id RUN_DEMO_3 \
  --reference-run-id train-mac24-20261020T004703Z-a09df9b2 \
  --expected-commit SHA_COMPLETO_RECIBIDO
```

Espera con `diagnose-job status --run-id RUN_COMPARACION_DEMO`, exporta con `diagnose-job export`, valida y publica. Exige `comparison.completed=true`, ambos replay verificados y estado final passed antes de avanzar. No se exige que la demo mejore sus métricas para comprobar la operación.

### 3. Entrenamiento H&M de tres épocas

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py tracking-up --port 5001
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --config hm-full-3epochs --expected-commit SHA_COMPLETO_RECIBIDO
```

Espera, exporta, valida y publica `RUN_FULL_3`. Comprueba tres épocas, 68,023,995 filas, estado passed y verificación 0. El informe registra tiempo, RAM/swap/OOM y MLflow. No uses `resume` del modelo original de una época. Si este nuevo experimento se interrumpe, exporta el intento; `train-job resume` del intento más reciente permite recuperarlo con **el mismo commit/configuración de tres épocas/recursos**, sin iniciar otro entrenamiento paralelo. Después utiliza el último run_id aprobado y su ZIP como candidato.

### 4. Comparación H&M 1→3

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --source-run-id RUN_FULL_3 \
  --reference-run-id train-mac24-20261020T004913Z-9d2fb726 \
  --expected-commit SHA_COMPLETO_RECIBIDO
```

Espera, exporta, valida y publica el cuarto reporte. El diagnóstico no tiene resume: ante interrupción conserva/exporta y reporta antes de un reintento. No modifica ninguno de los modelos. Si falta un ZIP original, consulta el procedimiento de recuperación en [diagnostics.md](diagnostics.md); si faltan pesos, informa el bloqueo.

### Entrega por GitHub

Los cuatro ZIP terminados viajan como adjuntos de Releases privadas. Devuelve commit, los cuatro run_id y sus URLs, conteos, tiempos, límites/picos/swap/OOM, reproducciones, métricas e intervalos de los contrastes, concentración y controles repetidos. Distingue la demo operativa del experimento H&M.

Sólo después de terminar todos los trabajos puedes actualizar documentación y un resumen agregado en `evidence/`, conservando los commits de ejecución originales en esa evidencia. No cambies entrenamiento o recursos para ocultar resultados. No publiques IDs/pesos/secretos. GitHub no despierta al agente de desarrollo: el usuario avisa cuando estén los reportes.

## Verificación de implementación

Pruebas de emparejamiento/intervalos conocidos, determinismo, ausencia de posiciones fijas, exclusión de usuarios nuevos, configuración incompatible, conciliación de filas y rechazo de comparación incompleta. Integración con dos modelos sintéticos reales y launcher/lector host; además regresiones de datos, entrenamiento, MLflow y bootstrap. Las corridas locales de comprobación se documentan por separado; la prueba H&M completa ocurre en mac24.

La [evidencia local](../evidence/local-three-epochs.json) registra 91 pruebas distintas aprobadas y dos trabajos desacoplados sobre `cd27dce369ba84d23867ccfa1730992595031f29`: entrenamiento demo de tres épocas (1152 filas) y comparación contra la demo original de dos. Ambos ZIP se validaron, publicaron y descargaron por el transporte privado. Las diez permutaciones por modelo terminaron, los dos replay coincidieron y los nueve archivos de la referencia conservaron su SHA-256. No hubo swap ni OOM. Las métricas normales de ambas demos empataron; esto comprueba el procedimiento, no mejora del modelo H&M. Su calidad, duración y memoria reales de tres épocas siguen pendientes de mac24.
