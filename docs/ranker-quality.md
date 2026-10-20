# Q3: ordenamiento LightGBM sobre H y G

Q3 implementa RH y RG del [protocolo quality-v1](superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md). RH reordena exactamente los candidatos H; RG reordena G. Ambos conservan Recall@200. Q2 ya mostró que G incumple el límite de pérdida de Recall: RG se mantiene como ablación académica; no se cambia su pool ni la regla para hacerlo elegible. Q4 hará la selección formal. Test permanece reservado.

Esta entrega incorpora código y pruebas locales. La calidad, memoria y duración de entrenamiento H&M deben medirse en mac24; una demo no las acredita. Los pesos y tablas quedan locales, fuera de Git y de los ZIP de reportes.

## Contrato implementado

- LightGBM CPU 4.7.0, dependencias Python bloqueadas y OpenMP `libgomp1=12.2.0-14+deb12u1` en las imágenes Linux. No se instala en macOS ni necesita TensorFlow/Hopsworks.
- Cuatro semanas anteriores por fold. Cada semana reconstruye su snapshot y grafo usando exclusivamente información anterior a su inicio. El grafo del corte externo nunca alimenta las semanas de entrenamiento.
- Veinte características numéricas, con esquema y orden verificados: frecuencias/recencias del par y usuario, popularidad/tendencia, afinidades de contenido, score del grafo, posiciones e indicadores de pertenencia. Las etiquetas se añaden después de calcularlas. Atributos nulos o vacíos comparten categoría ausente; sin historial la afinidad es cero. Recencia ausente=91; posición ausente=201.
- Grupos comprador–semana contiguos. Ajuste conserva todos los positivos recuperados y hasta 20 negativos por hash; no inyecta positivos ausentes. Grupos sin positivos o sin negativos se cuentan y se excluyen sólo del ajuste. Piloto limita compradores por hash a 2,000 por semana; full no los muestrea.
- Ajuste interno con tres semanas y early stopping en la cuarta. La NDCG personalizada usa todos los relevantes en el IDCG, también fuera del pool/catálogo, y mantiene compradores sin aciertos en la media. No usa la NDCG nativa de candidatos para elegir iteraciones.
- Refit desde cero con cuatro semanas y el número de iteraciones seleccionado internamente. Parámetros fijos: LambdaRank, hasta 300 iteraciones, paciencia 30, 31 hojas, mínimo 100 filas por hoja, tasa 0.05 y dos hilos. Si no hay grupos entrenables, falla explícitamente. Puede terminar con menos árboles útiles si no hay divisiones válidas; el reporte distingue iteraciones solicitadas de árboles reales.
- Parquet por lotes y matrices temporales en archivos de memoria mapeada para construir datasets binarios LightGBM. Esto limita copias, pero no garantiza RAM: mac24 medirá el proceso completo. Las matrices temporales se eliminan después de la conversión; permanecen binarios, modelos, tablas y manifiestos de auditoría.

## Insumos y coste

Cada Q3 exige el ZIP Q2 cerrado, su grafo/ranking G/evaluación privados y su referencia Q1 con snapshot/P/H/métricas originales. Se verifican hashes, fuente, configuración, corte, población y etiquetas. Un ZIP sin los artefactos privados no basta. Los originales se montan en lectura; sólo escribe el intento nuevo. No repetir Q1/Q2 ni los entrenamientos PyTorch.

Para medir preparación, Q3 vuelve a generar únicamente la lista externa H o G desde el estado verificado y exige el mismo hash del ranking original. Reutiliza sus métricas; no repite la campaña Q1/Q2. El coste del candidato suma preparación de cuatro semanas, construcción de binarios, ajuste, refit, construcción original del grafo externo, recuperación externa y predicción/características. Excluye verificación de referencias y evaluación. Cobra la construcción del grafo externo aunque se reutilice; el coste no se reduce artificialmente por recibir una caché.

Las cuatro semanas internas se construyen dentro de cada intento. No hay caché mutable compartida ni resume Q3; un reintento produce otro run_id y conserva el anterior y su cargo al presupuesto.

## Preparación en mac24

