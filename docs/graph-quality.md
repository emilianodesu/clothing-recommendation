# Q2: grafo de artículos y control PageRank

Q2 implementa la recuperación G del [protocolo aprobado](superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md). Conserva P/H como referencias verificadas de Q1. Los cinco reportes mac24 ya se descargaron y [revisaron](../evidence/quality-q2-review.json): Q2 está cerrada operativamente. G mejora MAP/NDCG, pero incumple el límite de pérdida de Recall. Esta página conserva el procedimiento de reproducción; no repetirlo por defecto. [Q3](ranker-quality.md) también está cerrada tras revisar diez reportes mac24. Q4 y T4 siguen pendientes y test permanece reservado.

## Qué calcula

El grafo usa compras de los 90 días estrictamente anteriores a cada corte. Las cestas son **aproximaciones semanales por comprador**, no carritos reales: el dataset no tiene identificador de pedido. Se deduplican artículos y se conservan como máximo 50 por cesta, por última fecha, frecuencia e ID. La coocurrencia se divide por la raíz del producto de soportes. Cada origen conserva hasta 50 vecinos, con desempate por ID. La poda puede producir aristas dirigidas; se conservan nodos aislados del catálogo anterior al corte.

La construcción utiliza una incidencia dispersa cesta–artículo y productos por bloques de 256 orígenes. Persiste cada partición de coocurrencia en disco antes de normalizar/podar y conserva su huella y conteos. Nunca crea una matriz densa catálogo×catálogo. La incidencia completa ocupa memoria proporcional a las membresías retenidas; el límite de memoria del contenedor y el supervisor siguen vigentes. Los bytes temporales reportados son el máximo de archivos temporales del constructor, no una estimación de RAM.

G usa hasta 50 semillas de compras en 90 días, ponderadas por `log1p(frecuencia) × 2^(-recencia/14)` y normalizadas. Recupera vecinos directos, conserva el prefijo de compras de 28 días y completa mediante fusión recíproca de rangos de vecinos y popularidad7d, constante 60. Mantiene 200 candidatos únicos o todo el catálogo si es menor. Sin vecinos, G coincide exactamente con H. El grafo del piloto sigue usando **todos** los eventos históricos; sólo la población evaluada se limita a 2,000 compradores por hash.

El control PageRank selecciona hasta 5,000 compradores por hash antes de separar disponibilidad de semillas. Induce como máximo 512 nodos por usuario, usa alpha 0.85, máximo 100 iteraciones y tolerancia L1 global 1e-6. La masa dangling vuelve a las semillas. Una falta de convergencia produce fallback G declarado. Compara H/G/PPR en la misma muestra y diez permutaciones sin propietarios fijos; conserva el prefijo del receptor y popularidad. Usuarios sin semillas se incluyen en las métricas de la cohorte pero se excluyen explícitamente del contraste de personalización. Con menos de dos usuarios con semillas ese contraste no aplica. Las listas de fallback G también se conservan como listas de los donantes cuando corresponda.

Los resultados completos de G y los de la muestra PPR están separados. No se promueve un motor a partir de la muestra. Las membresías/aciertos exclusivos describen las fuentes del pool G finalmente seleccionado; no estiman causalidad ni el valor de cambiar hiperparámetros.

[Prompt histórico Q2](mac24-q2-prompt.md), ya ejecutado; no reutilizarlo para iniciar Q3.

## Referencias y preparación

No repetir Q1. Cada Q2 exige el ZIP cerrado y los artefactos privados originales de Q1 en **esta misma mac24**. Se verifican hashes de snapshot, rankings y métricas, etiquetas, población, fechas, fuente y protocolo antes de reutilizarlos en lectura. Un ZIP sin `work` no basta. El nuevo lock declara SciPy 1.18.1 directamente; esto no cambia parámetros ni invalida los contratos históricos. No se exige igualdad de locks entre Q1 y Q2.

