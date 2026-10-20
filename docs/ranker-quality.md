# Q3: ordenamiento LightGBM sobre H y G

Q3 implementa RH y RG del [protocolo quality-v1](superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md). RH reordena exactamente los candidatos H; RG reordena G. Ambos conservan Recall@200. Q2 ya mostró que G incumple el límite de pérdida de Recall: RG se mantiene como ablación académica; no se cambia su pool ni la regla para hacerlo elegible. Q4 hará la selección formal. Test permanece reservado.

Esta entrega incorpora código, revisión y pruebas locales cerradas en [la evidencia Q3](../evidence/local-quality-q3.json). La calidad, memoria y duración de entrenamiento H&M deben medirse en mac24; una demo no las acredita. Los pesos y tablas quedan locales, fuera de Git y de los ZIP de reportes.

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

## Verificación local cerrada

El commit ejecutable `c7d12cecff01ebafef94cc70aab1b3aa57910821` pasó 145 pruebas distintas: 69 científicas, 68 de host y ocho de infraestructura real Docker. `verify --suite quality` pasó desde un clon limpio (41 unitarias, 12 de integración y las mismas 68 de host). Se omitió una prueba adicional que exige el servicio externo de MLflow; Q3 no modifica tracking. Las dos advertencias de pytest corresponden a fixtures ZIP deliberadamente inválidos. La revisión independiente del cambio completo no encontró hallazgos accionables ni asuntos pendientes de juzgar.

Se verificó importación, ajuste pequeño y save/load de LightGBM en Linux ARM64. El lock incluye resolución AMD64; no se afirma haber ejecutado esa arquitectura. Las pruebas también acreditan que cambiar compras del corte externo conserva tablas internas, iteraciones elegidas y SHA del modelo entrenado, y que los empates se ordenan por ID. Se corrigió la lectura de timestamps Docker con nanosegundos en Python 3.9; las 68 pruebas de host pasaron tanto en 3.9.6 como en 3.14.5.

Las demos oficiales usaron una imagen construida desde clon limpio, sin montajes de código, con perfil base 5 GiB/3 CPU/2 hilos y originales Q1/Q2 en lectura. Ambas pasaron exportación, lectura, publicación y descarga privada verificada:

| Pool | Run / Release | Duración job | RSS pico | Pico cgroup |
| --- | --- | ---: | ---: | ---: |
| H → RH | [quality-local-20261020T220311Z-08efb4e9](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-local-20261020T220311Z-08efb4e9) | 9.018661 s | 0.528835 GiB | 0.393005 GiB |
| G → RG | [quality-local-20261020T220621Z-2aa81e51](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-local-20261020T220621Z-2aa81e51) | 9.018796 s | 0.528702 GiB | 0.392666 GiB |

Sin swap ni OOM. En cada demo hubo 33 compradores externos y 24 artículos; el refit usó 128 grupos y 2,816 filas, con una iteración seleccionada. RH y RG alcanzaron MAP@12=0.98989899, NDCG@12=0.99288972 y Recall@200=0.98989899, conservando exactamente el pool original y su Recall. Son fixtures sintéticos pequeños: estas métricas no seleccionan ganador ni predicen el resultado H&M. Se validaron además todos los ZIP presentes: 29 en `incoming` y 23 en `handoff`, incluidos ambos nuevos. La evidencia guarda identidades, hashes y agregados con precisión redondeada; cada ZIP conserva precisión completa.

La implementación se aisló en un worktree y las demos en un clon limpio con configuración propia. Como Colima no monta la ruta del worktree, las comprobaciones con montajes utilizaron una carpeta temporal dentro del proyecto ya compartido; las demos finales verificaron el commit sin montajes auxiliares. En esta red fue necesario complementar el bundle temporal de compilación con CA ya confiables de Homebrew local: TLS permaneció activo y no se copiaron certificados entre Macs.