1. Fijar el commit recibido en un clon limpio; conservar cambios existentes. Leer AGENTS.md y las revisiones Q1/Q2. No cambiar de revisión durante las corridas.
2. Conservar Colima `hm-project` 14 GiB/4 CPU y recursos `mac24` 10 GiB/3 CPU/2 hilos. Mantener la Mac despierta; un solo trabajo pesado a la vez.
3. Conservar datos/features/checkpoints, los cinco `work` Q1 y cinco Q2, sus ZIP y `ARTIFACTS_DIR/quality-budget/quality-v1.json`. Inventariar SHA-256 antes/después; inventarios con rutas/IDs permanecen locales. No abrir test para evaluar.
4. Q1+Q2 consumieron aproximadamente 326.898137 s full y 41.213648 s piloto; el ledger local es la autoridad, incluidos reintentos. Límites: 2 h por intento, 2 h acumuladas de pilotos, 23.5 h full Q1–Q3 y 30 min reservados Q4. Inicio exige 20 GiB libres; el supervisor detiene al bajar de 5 GiB.
5. Cada `start` construye la imagen desde el clon limpio. Después de terminar la primera demo H, ejecutar `SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite quality` antes del piloto. Si faltan CA, deben proceder de la confianza local; no desactivar TLS ni copiar certificados de otra Mac.

## Referencias Q2 y orden de ejecución

| Tamaño/fold | Referencia Q2 para ambos pools |
| --- | --- |
| demo V3 | `quality-mac24-20261020T211723Z-03cfe799` |
| pilot V3 | `quality-mac24-20261020T211825Z-12071578` |
| full V1 | `quality-mac24-20261020T211909Z-cf26a40a` |
| full V2 | `quality-mac24-20261020T212038Z-fd730ff0` |
| full V3 | `quality-mac24-20261020T212206Z-00c9a0ec` |

Secuencia fija de diez intentos: **demo H → pilot H → demo G → pilot G → full H V1 → H V2 → H V3 → full G V1 → G V2 → G V3**. Esperar el estado final y exportar/validar/publicar cada reporte antes de avanzar. Un fallo operativo detiene la secuencia; una métrica peor se conserva y permite continuar, sin modificar código ni parámetros.

Primera orden, sustituyendo únicamente el commit:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage ranker --pool H --config quality-v1 --fold V3 --size demo --reference-run-id quality-mac24-20261020T211723Z-03cfe799 --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Para los siguientes intentos, cambiar `--pool`, `--size`, `--fold` y `--reference-run-id` según la secuencia y tabla; conservar el commit y recursos. `--pool` es obligatorio sólo en Q3. No ejecutar órdenes simultáneas.

Para cada run_id:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job status --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

Consultar `status` hasta cierre. Conservar/publicar también fallos; el lector devuelve 1 para ejecución fallida válida, 2 para paquete inválido. No publicar un ZIP inválido. La publicación verifica una descarga privada y no despierta al otro agente.

## Artefactos y devolución

Cada `work` Q3 contiene `training/week-*/` (snapshot, grafo, ranking, grupos, ejemplos muestreados; pool completo para validación interna), `model/` (binarios, modelos y manifiesto), `external-pool/`, `ranking-RH|RG/` y `evaluation-RH|RG/`. Las identidades de cliente/artículo permanecen allí.

El reporte cerrado incluye P/H/G y RH o RG para la misma población, cuatro semanas y sus conteos, positivos ausentes, grupos descartados, iteraciones, curva NDCG interna, hashes, costes y recursos. `passed` acredita ejecución e integridad; mantiene `quality_accepted=false` y `test_evaluated=false`.

Al finalizar los diez intentos o el primer fallo, actualizar `evidence/quality-q3-mac24.json` y esta guía: commit, run_id, tamaño/fold/pool, referencias, estado, poblaciones, métricas, conteos por semana, mejores iteraciones/árboles, duración, RSS/cgroup/swap/OOM, disco, presupuesto, preservación de originales y SHA/URL de cada Release. En Git redondear métricas a ocho decimales y GiB a seis; conservar precisión original en ZIP. Publicar el resumen y avisar al usuario. No iniciar Q4, T4 ni test.

## Referencias técnicas

[LightGBM 4.7.0 en PyPI](https://pypi.org/project/lightgbm/4.7.0/), [Dataset y grupos](https://lightgbm.readthedocs.io/en/v4.7.0/pythonapi/lightgbm.Dataset.html) y [parámetros](https://lightgbm.readthedocs.io/en/v4.7.0/Parameters.html). Las pruebas locales acreditan la instalación observada; la documentación de la biblioteca no sustituye la medición de escala en mac24.
