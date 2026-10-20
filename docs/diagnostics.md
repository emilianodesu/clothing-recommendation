# T3: diagnóstico de concentración y personalización

La época completa de mac24 supera popularidad histórica, pero sus primeras 12 recomendaciones cubren sólo 53 de los 102,967 artículos elegibles. Antes de aceptar la calidad y avanzar a T4, `diagnose-job` analiza el checkpoint existente. No ejecuta entrenamiento, no selecciona hiperparámetros y no evalúa test.

## Qué se mide

1. **Reproducción del resultado original.** Repite la evaluación exacta sobre el catálogo completo, con los mismos compradores, denominadores y desempates. Compara todos los campos con el reporte T3 cerrado: tolerancia absoluta `1e-10` para flotantes y coincidencia exacta para conteos y otros valores. Una discrepancia detiene el diagnóstico.
2. **Concentración.** Artículos distintos, cobertura, listas ordenadas distintas, conjuntos distintos y fracción de exposiciones que reciben los 1/5/10/50 artículos más expuestos, tanto en top 12 como en top 200. `effective_items` es la exponencial de la entropía de las exposiciones: baja cuando pocos artículos dominan. Estos valores no tienen un umbral de aprobación automático.
3. **Referencias de popularidad.** Histórica, últimos 7 días y últimos 28 días, con compras estrictamente anteriores a `2020-09-09`. Las ventanas son `[2020-09-02, 2020-09-09)` y `[2020-08-12, 2020-09-09)`. Las tres usan el mismo catálogo y compradores que el modelo. Se incluyen artículos sin compras recientes con conteo cero y desempate por article ID. Se reportan MAP@12, NDCG@12, Recall@200 y coincidencia con el modelo en top 12.
4. **Variación entre compradores.** Muestra determinista de hasta 5,000 compradores de validación, semilla 42. Se comparan pares distintos mediante un ciclo de una permutación; se informa intersección media, Jaccard y fracción de listas ordenadas idénticas.
5. **Dependencia de las entradas del usuario.** Sobre la misma muestra se evalúan tres controles: ID desconocido (0) conservando historial, IDs permutados conservando historial e historiales permutados conservando ID. Se reportan cambios de métricas respecto al modelo normal de esa muestra y cambios de rankings. La permutación no deja filas en su posición, aunque puede intercambiar valores iguales; se registra cuántas entradas realmente quedaron iguales.

Los controles miden sensibilidad a las entradas, no prueban causalidad ni sustituyen una evaluación real de arranque en frío. Los usuarios sin compras quedan excluidos como en T3. Los artículos relevantes fuera del catálogo permanecen en el denominador. En `hm-full` la evaluación normal y las referencias usan todos los compradores de validación; sólo los controles usan la muestra de 5,000. Los pilotos conservan la población de evaluación fijada en su entrenamiento. No se accede a etiquetas de test para calcular métricas o popularidad; la verificación de integridad del dataset puede leer bytes y metadatos de todas sus particiones.

## Contrato e implementación

- Configuración versionada: `configs/diagnostics/validation.json`. No cambian dependencias, lock, perfiles de recursos ni arquitectura de PyTorch.
- El origen debe ser un ZIP `training-handoff` local, cerrado, validado y exitoso en `artifacts/handoff/`. Se fijan SHA-256 del ZIP, package ID, commit original, configuración, lock, evaluación e identidad de entradas.
- `latest.json` y su checksum deben coincidir con el checkpoint registrado en el reporte original. Se cargan tensores con `weights_only=True`, se exige cursor final y las mismas filas/inputs. Un intento posterior que haya cambiado el checkpoint requiere usar su propio reporte.
- Docker monta datos y artefactos en **solo lectura** y únicamente la carpeta del diagnóstico nuevo en escritura; raíz de contenedor en lectura, `/tmp` efímero y sin red. El lanzador comprueba los montajes y límites efectivos antes de iniciar. No necesita MLflow ni puertos nuevos.
- Se conserva la exclusión de trabajos pesados simultáneos, imagen por ID, clon limpio y commit esperado. `--profile` elige Colima; `--resources` fija el presupuesto. No se amplía la VM automáticamente.
- Cada ejecución tiene un `diagnose-...` distinto, estado y recursos medidos. `passed` significa que las pruebas, integridad y reproducción pasaron; **`quality_accepted` sigue en false** hasta revisar los resultados.
- El ZIP `diagnostic-handoff` contiene agregados, configuración y logs acotados. No incluye rankings individuales, IDs de compradores, pesos ni datos. Usa el mismo GitHub privado que T1–T3.
- No hay `resume`: si se interrumpe, se exporta el intento y se inicia otro diagnóstico sobre el mismo origen. El checkpoint permanece intacto.