| Paso Q2 | Fold/tamaño | `--reference-run-id` de Q1 |
|---|---|---|
| 1 | V3 demo | `quality-mac24-20261020T201526Z-09fc3714` |
| 2 | V3 pilot | `quality-mac24-20261020T201604Z-9415f25d` |
| 3 | V1 full | `quality-mac24-20261020T201629Z-9e5fc20a` |
| 4 | V2 full | `quality-mac24-20261020T201718Z-80c2b832` |
| 5 | V3 full | `quality-mac24-20261020T201805Z-63eb4569` |

1. Fijar el commit recibido con clon limpio; conservar cambios existentes y no actualizar durante la secuencia. Leer AGENTS.md y `evidence/quality-q1-review.json`.
2. Conservar Colima `hm-project` 14 GiB/4 CPU y perfil `mac24` 10 GiB/3 CPU/2 hilos. No ejecutar dos trabajos pesados. Mantener la Mac despierta mientras el worker siga activo.
3. Conservar dataset T2, features/checkpoints y los cinco `work` Q1. Inventariar SHA-256 antes/después de features, checkpoints y artefactos Q1; el inventario con rutas/IDs permanece local. No abrir la partición test para evaluar. Registrar conteo de archivos verificados y coincidencia agregada.
4. Conservar `ARTIFACTS_DIR/quality-budget/quality-v1.json`. Q1 dejó aproximadamente 108.35 segundos full y 12.15 segundos piloto consumidos; el archivo local es la autoridad. No reiniciar contadores al actualizar código. Continúan 2 h por intento, 2 h acumuladas de pilotos y 23.5 h full, con 30 min reservados a Q4. Demo se registra aparte.
5. Comprobar los ZIP de la tabla en `ARTIFACTS_DIR/handoff/` o `incoming/`. Si falta alguno, `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download RUN_ID` lo descarga y valida. Si faltan artefactos privados, informar el bloqueo y no reconstruir Q1 sin autorización.
6. Ejecutar `SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite quality` después de construir la imagen nueva; `quality-job start` la construye automáticamente y ejecuta unitarias/paridad antes del trabajo. Alternativamente usar el primer `start` como preparación verificada y correr la suite quality después de terminar la demo. No hace falta instalar SciPy ni otras bibliotecas científicas en el host.

Se exigen 20 GiB libres antes de iniciar y al menos 5 GiB durante todo el intento. El supervisor controla tiempo, disco, RSS/cgroup/swap/OOM. Los originales se montan en lectura, sólo el directorio del intento nuevo permite escritura y el worker no tiene red. Las imágenes se construyen con dependencias bloqueadas y CA de confianza local; no desactivar TLS ni copiar CA/credenciales desde otra Mac.

## Ejecución secuencial

Sustituir `SHA_COMPLETO_RECIBIDO` por el commit del prompt. Ejecutar **una línea, esperar cierre y publicar el reporte antes de continuar con la siguiente**. No lanzar este bloque como cinco jobs simultáneos.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage graph --config quality-v1 --fold V3 --size demo --reference-run-id quality-mac24-20261020T201526Z-09fc3714 --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Después del cierre satisfactorio de demo:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage graph --config quality-v1 --fold V3 --size pilot --reference-run-id quality-mac24-20261020T201604Z-9415f25d --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Después del piloto, cada full por separado:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage graph --config quality-v1 --fold V1 --size full --reference-run-id quality-mac24-20261020T201629Z-9e5fc20a --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage graph --config quality-v1 --fold V2 --size full --reference-run-id quality-mac24-20261020T201718Z-80c2b832 --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage graph --config quality-v1 --fold V3 --size full --reference-run-id quality-mac24-20261020T201805Z-63eb4569 --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Para **cada** intento, guardar el run_id anunciado y ejecutar:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job status --run-id RUN_ID
# Repetir status con espera hasta estado final; entonces:
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

`read-handoff` devuelve 1 ante ejecución fallida válida y 2 ante paquete inválido. Publicar fallos válidos también está autorizado. Una métrica peor es evidencia y no detiene la secuencia. Un fallo operativo la detiene: conservar diagnóstico, no cambiar código, parámetros, presupuesto o recursos para ocultarlo. No hay resume Q2; un nuevo intento requiere otro run_id y conserva los anteriores. Si se agota presupuesto, parar; no reducir población y llamarla full.