El lock principal se resolvió normalmente. El lock secundario de Airflow conserva sus versiones históricas y añade sólo el bloque LightGBM generado por uv, porque resolverlo universalmente intentaba Python >=3.15 fuera del Python 3.12 soportado por la imagen. La integración Airflow corresponde a su etapa posterior. Las demás decisiones, motivos y límites se registran en `decisions_and_limits` de la evidencia local.

Siguiente paso: ejecutar la secuencia mac24 anterior con el commit de entrega, conservar sus diez reportes o el primer fallo y devolverlos para auditoría. Todavía no se ejecutaron Q3 H&M, Q4, T4 ni test.

## Referencias técnicas

[LightGBM 4.7.0 en PyPI](https://pypi.org/project/lightgbm/4.7.0/), [Dataset y grupos](https://lightgbm.readthedocs.io/en/v4.7.0/pythonapi/lightgbm.Dataset.html) y [parámetros](https://lightgbm.readthedocs.io/en/v4.7.0/Parameters.html). Las pruebas locales acreditan la instalación observada; la documentación de la biblioteca no sustituye la medición de escala en mac24.


## Ejecución Q3 en mac24: 20 de octubre de 2026

Las diez corridas pasaron desde el commit limpio `b0c93b85ea3dd96a4218fbae4882ca177214691f`, en el orden fijado. Cada ZIP se exportó, validó y publicó con descarga verificada antes del siguiente intento. La suite `quality` pasó tras demo H y antes de piloto H: 41 unitarias, 12 integraciones y 68 host. [Evidencia Q3 completa](../evidence/quality-q3-mac24.json).

Colima 14 GiB/4 CPU; trabajos 10 GiB/3 CPU/2 hilos, uno a la vez, Mac sin suspensión y TLS validado. Los 1,725 archivos inventariados de datos procesados, features, entrenamientos/checkpoints y `work` Q1/Q2 conservaron SHA-256 antes/después. Inventarios y artefactos privados Q3 permanecen en mac24; no se publicaron modelos ni filas individuales.

| Corrida / Release | Compradores | Job (s) | RSS / cgroup (GiB) | Iteraciones / árboles reales |
| --- | ---: | ---: | ---: | ---: |
| H demo V3: [quality-mac24-20261020T221804Z-9d6db5b0](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T221804Z-9d6db5b0) | 33 | 10.019 | 0.529896 / 0.394764 | 1 / 1 |
| H pilot V3: [quality-mac24-20261020T221923Z-b563ed9c](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T221923Z-b563ed9c) | 2,000 | 65.063 | 2.475777 / 3.234974 | 56 / 56 |
| G demo V3: [quality-mac24-20261020T222042Z-de0b3baa](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T222042Z-de0b3baa) | 33 | 10.033 | 0.529690 / 0.392975 | 1 / 1 |
| G pilot V3: [quality-mac24-20261020T222108Z-ed1929b6](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T222108Z-ed1929b6) | 2,000 | 67.096 | 2.493530 / 3.260471 | 154 / 154 |
| H full V1: [quality-mac24-20261020T222229Z-5226fbce](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T222229Z-5226fbce) | 80,253 | 373.597 | 2.572201 / 3.400448 | 15 / 15 |
| H full V2: [quality-mac24-20261020T222857Z-732b9e7a](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T222857Z-732b9e7a) | 75,822 | 358.586 | 2.541401 / 3.407280 | 8 / 8 |
| H full V3: [quality-mac24-20261020T223509Z-8f0895b5](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T223509Z-8f0895b5) | 72,019 | 352.462 | 2.585171 / 3.450287 | 3 / 3 |
| G full V1: [quality-mac24-20261020T224119Z-94bf95ca](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T224119Z-94bf95ca) | 80,253 | 613.153 | 2.582432 / 3.553280 | 167 / 167 |
| G full V2: [quality-mac24-20261020T225147Z-7620af5d](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T225147Z-7620af5d) | 75,822 | 450.696 | 2.572979 / 3.667202 | 10 / 10 |
| G full V3: [quality-mac24-20261020T225935Z-24b0b976](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T225935Z-24b0b976) | 72,019 | 526.050 | 2.637104 / 3.606720 | 89 / 89 |

Sin swap ni OOM en los diez intentos. Se verificó la cadena de referencias Q2/Q1 y la igualdad del pool regenerado. RH/RG conservaron exactamente Recall@200 de H/G respectivamente. Los costes son offline, no latencia de servicio.

| Fold full | Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | --- | ---: | ---: | ---: |
| V1 | H | 0.01936354 | 0.02936683 | 0.19345198 |
| V1 | RH | 0.01978710 | 0.03043014 | 0.19345198 |
| V1 | G | 0.02051416 | 0.03136443 | 0.18380591 |
| V1 | RG | 0.02179049 | 0.03371571 | 0.18380591 |
| V2 | H | 0.02272145 | 0.03416696 | 0.20157019 |
| V2 | RH | 0.02474579 | 0.03694299 | 0.20157019 |
| V2 | G | 0.02407240 | 0.03628144 | 0.19334432 |
| V2 | RG | 0.02596160 | 0.03960858 | 0.19334432 |
| V3 | H | 0.02458902 | 0.03549285 | 0.19078554 |
| V3 | RH | 0.02482558 | 0.03694293 | 0.19078554 |
| V3 | G | 0.02640059 | 0.03835016 | 0.18292317 |
| V3 | RG | 0.02857874 | 0.04221045 | 0.18292317 |

### Preparación y ajuste full

Cada intento reconstruyó las cuatro semanas internas con sus propios cortes; tres semanas para ajuste, cuarta para selección interna y cuatro para refit. No se usaron etiquetas externas para elegir iteraciones. Conteos sumados de cuatro semanas:

| Pool / fold | Grupos totales / entrenables | Positivos recuperados / ausentes | Grupos sin positivos / sin negativos | Filas muestreadas |
| --- | ---: | ---: | ---: | ---: |
| H V1 | 300,764 / 96,809 | 139,654 / 875,543 | 203,955 / 0 | 2,075,834 |
| H V2 | 298,215 / 102,535 | 150,335 / 832,334 | 195,680 / 0 | 2,201,035 |
| H V3 | 299,204 / 106,292 | 156,216 / 805,015 | 192,912 / 0 | 2,282,056 |
| G V1 | 300,764 / 96,143 | 140,830 / 874,367 | 204,621 / 0 | 2,063,690 |
| G V2 | 298,215 / 99,873 | 146,898 / 835,771 | 198,342 / 0 | 2,144,358 |
| G V3 | 299,204 / 102,915 | 150,719 / 810,512 | 196,289 / 0 | 2,209,019 |

La evidencia incluye los conteos de cada semana, fechas, compradores muestreados, curva NDCG interna, iteraciones evaluadas/seleccionadas, árboles reales, costes por componente, segmentos, recompra/descubrimiento, exposición y hashes. No se inyectaron positivos ausentes ni se modificaron parámetros tras observar resultados.

Presupuesto acumulado Q1–Q3: full 3001.522436 s, pilotos 173.372630 s y demos 33.523709 s. Entradas previas intactas y ninguna reserva abierta; se conserva el ledger local. Espacio libre mínimo: 186.095436 GiB.

**Éxito operativo; calidad pendiente de revisión.** RH y RG mejoraron MAP/NDCG frente a sus pools en los tres folds. RH conserva Recall de H; RG hereda la pérdida de Recall de G y continúa como ablación académica. No se declara ganador ni se calcula aquí el bootstrap formal de Q4. No se repitió Q1/Q2 ni entrenamiento PyTorch; no se inició Q4/T4 ni se evaluó test. La siguiente acción es revisar los diez reportes desde la máquina principal.