Código: `scripts/diagnose_job.py`, lanzador compartido `scripts/data_job.py`, `src/hm_recommender/model/diagnostic_worker.py` y `diagnostics.py`.

## Ejecutar en mac24

Primero conserva cambios locales y comprueba `git status --short`. Si el clon está limpio, actualízalo por fast-forward y verifica el SHA completo que entrega el agente de desarrollo. No cambies de rama ni actualices durante un trabajo. Todos los comandos de host llevan `SSL_CERT_FILE=/etc/ssl/cert.pem` y TLS permanece activado.

Se reutilizan los orígenes ya cerrados:

| Paso | Origen T3 en mac24 |
| --- | --- |
| Comprobación sintética | `train-mac24-20261020T004703Z-a09df9b2` |
| Diagnóstico completo | `train-mac24-20261020T004913Z-9d2fb726` |

Con Colima `hm-project` de 14 GiB/4 CPU y sin otro trabajo pesado:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job start \
  --machine-id mac24 --profile hm-project --resources mac24 \
  --source-run-id train-mac24-20261020T004703Z-a09df9b2 \
  --expected-commit SHA_COMPLETO_RECIBIDO
```

Conserva el `run_id` que anuncia. Después de `started`, puede cerrarse la terminal; no significa que la evaluación haya terminado. Consulta hasta estado final:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job status --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA_DEL_ZIP
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA_DEL_ZIP
```

Si pasó la prueba sintética, repite `start` con `--source-run-id train-mac24-20261020T004913Z-9d2fb726`, espera, exporta, valida y publica también ese resultado. En caso de fallo publica el reporte y detente: no modifiques datos, código o límites para ocultarlo. Si el ZIP de origen falta pero el checkpoint existe, se puede recuperar por `github-handoff.py download ORIGEN` y copiar **sólo el ZIP validado** de `artifacts/incoming/` a `artifacts/handoff/`, sin sobrescribir otro archivo. Si faltan pesos, informa el requisito; descargar el reporte no descarga el modelo.

Para detener explícitamente: `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py diagnose-job stop --run-id RUN_ID`. No elimina datos ni contenedores. Un fallo antes de generar run_id, como origen ausente o lock incompatible, debe devolverse con su mensaje; los intentos que sí generaron run_id se exportan aunque fallen.

Devuelve commit, ambos run_id, orígenes, estado, Releases, tiempo/RAM/swap/OOM, coincidencia del replay, métricas modelo vs referencias, concentración top12/top200, variación entre usuarios y cambios bajo los tres controles. No inicies T4 ni otro entrenamiento: la decisión posterior depende de esta evidencia.