## Artefactos y devolución

Cada intento Q2 conserva en su `work` privado:

- `graph/`: IDs/soporte, semillas, CSR dispersa, hashes, conteos y tamaño temporal/final. Las particiones temporales se borran sólo después de incorporarse; sus hashes quedan en el manifiesto.
- `ranking-G/`, `evaluation-G/`: candidatos y métricas individuales para Q3/Q4.
- `pagerank/`: rankings y evaluaciones de la cohorte, diez permutaciones, y `iterations.parquet` con convergencia por usuario. Se conservan localmente para auditoría.
- P/H y snapshot siguen en el `work` Q1 referenciado; Q2 no los modifica ni duplica completos.

`result.json` agrega métricas P/H/G, segmentos, recompra/descubrimiento, exposición, membresías y aciertos exclusivos, detalles del grafo, control PPR separado y costes por componente. `candidate_cost_seconds` incluye construcción del grafo y recuperación; excluye PPR y evaluación. Si posteriormente se reutiliza una caché, el coste de construcción original del manifiesto sigue contándose. El ZIP conserva sólo agregados, hashes, configuración, entorno y logs; no publica tablas, IDs, grafos ni modelos.

Al cerrar los cinco intentos o el primer fallo, actualizar `evidence/quality-q2-mac24.json` y una nota en esta guía con commit, run_id, estado, fold/tamaño, referencias Q1, fuente/protocolo, métricas, grafo, PPR, costes, presupuesto, RSS/cgroup/swap/OOM, disco, hashes originales y SHA/URL de cada Release. Redondear métricas en Git a ocho decimales y GiB a seis; mantener precisión completa en ZIP. No declarar aceptación ni avanzar Q3 automáticamente. Publicar cambios de evidencia y avisar al usuario; la otra Mac no se activa automáticamente.

## Cobertura académica

Q2 añade pesado de vínculos (3.3), caminatas aleatorias/PageRank personalizado acotado (3.1/3.4), similitud entre nodos (5.5), recomendación por interacción implícita y filtrado colaborativo por artículos (4.1/4.3) y evaluación temporal (4.5). No implementa HITS, comunidades ni comunidades traslapadas; un grafo comprador–artículo/coocurrencia tampoco convierte los datos en una red social real. Q4 consolidará cobertura y conclusiones con los resultados completos.

## Evidencia local y revisión

La [evidencia local Q2](../evidence/local-quality-q2.json) registra 133 pruebas distintas: 58 científicas, 67 host y 8 de infraestructura Docker. La suite completa científica inicial pasó 57; la prueba adicional de offsets/recencia pasó después y la suite quality final pasó 34 unitarias y 7 integraciones. También se verificaron los 24 ZIP históricos. La revisión final independiente no encontró hallazgos accionables.

La [demo oficial](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-local-20261020T204855Z-99a612bd), commit `db3d7389da74a2d45022da37b4262f2b02a898ff`, pasó con 33 compradores, 24 artículos, 224 cestas y 48 aristas. Las 32 ejecuciones PPR con semillas convergieron; un comprador quedó fuera del contraste de personalización por no tener semillas. Duración 6.047426 s, RSS 0.434319 GiB, sin swap/OOM. Se exportó, validó, publicó y verificó la descarga del ZIP privado. G empató con H y las permutaciones no alteraron materialmente las métricas de esta fixture: no es una mejora demostrada en H&M.

La implementación preserva la incidencia dispersa en RAM y persiste coocurrencias por bloques; la prueba de escala real corresponde a mac24. Reutilizar Q1 requiere conservar sus tablas privadas. El fallback G de un donante no convergente entra explícitamente en el control de permutaciones; el reporte permite separar esa limitación. La revisión no acreditó resultados H&M, selección Q3/Q4 ni despliegue T4. No hay hallazgos menores aplazados.


## Resultados mac24: 20 de octubre de 2026

