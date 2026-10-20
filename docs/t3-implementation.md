# T3: alcance de esta entrega

La siguiente prueba en mac24 implementa el modelo y la evaluación de T3. T4 (versión de servicio, índice y API) queda pendiente. La factibilidad de una época completa se decide con el reporte real de mac24, no con una extrapolación del piloto.

- Dos torres CPU de 64 dimensiones, producto punto, embeddings de ID y atributos. Adagrad no fusionado admite embeddings dispersos y capas densas. Negativos disponibles antes de cada semana, excluyendo todas las compras positivas de ese usuario en esa semana.
- Lotes dentro de una sola partición semanal. Pilotos por subconjuntos deterministas de usuarios; full consume todos los ejemplos elegibles. Semilla, orden, optimizador y cursor se conservan al reanudar.
- Perfiles JSON versionados independientes de los perfiles T2. Demo sintética ampliada para que haya negativos y múltiples lotes.
- Checkpoint atómico y verificable, progreso y resultado explícitos. Se conservan dos generaciones. La recuperación después de un corte repite únicamente trabajo posterior al checkpoint durable.
- Evaluación exacta contra todo el catálogo disponible al corte, validación solamente, popularidad de referencia y segmentos fríos. Prueba final permanece reservada.
- MLflow local con PostgreSQL y transporte HTTP de artefactos. No se requiere una cuenta externa.
- Reportes sin pesos ni identificadores de clientes, usando el protocolo existente de Releases privadas. Los pesos permanecen en mac24 hasta implementar el contrato portable de T4.

## Identidades y tolerancias

Cada intento tiene su propio `run_id` y ZIP inmutable, como T2. `experiment_id` enlaza los intentos de un mismo entrenamiento y `attempt` los numera. Esto conserva la compatibilidad del intercambio existente. Un resume exige mismo commit, configuración, recursos, manifiestos y archivos de entrada; un cambio exige start nuevo.

T2 conserva hashes de archivos originales. El entrenamiento transforma números a Float32 y registra una huella del tensor de perfiles ordenado, además de las huellas originales. Para comparar floats de features se admite `atol=1e-12, rtol=1e-12`; para guardar/cargar un modelo en el mismo dispositivo `atol=1e-6, rtol=0`. No se exige entrenamiento bit a bit entre arquitecturas. Las diferencias remotas de snapshots no se declaran equivalentes sin comparar sus entradas canónicas.