## Recibir y verificar aquí

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py list
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py artifacts/incoming/RUN_ID.zip
```

GitHub transporta evidencia; no activa al otro agente. El usuario avisa cuando los reportes estén publicados. Las pruebas del código se ejecutan con `project.py verify --suite diagnostics` después de construir la imagen; además se ejecutan pruebas host y de bootstrap al cambiar el lanzador. La evidencia local no se atribuye a mac24.

## Evidencia de esta entrega

Se probaron 84 casos únicos: 50 host, 12 unitarios, 4 de entrenamiento, 2 de diagnóstico, 8 de datos y 8 de bootstrap. En la Mac de desarrollo de 16 GiB se ejecutaron dos diagnósticos desacoplados desde `bfa7b7a`: demo (4.185 s, 0.455 GiB pico de contenedor) y checkpoint H&M del piloto de 10,000 usuarios entrenados (13.539 s, 1.008 GiB; evaluación de 2,000 compradores y 102,967 artículos). Ambos reprodujeron las métricas originales, sin swap/OOM y con hashes del entrenamiento intactos. Los tiempos excluyen construir la imagen. Véase [evidencia local](../evidence/local-t3-diagnostics.json); no representa ejecución full en mac24.

En ese piloto, popularidad reciente supera al modelo y hay mucha coincidencia entre recomendaciones de distintos compradores. Esto demuestra que el diagnóstico detecta diferencias útiles; la ejecución posterior sobre el checkpoint full de mac24 se documenta abajo para revisar posibles cambios al modelo.

Revisión independiente: sin defectos bloqueantes. Mejoras menores diferidas: el esquema acepta hasta 10,000 usuarios aunque el perfil entregado fija 5,000; los fallos de integridad muestran fase y tipo de error, pero la causa precisa dentro de `source` requiere inspección local. No se modificó el perfil para ampliar la muestra.


## Resultado físico en mac24

Ambos diagnósticos pasaron desde el commit limpio `b4248cc80337dc524ce1d505b4c96248278a3119`, reproduciendo exactamente todos los campos de evaluación de los reportes originales. Colima: 14 GiB/4 CPU; jobs: 10 GiB/3 CPU/2 hilos. Entradas montadas en lectura, contenedores sin red. No se entrenó, evaluó test ni inició T4. `quality_accepted` permanece en false.

| Origen | Run ID / Release | Tiempo | Pico RSS / cgroup |
| --- | --- | ---: | ---: |
| Demo | [diagnose-mac24-20261020T012621Z-0f44f776](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-diagnose-mac24-20261020T012621Z-0f44f776) | 4.678 s | 0.331 / 0.454 GiB |
| Full | [diagnose-mac24-20261020T012703Z-46890b00](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-diagnose-mac24-20261020T012703Z-46890b00) | 121.299 s | 1.926 / 2.569 GiB |

Swap máximo y eventos OOM: cero en ambos. Los tiempos excluyen construir la imagen. Los run_id usan UTC del 20 de octubre; fecha local de ejecución: 19 de octubre de 2026, America/Mexico_City. Ambos ZIP fueron exportados, validados y publicados con descarga verificada. La [evidencia agregada](../evidence/mac24-t3-diagnostics-2026-10-19.json) conserva las huellas, recursos, controles y resultados completos de ambas corridas.

Full: 72,019 compradores y 102,967 artículos del catálogo de validación.

| Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | ---: | ---: | ---: |
| Modelo | 0.005768773 | 0.011201051 | 0.138934855 |
| Popularidad histórica | 0.003444009 | 0.006318059 | 0.064267564 |
| Popularidad 7 días | 0.006604104 | 0.012604620 | 0.167579202 |
| Popularidad 28 días | 0.005592524 | 0.011816721 | 0.144753568 |

Popularidad de 7 días supera al modelo en las tres métricas. La referencia de 28 días supera NDCG y Recall; el modelo sólo tiene MAP ligeramente mayor. Superar popularidad histórica no basta para aceptar calidad.

| Concentración full | Top12 | Top200 |
| --- | ---: | ---: |
| Artículos distintos | 53 | 536 |
| Cobertura | 0.05147% | 0.52056% |
| Artículos efectivos por entropía | 18.71 | 265.70 |
| Listas ordenadas distintas | 32,874 | 66,625 |
| Conjuntos distintos | 651 | 65,843 |
| Exposición de los 10 artículos más expuestos | 73.87% | 5.00% |

En la muestra determinista de 5,000 compradores, la coincidencia top12 media entre usuarios fue 74.82%, Jaccard 0.6236 y listas ordenadas idénticas 0.64%. Los controles se comparan con el modelo normal de esa misma muestra, no con las métricas full:

| Control | Delta MAP@12 | Delta NDCG@12 | Delta Recall@200 | Coincidencia top12 con normal |
| --- | ---: | ---: | ---: | ---: |
| ID desconocido, historial conservado | -0.001715575 | -0.002850166 | -0.018028313 | 50.72% |
| IDs permutados, historial conservado | +0.000142876 | +0.000113707 | -0.003076262 | 83.04% |
| Historiales permutados, ID conservado | +0.000056738 | -0.000311192 | -0.002501999 | 78.93% |

Eliminar el ID reduce las métricas; permutar IDs o historiales apenas las empeora en agregado. Estos controles y la concentración requieren revisión antes de aceptar personalización; no prueban causalidad. Próximo paso: revisar esta evidencia en la máquina principal y decidir experimentos con validación mediante una nueva instrucción.

## Comparación del siguiente candidato

El [experimento de tres épocas](three-epochs.md) añade `--reference-run-id` para comparar dos checkpoints cerrados con intervalos pareados y permutaciones repetidas. La ejecución anterior de este documento permanece como evidencia del modelo de una época; no se sobrescribe ni acredita el candidato nuevo.


## Experimento de tres épocas ejecutado en mac24

Los cuatro pasos finalizaron con `passed` desde el commit limpio `41e41037a3e203a683d94226ee535b56079563fc`. Los cuatro ZIP se exportaron, validaron y publicaron con descarga verificada. La [evidencia agregada](../evidence/mac24-three-epochs-2026-10-20.json) conserva métricas, intervalos, recursos, hashes y controles completos. Las horas de los run_id son UTC; la ejecución atravesó la medianoche local del 19 al 20 de octubre de 2026 (America/Mexico_City).

Colima `hm-project`: 14 GiB/4 CPU. Recursos solicitados y efectivos de cada trabajo: 10 GiB/3 CPU/2 hilos. Un trabajo pesado a la vez, Mac despierta y TLS validado. Los 9 archivos de la demo original y los 10 del full original conservaron sus SHA-256. No se evaluó test ni se inició T4.

| Paso / Release | Estado | Tiempo de job | Pico RSS / cgroup |
| --- | --- | ---: | ---: |
| Entrenamiento demo: [train-mac24-20261020T055728Z-18b99f69](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-train-mac24-20261020T055728Z-18b99f69) | passed | 6.559 s | 0.418 / 0.458 GiB |
| Comparación demo: [diagnose-mac24-20261020T055813Z-1a6f12b6](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-diagnose-mac24-20261020T055813Z-1a6f12b6) | passed | 4.491 s | 0.331 / 0.458 GiB |
| Entrenamiento full: [train-mac24-20261020T055912Z-d76319c1](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-train-mac24-20261020T055912Z-d76319c1) | passed | 790.859 s | 1.871 / 4.122 GiB |
| Comparación full: [diagnose-mac24-20261020T061256Z-b53ce9d7](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-diagnose-mac24-20261020T061256Z-b53ce9d7) | passed | 353.868 s | 2.515 / 3.857 GiB |

Los tiempos del job excluyen construcción de imagen. Swap pico y eventos OOM: cero en los cuatro trabajos. El límite combinado memoria+swap es 20 GiB; no representa uso de swap. La demo consumió 1,152 ejemplos en tres épocas y empató sus métricas con la referencia de dos épocas: los tres intervalos candidato−referencia fueron [0, 0]. Es una comprobación operativa, no evidencia de mejora en H&M.

Full se entrenó desde cero: **3 épocas, 68,023,995 ejemplos** (22,674,665 por época), 1,351,314 usuarios de entrenamiento y 16,767 pasos. Entrenamiento: 735.978 s; job con evaluación y tracking: 790.859 s. MLflow verificado: `d824eff3faca45b18ccc24248e373770` (la demo no requiere registro MLflow). Verificación: salida 0.

Ambas comparaciones reprodujeron los dos checkpoints y marcaron `comparison.completed=true`. Full evaluó 72,019 compradores y 102,967 artículos. Las diez permutaciones por modelo terminaron con semillas 42–51 y cero posiciones fijas: 4,583 usuarios conocidos, excluidos 417 desconocidos de la muestra de 5,000.

| Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | ---: | ---: | ---: |
| Full 3 épocas | 0.004903911 | 0.010222874 | 0.145741838 |
| Full 1 época | 0.005768773 | 0.011201051 | 0.138934855 |
| Popularidad histórica | 0.003444009 | 0.006318059 | 0.064267564 |
| Popularidad 7 días | 0.006604104 | 0.012604620 | 0.167579202 |
| Popularidad 28 días | 0.005592524 | 0.011816721 | 0.144753568 |

Intervalos pareados percentiles al 95%, 2,000 remuestreos de usuarios, semilla 42. Son condicionales a estos checkpoints; no estiman variabilidad entre semillas ni prueban causalidad. Las métricas secundarias son descriptivas, sin corrección por comparaciones múltiples.

| Contraste | Métrica | Delta | IC 95% |
| --- | --- | ---: | --- |
| 3 épocas − 1 época | MAP@12 | -0.000864861 | [-0.001195255, -0.000538909] |
| 3 épocas − 1 época | NDCG@12 | -0.000978177 | [-0.001403608, -0.000565990] |
| 3 épocas − 1 época | Recall@200 | +0.006806984 | [+0.005286360, +0.008311503] |
| 3 épocas − pop. 7 días | MAP@12 | -0.001700193 | [-0.002085518, -0.001305536] |
| 3 épocas − pop. 7 días | NDCG@12 | -0.002381746 | [-0.002832486, -0.001945498] |
| 3 épocas − pop. 7 días | Recall@200 | -0.021837364 | [-0.023539295, -0.020120733] |
| 1 época − pop. 7 días | MAP@12 | -0.000835332 | [-0.001160955, -0.000533405] |
| 1 época − pop. 7 días | NDCG@12 | -0.001403569 | [-0.001821447, -0.001006177] |
| 1 época − pop. 7 días | Recall@200 | -0.028644347 | [-0.030455026, -0.026896716] |

El candidato empeora MAP y NDCG frente a una época, aunque mejora Recall. Popularidad de siete días lo supera en las tres métricas, con los tres intervalos por debajo de cero. **Éxito operativo; criterio de calidad no cumplido, `quality_accepted=false`.** No se ajustaron hiperparámetros después del resultado.

| Concentración del candidato | Top12 | Top200 |
| --- | ---: | ---: |
| Artículos distintos | 126 | 1,113 |
| Listas ordenadas distintas | 53,035 | 66,625 |
| Conjuntos distintos | 3,340 | 66,614 |
| Cobertura | 0.12237% | 1.08093% |
| Artículos efectivos | 25.46 | 345.01 |
| Exposición top10 | 66.21% | 5.00% |

La cobertura top12 pasó de 53 a 126 artículos y top200 de 536 a 1,113. La coincidencia media top12 entre usuarios bajó de 74.82% a 62.05% (Jaccard 0.4855; listas ordenadas idénticas 0.64%). Esta mayor diversidad no compensa la pérdida de relevancia.

Controles individuales sobre los mismos 5,000 compradores; deltas control−normal:

| Control | Delta MAP | Delta NDCG | Delta Recall | Coincidencia top12 |
| --- | ---: | ---: | ---: | ---: |
| ID cero | -0.001767768 | -0.003382740 | -0.042131998 | 32.44% |
| ID permutado | -0.001016663 | -0.000951030 | -0.004530148 | 64.44% |
| Historial permutado | -0.000845353 | -0.001074152 | -0.005492117 | 79.78% |

Contraste repetido sólo entre conocidos: normal menos el promedio de diez permutaciones por usuario.

| Modelo | Métrica | Delta | IC 95% |
| --- | --- | ---: | --- |
| 3 épocas | MAP@12 | +0.001175736 | [+0.000318894, +0.002192310] |
| 3 épocas | NDCG@12 | +0.001289001 | [+0.000271031, +0.002418885] |
| 3 épocas | Recall@200 | +0.002138982 | [-0.000972546, +0.005360723] |
| 1 época | MAP@12 | -0.000012620 | [-0.000338513, +0.000298688] |
| 1 época | NDCG@12 | -0.000014739 | [-0.000519756, +0.000440985] |
| 1 época | Recall@200 | +0.000669827 | [-0.001325094, +0.002625671] |

La asignación correcta del ID aporta MAP y NDCG al candidato frente a estas permutaciones; el intervalo de Recall incluye cero. Esto no demuestra causalidad ni aceptación de calidad. Siguiente acción: revisar en la máquina principal los cuatro reportes y decidir otro experimento mediante una nueva instrucción; T4 permanece pendiente.

## Revisión en la máquina principal y siguiente paso

Los cuatro ZIP anteriores se descargaron y verificaron por inventario, tamaños, SHA-256, package ID y commit limpio. Se conciliaron límites efectivos de 10 GiB/3 CPU/2 hilos, tres épocas/filas, ambos replay, población, diez permutaciones y deltas pareados. La evidencia coincide con los reportes. Los hashes intactos de los pesos son una comprobación realizada en mac24: aquí sólo se recibieron reportes, no los modelos. Auditoría local: `artifacts/reviews/mac24-three-epochs-review.json`.

Se mantiene `quality_accepted=false`; aumentar épocas no resolvió la calidad. El siguiente paso solicitado queda preparado en [recent-history.md](recent-history.md): medir compras recientes explícitas con popularidad como respaldo y el aporte incremental del modelo de una época. Reglas fijadas antes de ejecutarlo, dos diagnósticos por GitHub, sin nuevo entrenamiento ni test/T4.


## Historial reciente validado en mac24 (20 de octubre de 2026)

Protocolo `recent-history-v1`, commit limpio `d4a4f8495bc05d4458e1fb084d9411f3b9e06385`. Demo y full finalizaron `passed`, verificación 0, replay del modelo confirmado, cinco comparaciones pareadas y diez permutaciones por variante. Ambos ZIP se exportaron, validaron y publicaron con descarga verificada. [Evidencia agregada completa](../evidence/mac24-recent-history-2026-10-20.json).

Colima `hm-project`: 14 GiB/4 CPU; cada job solicitó y recibió 10 GiB/3 CPU/2 hilos. Ejecución secuencial con suspensión inhibida y TLS validado. Los 19 archivos de los dos entrenamientos originales conservaron sus SHA-256. No hubo entrenamiento, evaluación de test ni T4.

| Corrida / Release | Tiempo job | Pico RSS / contenedor | Swap / OOM |
| --- | ---: | ---: | --- |
| [diagnose-mac24-20261020T184027Z-02396100](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-diagnose-mac24-20261020T184027Z-02396100) | 4.888 s | 0.333 / 0.461 GiB | 0 / 0 |
| [diagnose-mac24-20261020T184112Z-4b76ee72](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-diagnose-mac24-20261020T184112Z-4b76ee72) | 132.132 s | 1.923 / 2.568 GiB | 0 / 0 |

Los tiempos excluyen construcción de imagen. Demo: 33 compradores, catálogo de 24 artículos, 32 compradores con historial y uno sin historial. Full: 72,019 compradores y 102,967 artículos; 32,414 con historial reciente y 39,605 sin historial. Prefijo medio retenido: 2.36353 artículos; 3,043 compradores llenan top12 con su historial.

### Demo: métricas e intervalos

| Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | ---: | ---: | ---: |
| model | 0.97474747 | 0.98170577 | 0.98989899 |
| popular_7d | 0.20251596 | 0.33714450 | 0.98989899 |
| recent_then_model | 0.96565657 | 0.97430950 | 0.98989899 |
| recent_then_popular | 0.96565657 | 0.97430950 | 0.98989899 |

Contrastes pareados: delta [IC95%], 2,000 remuestreos de compradores, semilla 42.

| Contraste | MAP@12 | NDCG@12 | Recall@200 |
| --- | --- | --- | --- |
| recent_model_vs_model | -0.00909091 [-0.02727273, +0.00000000] | -0.00739627 [-0.02218881, +0.00000000] | +0.00000000 [+0.00000000, +0.00000000] |
| recent_model_vs_popular | +0.76314061 [+0.67306079, +0.84525809] | +0.63716499 [+0.53534391, +0.74169109] | +0.00000000 [+0.00000000, +0.00000000] |
| recent_model_vs_recent_popular | +0.00000000 [+0.00000000, +0.00000000] | +0.00000000 [+0.00000000, +0.00000000] | +0.00000000 [+0.00000000, +0.00000000] |
| recent_popular_vs_model | -0.00909091 [-0.02727273, +0.00000000] | -0.00739627 [-0.02218881, +0.00000000] | +0.00000000 [+0.00000000, +0.00000000] |
| recent_popular_vs_popular | +0.76314061 [+0.67306079, +0.84525809] | +0.63716499 [+0.53534391, +0.74169109] | +0.00000000 [+0.00000000, +0.00000000] |

### Full: métricas e intervalos

| Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | ---: | ---: | ---: |
| model | 0.00576877 | 0.01120105 | 0.13893485 |
| popular_7d | 0.00660410 | 0.01260462 | 0.16757920 |
| recent_then_model | 0.02452712 | 0.03533274 | 0.17976476 |
| recent_then_popular | 0.02458902 | 0.03549285 | 0.19078554 |

Contrastes pareados: delta [IC95%], 2,000 remuestreos de compradores, semilla 42.

| Contraste | MAP@12 | NDCG@12 | Recall@200 |
| --- | --- | --- | --- |
| recent_model_vs_model | +0.01875834 [+0.01790050, +0.01961420] | +0.02413169 [+0.02315741, +0.02511934] | +0.04082991 [+0.03920126, +0.04249339] |
| recent_model_vs_popular | +0.01792301 [+0.01709919, +0.01877571] | +0.02272812 [+0.02177902, +0.02370243] | +0.01218556 [+0.01075088, +0.01365701] |
| recent_model_vs_recent_popular | -0.00006190 [-0.00012972, +0.00000903] | -0.00016011 [-0.00031133, -0.00001950] | -0.01102078 [-0.01210342, -0.00992418] |
| recent_popular_vs_model | +0.01882025 [+0.01796394, +0.01966486] | +0.02429180 [+0.02330044, +0.02530256] | +0.05185069 [+0.04989939, +0.05395356] |
| recent_popular_vs_popular | +0.01798491 [+0.01716408, +0.01882157] | +0.02288823 [+0.02195755, +0.02386316] | +0.02320634 [+0.02223473, +0.02420027] |

### Cobertura y controles full

| Método | Artículos top12 / top200 | Cobertura top12 / top200 | Exposición top10 en top12 |
| --- | ---: | ---: | ---: |
| model | 53 / 536 | 0.0515% / 0.5206% | 73.87% |
| popular_7d | 12 / 200 | 0.0117% / 0.1942% | 83.33% |
| recent_then_model | 18,370 / 19,238 | 17.8407% / 18.6837% | 58.45% |
| recent_then_popular | 18,370 / 19,237 | 17.8407% / 18.6827% | 72.86% |

Controles sobre 2,224 compradores con historial, de una muestra de 5,000 (2,776 excluidos sin historial). Diez permutaciones por variante, semillas 42–51, cero propietarios en su posición original. El reporte registra por réplica historiales idénticos pese al cambio de propietario. Normal menos promedio de diez permutaciones, delta [IC95%]:

| Variante | MAP@12 | NDCG@12 | Recall@200 |
| --- | --- | --- | --- |
| recent_then_popular | +0.04529379 [+0.03871746, +0.05247017] | +0.05904768 [+0.05153870, +0.06734188] | +0.05806492 [+0.04988059, +0.06709963] |
| recent_then_model | +0.04507289 [+0.03843840, +0.05226380] | +0.05865314 [+0.05128939, +0.06690224] | +0.06083768 [+0.05246759, +0.06983217] |

**Éxito operativo; aceptación de calidad pendiente (`quality_accepted=false`).** Historial+popularidad mejora MAP, NDCG y Recall frente a popularidad7d, con intervalos positivos. Añadir el modelo al mismo prefijo no demuestra ganancia de MAP (IC incluye cero) y reduce NDCG y Recall respecto a historial+popularidad. Los controles miden el aporte del historial correcto; no prueban causalidad. Todos los segmentos, exposiciones y réplicas están en la evidencia agregada.

Los intervalos son condicionales al checkpoint, reglas y permutaciones. Se reutilizó validación: resultados exploratorios, sin corrección por comparaciones múltiples ni evaluación de test. No se modificaron reglas, hiperparámetros o recursos tras observar resultados. La máquina principal debe revisar ambos reportes antes de aceptar calidad o autorizar otro paso.

### Revisión completada en la máquina principal

Ambos ZIP se descargaron y validaron; sus métricas y recursos coinciden con esta evidencia. La [auditoría](../evidence/mac24-recent-history-review-2026-10-20.json) concilia identidades de origen, replay, configuración, segmentos, controles y aislamiento. La [decisión y sus límites](recent-history.md#revisión-de-los-reportes-de-mac24) recomiendan historial+popularidad como candidato: cumple el criterio primario de validación; el aporte incremental del modelo no lo cumple. No se altera `quality_accepted=false` en los reportes originales ni se afirma calidad final sobre test. Antes de T4 debe revisarse el diseño porque el candidato ganador no utiliza inferencia neuronal. No se inició entrenamiento, test ni T4 durante esta revisión.