Los cinco intentos Q2 pasaron desde el commit limpio `77267d02248e082259cccc4c573f9adff72d2114`. La suite `quality` pasó en mac24 después de la demo y antes del piloto. Cada ZIP se exportó, validó, publicó y descargó/verificó antes del siguiente intento. [Evidencia agregada Q2](../evidence/quality-q2-mac24.json).

Colima 14 GiB/4 CPU; jobs 10 GiB/3 CPU/2 hilos, secuenciales, con suspensión inhibida. Los 500 archivos de features, entrenamientos/checkpoints y cinco `work` Q1 conservaron SHA-256 antes/después. Inventarios y tablas individuales permanecen locales. No se repitió Q1 ni entrenamiento ni se evaluó test.

| Corrida / Release | Compradores | Tiempo job | RSS / cgroup GiB |
| --- | ---: | ---: | ---: |
| V3 demo: [quality-mac24-20261020T211723Z-03cfe799](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T211723Z-03cfe799) | 33 | 6.012 s | 0.434818 / 0.355473 |
| V3 pilot: [quality-mac24-20261020T211825Z-12071578](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T211825Z-12071578) | 2,000 | 29.050 s | 1.838543 / 1.924377 |
| V1 full: [quality-mac24-20261020T211909Z-cf26a40a](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T211909Z-cf26a40a) | 80,253 | 75.176 s | 1.884926 / 2.054413 |
| V2 full: [quality-mac24-20261020T212038Z-fd730ff0](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T212038Z-fd730ff0) | 75,822 | 72.177 s | 1.885216 / 1.991974 |
| V3 full: [quality-mac24-20261020T212206Z-00c9a0ec](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T212206Z-00c9a0ec) | 72,019 | 71.196 s | 1.853237 / 1.958664 |

Sin swap ni OOM. Todas las referencias Q1 y sus artefactos privados pasaron verificación. Costes offline; no representan latencia de servicio.

| Fold full | Método | MAP@12 | NDCG@12 | Recall@200 |
| --- | --- | ---: | ---: | ---: |
| V1 | P | 0.00682845 | 0.01336663 | 0.17618543 |
| V1 | H | 0.01936354 | 0.02936683 | 0.19345198 |
| V1 | G | 0.02051416 | 0.03136443 | 0.18380591 |
| V2 | P | 0.00673213 | 0.01400663 | 0.18032371 |
| V2 | H | 0.02272145 | 0.03416696 | 0.20157019 |
| V2 | G | 0.02407240 | 0.03628144 | 0.19334432 |
| V3 | P | 0.00660410 | 0.01260462 | 0.16757920 |
| V3 | H | 0.02458902 | 0.03549285 | 0.19078554 |
| V3 | G | 0.02640059 | 0.03835016 | 0.18292317 |

G mejora MAP y NDCG frente a H en los tres folds, pero reduce Recall. Esto no acredita selección final. PPR se evaluó únicamente como control separado, con 5,000 compradores por full y diez permutaciones; no se mezcla con la población full ni autoriza promoción del motor.

| Fold | Nodos / aristas | Cestas | Coste candidato (construcción + recuperación) | PPR: con semillas / convergentes / fallback por no converger |
| --- | ---: | ---: | ---: | ---: |
| V1 | 101,151 / 1,466,299 | 1,084,449 | 27.475 s | 3697 / 3697 / 0 |
| V2 | 101,945 / 1,474,641 | 1,080,058 | 26.122 s | 3702 / 3702 / 0 |
| V3 | 102,967 / 1,484,325 | 1,079,003 | 25.092 s | 3667 / 3667 / 0 |

Presupuesto acumulado Q1+Q2: full 326.898137 s, piloto 41.213648 s, demo 13.286877 s. Entradas Q1 intactas, ninguna reserva abierta; se conserva el ledger local. Espacio libre mínimo de los intentos: 198.446957 GiB.

La evidencia incluye segmentos, recompra/descubrimiento, exposición, fuentes del pool, PPR y sus controles, construcción/recuperación/evaluación, disco y presupuesto. Se conservaron grafos y métricas individuales privados para las fases posteriores. El reporte de entrega registró `quality_accepted=false` y revisión pendiente; la revisión posterior se conserva por separado a continuación, sin modificar ese registro histórico.

