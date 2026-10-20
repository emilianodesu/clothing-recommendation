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
