# Entrenamiento y evaluación T3

## Qué ejecuta

PyTorch CPU aprende dos torres de 64 dimensiones normalizadas. La torre de usuario combina ID e historial (compras, precio medio, fracción online y recencia); la de artículo combina ID, tipo, grupo y color. El score es su producto punto. Adagrad no fusionado actualiza embeddings dispersos y capas densas, con cross-entropy, temperatura 0.1 y ocho negativos. Los negativos proceden del catálogo disponible antes de la semana y excluyen todas las etiquetas positivas de ese usuario en esa semana. Si una cesta ocupa todo el catálogo, el trabajo falla explícitamente, sin convertir un positivo en negativo.

Se lee una partición semanal a la vez, con lotes de 4096, semilla 42 y dos hilos. No se cargan 31.8 millones de transacciones en RAM. Los perfiles están en `configs/training/`:

| Perfil | Usuarios de entrenamiento | Validación | Épocas |
| --- | ---: | ---: | ---: |
| demo | 32 sintéticos | 33 compradores sintéticos | 2 |
| hm-small | hasta 1,000 | hasta 1,000 compradores | 1 |
| hm-medium | hasta 10,000 | hasta 2,000 compradores | 1 |
| hm-full | todos los perfiles antes del corte | todos los compradores del periodo | 1 |

Los pilotos seleccionan usuarios por SHA-256 de semilla e ID, conservando sus semanas. No son una muestra aleatoria nueva en cada intento. La corrida full debe conciliar exactamente **22,674,665 filas elegibles** por época. Una ejecución piloto no acredita entrenamiento masivo. El dataset completo conserva las compras repetidas; las etiquetas semanales son binarias usuario/artículo.

## Cortes y métricas

Entrenamiento anterior a 2020-09-09; validación [2020-09-09, 2020-09-16). El test [2020-09-16, 2020-09-23) permanece reservado. No se busca configuración ni se reporta rendimiento contra test en T3.

La búsqueda de evaluación es exacta por producto punto contra **todos** los artículos conocidos al corte, en bloques de 128 usuarios. Se recuperan hasta 200; empates se resuelven por artículo ascendente. No se excluyen compras anteriores.

- MAP@12 divide la suma de precisiones en aciertos entre `min(12, número de relevantes)`.
- NDCG@12 utiliza relevancia binaria y descuento `1/log2(posición+1)`.
- Recall@200 cuenta aciertos entre todos los artículos relevantes del usuario.
- Cobertura cuenta artículos distintos entre las primeras 12 recomendaciones sobre catálogo disponible.
- Usuarios sin compras en el periodo se excluyen y se cuentan respecto al registro de clientes. Los compradores omitidos por muestreo del piloto se cuentan por separado.
- Segmentos: ID de usuario fuera del vocabulario entrenado (incluye usuarios omitidos por pilotos), ID conocido y compradores de artículos nuevos. Los artículos no disponibles siguen en el denominador principal y su recall es cero. La disponibilidad es primera compra observada, una aproximación porque no tenemos altas de catálogo.
- Popularidad usa frecuencias anteriores al mismo corte y los mismos compradores, candidatos y métricas. Es referencia offline, no otro motor desplegado.

El modelo es una primera implementación. No se declara que supere popularidad hasta recibir y revisar la evaluación. Las métricas de los pilotos describen poblaciones diferentes y no deben compararse como mejora de calidad sin igualar esa población.

## Checkpoints y recursos

Se conserva modelo, optimizador Adagrad, RNG PyTorch/NumPy, época, semana, posición del próximo lote, contador de filas y tiempo acumulado. Un puntero atómico incluye SHA-256; se guardan dos generaciones. `stop` envía SIGTERM y concede 120 s para guardar. Un corte abrupto puede perder trabajo posterior al último checkpoint, pero no suma dos veces esas filas al reanudar. Si no llegó a existir checkpoint, se exporta el fallo y se hace un `start` nuevo.

Cada `run_id` es un intento inmutable. `experiment_id` enlaza intentos; `attempt` aumenta con `resume`. Mismo commit, lock, configuración, recursos y huellas de entradas son obligatorios. Cambiar presupuesto requiere otro experimento. `status` lee progreso; la terminal no necesita permanecer abierta. El equipo sí debe mantenerse encendido y sin suspensión.

Se registran RSS del proceso, `memory.peak`, eventos OOM, estadísticas de memoria/cache y swap cuando cgroup los expone, límites Docker efectivos, tiempo y filas/s. RSS y memoria del contenedor son medidas distintas. `duration_seconds` mide entrenamiento/checkpoints, no descarga, construcción ni evaluación. Los jobs comprueban una reserva de 10 GiB de disco para H&M.

Los archivos originales T2 mantienen sus hashes. Las entradas numéricas del modelo son Float32, con huella canónica de perfiles ordenados. Las comparaciones de features admiten `atol=rtol=1e-12`; save/load de scores en el mismo dispositivo exige `atol=1e-6, rtol=0`. Reanudar exige identidad exacta; no se promete repetición bit a bit entre plataformas.

## MLflow y transporte

MLflow se ejecuta localmente con metadatos PostgreSQL (`hm_mlflow`) y artefactos servidos por HTTP desde un volumen Docker. Sólo publica un puerto en loopback. `tracking-up --port 5001` conserva ese puerto en el `.env` propio del equipo. Conserva volúmenes al detener el proyecto.

H&M registra parámetros, métricas, reporte y pesos finales; verifica subir/descargar un JSON por HTTP. La demo guarda el reporte local. Si falla tracking después del entrenamiento, el intento falla y puede reanudar evaluación/tracking desde el checkpoint completo sin repetir la época.

GitHub transporta únicamente reportes pequeños, verificables e inmutables: no incluye pesos, perfiles ni IDs de clientes. El transporte portable de modelos y el servicio de recomendaciones corresponden a T4. La Mac de desarrollo puede leer todos los resultados sin acceder a la base MLflow de mac24.

Referencias: [Adagrad en PyTorch](https://docs.pytorch.org/docs/stable/generated/torch.optim.Adagrad.html), [servidor de tracking MLflow](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/), [CLI MLflow](https://mlflow.org/docs/latest/api_reference/cli.html).