## Revisión de la máquina principal: Q2 cerrada

La [auditoría estructurada](../evidence/quality-q2-review.json) verifica los cinco ZIP: inventario/SHA-256, identidades, commit limpio, configuración/lock/recursos, referencias Q1, poblaciones, secuencia y presupuesto. Los agregados redondeados de Git concuerdan con los originales. P/H y sus hashes de rankings/métricas coinciden exactamente con Q1. Piloto y full V3 usan el mismo grafo global, con hashes idénticos; los tres folds tienen snapshots distintos y la misma fuente H&M. Cada log de worker registra 35 pruebas aprobadas.

| Media simple de tres folds | H | G | Cambio relativo G/H |
| --- | ---: | ---: | ---: |
| MAP@12 | 0.02222467 | 0.02366238 | +6.47% |
| NDCG@12 | 0.03300888 | 0.03533201 | +7.04% |
| Recall@200 | 0.19526924 | 0.18669113 | −4.39% |

G mejora MAP en V1/V2/V3 +5.94%/+5.95%/+7.37%, pero pierde Recall −4.99%/−4.08%/−4.12%. **Incumple el límite predeclarado de pérdida media de Recall del 1%.** Cumple los umbrales puntuales de MAP, NDCG y MAP por segmento; eso no compensa el incumplimiento de Recall. Los intervalos y la selección formal corresponden a Q4, con las tablas privadas por comprador. Esta revisión no los calcula desde medias.

La pérdida se concentra en compradores con historia de 29–90 días: Recall cae 19.13%, 19.70% y 22.57%, aunque MAP aumenta en los tres folds. En compradores con compras de 28 días mejoran ambas métricas; los segmentos sin historia o sólo historia anterior a 90 días conservan exactamente H. El grafo aporta mejores posiciones iniciales, pero el pool fijo de 200 pierde artículos relevantes: reordenarlo no puede recuperarlos. La cobertura top12 del catálogo sube de 17.84–18.71% con H a 32.78–33.98% con G; no equivale a recuperar más compras relevantes.

En la muestra PPR de 5,000 por fold convergen todos los compradores con semillas: 3,697/3,702/3,667. Los deltas de MAP frente al promedio de diez permutaciones son +0.00358544/+0.00306048/+0.00486076; NDCG/Recall también mejoran. Apoya una señal personalizada de la fuente PPR, sin intervalo nuevo ni afirmación causal. Frente a G en la misma muestra, MAP cambia −1.20%/+0.44%/−0.20%, sin ventaja consistente. PPR sigue siendo un control acotado a subgrafos de hasta 512 nodos, no un candidato full.

Los tres jobs full suman 218.548781 s, con máximo RSS 1.885216 GiB y cgroup 2.054413 GiB, sin swap/OOM. Construcción+recuperación de G suman 78.688904 s; excluyen el control PPR y evaluación. Q1+Q2 consumieron 326.898137 s del presupuesto full; quedan 84,273.101863 s para Q1–Q3 y se conservan 1,800 s reservados para Q4. Estas medidas batch no acreditan latencia de API.

La preservación de 500 originales y la suite quality posterior a demo son declaraciones de mac24: sus inventarios/log completo permanecen allí. Se validaron los ZIP y sus logs de worker aquí, sin recalcular archivos privados ni métricas individuales. La auditoría conserva esta distinción.

**Decisión:** cerrar Q2 y conservar H como referencia sin repetir Q1/Q2. RH puede mejorar el orden de H manteniendo su Recall. RG se conserva como ablación de recuperación/ordenamiento; al usar exactamente el pool G hereda su pérdida de Recall y no puede cumplir ese umbral sin cambiar el protocolo. No se cambia el pool, los parámetros ni la regla de selección. Posteriormente también se cerró [Q3](ranker-quality.md): RH/RG mejoran MAP promedio, pero incumplen segmentos. Sigue Q4; test y T4 permanecen pendientes.
