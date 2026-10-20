# Q1: referencias temporales reproducibles

Esta entrega implementa Q1-A/Q1-B del [plan](superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md). Compara P (popularidad de 7 días) y H (historial de 28 días seguido de P). Q1, [Q2](graph-quality.md) y [Q3](ranker-quality.md) están cerradas tras revisión de sus reportes mac24. [Q4](quality-summary.md) está implementada; su ejecución mac24 y revisión siguen pendientes, al igual que T4. Esta página conserva la reproducción histórica Q1, no debe repetirse por defecto. `passed` significa que la ejecución y sus controles pasaron; no significa que se haya seleccionado el mejor motor. La evidencia local se registra en `evidence/local-quality-q1.json`; la [evidencia mac24](../evidence/quality-q1-mac24.json) y su [revisión](../evidence/quality-q1-review.json) ya cierran Q1.

## Protocolo y archivos

`configs/quality/quality-v1.json` fija parámetros antes de observar los resultados. Ventanas de etiquetas: V1 [26 agosto, 2 septiembre), V2 [2 septiembre, 9 septiembre), V3 [9 septiembre, 16 septiembre), todas de 2020. Cada snapshot usa sólo compras estrictamente anteriores a su corte, para todos los compradores históricos. Test del 16–23 de septiembre permanece reservado. Los artículos relevantes fuera del catálogo siguen contando en los denominadores.

P ordena por frecuencia e ID; H por fecha de última compra, frecuencia e ID, elimina duplicados y completa con P hasta 200 artículos o el tamaño del catálogo. Se mantienen recompras. Sin historial28, H coincide con P. Se reportan MAP@12, NDCG@12, Recall@200, exposición, recompra/descubrimiento y segmentos disjuntos: reciente28, historia29–90, anterior y nuevo. El piloto selecciona hasta 2,000 compradores por SHA-256 con semilla 42, sin cambiar el snapshot ni seleccionar por aciertos. Full conserva todos los compradores de la semana.

Cada job escribe en `ARTIFACTS_DIR/data-jobs/RUN_ID/`:

- `environment.json`, configuración resuelta, logs y `result.json`: identidad, agregados y recursos.
- `work/snapshot/`: catálogo, perfiles, historial y popularidad con manifiesto de corte/entradas.
- `work/ranking-P/`, `work/ranking-H/`: rankings privados por comprador, puntuación y fuente.
- `work/evaluation-P/`, `work/evaluation-H/`: métricas privadas por comprador para el futuro bootstrap Q4.

Los directorios `work` permanecen en mac24. El ZIP `quality-handoff` incluye únicamente archivos permitidos de reporte, configuración y logs. SHA-256, tamaños e identidad del paquete se verifican antes de publicar/leer. El lector también exige la huella normativa completa de `quality-v1`, el presupuesto y la evidencia de disco/tiempo; no basta con un hash coherente de una configuración diferente. El lector rechaza reportes aprobados incompletos, métricas no finitas, fechas incoherentes, recursos incompatibles y archivos ajenos al contrato. Los reportes históricos mantienen sus contratos.

## Preparar mac24

1. Lee AGENTS.md. Obtén el commit completo recibido mediante Git, conserva cualquier cambio existente y selecciona esa revisión en un clon limpio. No actualices el código durante la secuencia. No repitas T1/T2 ni entrenamiento.
2. Conserva Colima `hm-project` en 14 GiB/4 CPU y jobs `--resources mac24` en 10 GiB/3 CPU/2 hilos. Verifica el perfil y la ausencia de otro trabajo pesado; el lanzador no modifica la VM. Usa `SSL_CERT_FILE=/etc/ssl/cert.pem`; no desactives TLS. Mantén el equipo despierto durante todo el worker, no sólo durante `start`.
3. Reutiliza `DATA_DIR/processed/hm-v1` de T2. El worker verifica integridad de particiones antes de leer. No necesita MLflow ni los pesos para calcular P/H. Conserva features/checkpoints originales: registra SHA-256 de sus archivos antes/después de toda la secuencia y devuelve conteo y coincidencia agregados. Guarda ese inventario local fuera de Git.
4. Full V3 exige el ZIP histórico `diagnose-mac24-20261020T184112Z-4b76ee72` en `ARTIFACTS_DIR/handoff/` o `ARTIFACTS_DIR/incoming/`. SHA-256 esperado: `730c5ff558a7cfdb285d05b009058aaa3291bf6f01e59145c5a4b443b51f048f`. Si falta, recupéralo con el descargador del repo. No sustituyas métricas redondeadas del resumen por el ZIP original.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download diagnose-mac24-20261020T184112Z-4b76ee72
```

El descargador conserva el ZIP recibido. Valídalo con `scripts/read-handoff.py RUTA_EXACTA.zip`. No sobrescribas un archivo ya existente. La comparación full V3 verifica fuente, población, tres métricas, exposición y segmento reciente contra los valores originales con tolerancia absoluta `1e-10`. Además, V3 verifica igualdad exacta de los rankings P/H contra las funciones históricas.

## Ejecutar la secuencia

Sustituye `SHA_COMPLETO_RECIBIDO` por el commit indicado en el prompt, no por una revisión distinta. `start` reconstruye la imagen desde el clon limpio, verifica límites/aislamiento y lanza un supervisor desacoplado. El worker ejecuta unitarias y paridad temporal antes de evaluar. `baselines` reproduce Q1; `graph` usa el procedimiento Q2 enlazado y exige una referencia Q1 en cada intento. `ranker` sigue rechazado.

Ejecuta **una orden por vez**. Antes de la siguiente, espera estado final y completa exportación, validación y publicación. Un fallo operativo detiene la secuencia; una métrica peor no la detiene ni autoriza cambiar parámetros.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage baselines --config quality-v1 --fold V3 --size demo --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Después del cierre de demo:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage baselines --config quality-v1 --fold V3 --size pilot --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Después del cierre del piloto, cada full por separado:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage baselines --config quality-v1 --fold V1 --size full --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage baselines --config quality-v1 --fold V2 --size full --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job start --stage baselines --config quality-v1 --fold V3 --size full --reference-run-id diagnose-mac24-20261020T184112Z-4b76ee72 --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Para **cada** run_id anunciado, incluidos fallos:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job status --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py quality-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

`read-handoff` devuelve 0 aprobado, 1 fallo válido y 2 paquete rechazado. Un fallo válido se publica; un paquete rechazado se conserva para diagnóstico. Exportar antes del cierre está bloqueado. Si se necesita detener: `quality-job stop --run-id RUN_ID`, después esperar/exportar. No existe `resume` Q1: cada reintento tiene otro ID y conserva parciales anteriores; informar el fallo antes de repetir. La publicación privada ya está autorizada y no activa automáticamente al otro agente.

## Presupuesto y aislamiento

Cada job dispone de hasta dos horas de contenedor, incluyendo verificación de pruebas e IO; la construcción de imagen se registra por separado y no consume ese tiempo de cómputo. Pilotos H&M comparten dos horas acumuladas. Los full Q1–Q3 comparten 23.5 horas y reservan 30 minutos para Q4. Demo tiene límite de dos horas por job y se contabiliza aparte. Fallos e interrupciones consumen presupuesto; cambiar run_id no lo reinicia.

El registro persistente `ARTIFACTS_DIR/quality-budget/quality-v1.json` se actualiza bajo el bloqueo de trabajos pesados. `status`/`export` cierran la reserva cobrando el mayor tiempo entre Docker y la duración monotónica informada; si desaparece el contenedor se cobra toda la reserva. Una reserva sin cerrar impide el siguiente job. No borres ni edites el registro para continuar; conserva el bloqueo y su diagnóstico. El presupuesto es local a la instalación de mac24, no un contador distribuido entre clones.

Se exigen 20 GiB libres al inicio. El supervisor comprueba espacio cada segundo y mata el worker al bajar de 5 GiB o agotar tiempo; guarda motivo y recursos. Repite la comprobación al cerrar, aunque el worker haya terminado entre sondeos. El job usa red `none`, raíz de contenedor en lectura, entradas `/data` y `/artifacts` en lectura y sólo su directorio nuevo en escritura. No hay dos jobs pesados simultáneos. Reinicio de Docker/VM o terminación abrupta puede impedir el cierre del worker; `status` no lo interpreta como aprobado. No se borra nada automáticamente para liberar espacio.

## Devolver y revisar resultados

Sólo después del cierre de los cinco intentos (o del primer fallo) comprueba hashes originales y actualiza `evidence/quality-q1-mac24.json` y una nota al final de esta guía. Incluye commit ejecutado, run_id, estado, fold/tamaño, compradores/catálogo, métricas P/H, segmentos, paridad V3, RSS/cgroup/swap/OOM, tiempo, presupuesto acumulado, espacio libre, SHA del ZIP y URL de Release. Redondea resúmenes en Git a ocho decimales y memoria GiB a seis; conserva precisión completa en ZIP. No publiques IDs individuales ni archivos `work`.

La Mac principal descarga los reportes con `github-handoff.py download RUN_ID`, verifica integridad y compara agregados contra el protocolo. Q1 sólo se cierra después de revisar los cinco ZIP. Entonces se prepara Q2 (grafo y PageRank), manteniendo test/T4 pendientes.

Para desarrollo local, la comprobación reproducible es:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima python3 scripts/project.py verify --suite quality
```

La demo oficial de la primera Mac usa `--machine-id local --profile default --resources base`; no representa rendimiento ni calidad de mac24.

## Evidencia local de esta entrega

La [evidencia estructurada](../evidence/local-quality-q1.json) registra 116 pruebas distintas: 65 host, 43 unitarias/integración científica y 8 de infraestructura Docker. La revisión final detectó tres problemas operativos; sus regresiones fallaron antes de corregirlos y pasaron después. La imagen final se probó sin montajes auxiliares de código y conserva compatibilidad con los 19 ZIP históricos recibidos.

La [demo oficial final](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-local-20261020T200951Z-1f9c6a28), ejecutada en `502e4a932e22374ae3d78ceda2f7c4c6903083fc`, pasó con 33 compradores, 24 artículos y paridad exacta V3. Duró 6.006559 s, con RSS máximo 0.412128 GiB, sin swap ni OOM. El ZIP se exportó, validó, publicó y volvió a descargar/verificar. Se conserva la primera demo anterior a las correcciones. Estas son mediciones sintéticas de la primera Mac; no acreditan calidad ni rendimiento H&M en mac24.


## Ejecución mac24: 20 de octubre de 2026

Los cinco intentos pasaron desde `120e8f3f68c1b1c7efe0786585d52490dc0e299a`, con clon limpio, Colima 14 GiB/4 CPU y límites efectivos 10 GiB/3 CPU/2 hilos. Se ejecutaron secuencialmente y cada ZIP se validó, publicó y descargó/verificó antes del siguiente. [Evidencia mac24](../evidence/quality-q1-mac24.json).

| Fold / tamaño y Release | Compradores / catálogo | Tiempo job | Pico RSS / cgroup GiB | MAP P / H |
| --- | ---: | ---: | ---: | ---: |
| V3 demo: [quality-mac24-20261020T201526Z-09fc3714](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T201526Z-09fc3714) | 33 / 24 | 7.013 s | 0.408321 / 0.344849 | 0.20251596 / 0.96565657 |
| V3 pilot: [quality-mac24-20261020T201604Z-9415f25d](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T201604Z-9415f25d) | 2,000 / 102,967 | 12.034 s | 2.197918 / 2.524887 | 0.00619763 / 0.02215935 |
| V1 full: [quality-mac24-20261020T201629Z-9e5fc20a](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T201629Z-9e5fc20a) | 80,253 / 101,151 | 35.060 s | 2.189018 / 2.510223 | 0.00682845 / 0.01936354 |
| V2 full: [quality-mac24-20261020T201718Z-80c2b832](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T201718Z-80c2b832) | 75,822 / 101,945 | 34.055 s | 2.239624 / 2.563431 | 0.00673213 / 0.02272145 |
| V3 full: [quality-mac24-20261020T201805Z-63eb4569](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-quality-mac24-20261020T201805Z-63eb4569) | 72,019 / 102,967 | 39.065 s | 2.202866 / 2.529861 | 0.00660410 / 0.02458902 |

Sin swap ni OOM. Full V3 verificó rankings P/H exactos contra las funciones históricas y el ZIP `diagnose-mac24-20261020T184112Z-4b76ee72`, tolerancia 1e-10. Presupuesto cerrado acumulado: demo 7.146425 s, piloto 12.151669 s, full 108.349356 s; ninguna reserva pendiente. Se conservan `work` privados y el registro local de presupuesto para la revisión y Q4 posterior.

Los 606 archivos inventariados de datos procesados y entrenamientos coinciden antes/después. Limitación del inventario: las features no se incluyeron en el inventario nuevo previo; al cierre se verificaron 113 archivos de features de entrenamiento/validación contra sus manifiestos históricos y se enlazaron los dos manifiestos H&M con sus hashes del entrenamiento original. No se leyeron payloads de features de test. Inventarios detallados permanecen fuera de Git.

Las métricas P/H, segmentos, recompra/descubrimiento, exposición, espacio libre, presupuesto y SHA de cada ZIP están en la evidencia. `passed` acredita ejecución; Q1 ya pasó la revisión de los cinco reportes en la máquina principal (véase el apartado siguiente). No hubo entrenamiento ni evaluación de test ni avance a Q2–Q4 o T4.

## Revisión de la máquina principal: Q1 cerrada

Se descargaron los cinco ZIP desde GitHub y se verificaron inventario, tamaños/SHA-256, package ID, commit, configuración normativa, lock, recursos, aislamiento, secuencia y presupuesto acumulado. Los agregados redondeados de Git coinciden con los resultados originales. Piloto y full V3 comparten snapshot; V1/V2/V3 tienen snapshots distintos con una misma fuente H&M. La [revisión estructurada](../evidence/quality-q1-review.json) conserva comprobaciones y límites.

Se recalcularon las comparaciones de métricas, exposición y segmentos de V3 contra el ZIP histórico de historial reciente: diferencia de las métricas globales exactamente cero para P y H. El worker informó igualdad exacta de rankings, que permanecen privados en mac24; aquí se compararon agregados, no filas individuales. Se corrigió en el resumen Git la tolerancia `absolute_tolerance`, que el redondeo había convertido de `1e-10` a `0.0`; el ZIP original era correcto y se conserva intacto.

| Fold full | MAP@12 P | MAP@12 H | Mejora relativa H/P |
| --- | ---: | ---: | ---: |
| V1 | 0.00682845 | 0.01936354 | +183.57% |
| V2 | 0.00673213 | 0.02272145 | +237.51% |
| V3 | 0.00660410 | 0.02458902 | +272.33% |

H también supera a P en NDCG@12 y Recall@200 en cada semana. La media no ponderada de MAP entre semanas es 0.02222467 frente a 0.00672156 (+230.65%). Es evidencia descriptiva temporal, sin nuevo intervalo de confianza ni test final. Q4 calculará sus contrastes con las métricas por comprador conservadas en mac24.

La mejora tiene un límite claro: entre 55% y 56% de los compradores semanales no tienen historial28 y reciben el mismo respaldo que P. En artículos que el comprador no había comprado antes, MAP de H cae entre 26.88% y 30.18% respecto a P. H obtiene su ventaja global por recompras; no se debe describir como mejora universal de descubrimiento. Q2/Q3 ya estaban previstos para contrastar recuperación y ordenamiento; este hallazgo no modifica sus parámetros congelados.

Las tres evaluaciones full sumaron 108.179411 s de job; máximo RSS 2.239624 GiB y máximo cgroup 2.563431 GiB, sin swap/OOM. Se conciliaron 108.349356 s cobrados al presupuesto full. Quedan 84,491.650644 s para Q1–Q3, 7,187.848331 s de pilotos y 1,800 s reservados para Q4. El espacio mínimo observado fue 202.403690 GiB. No son mediciones de latencia de la futura API.

La preservación de 606 archivos y la comprobación posterior de 113 features son declaraciones de mac24 respaldadas por sus inventarios locales; no se recalcularon esos hashes en la primera Mac. Se mantiene visible la omisión de features en el inventario previo, sin atribuirle un fallo de calidad. El aislamiento de los jobs se verificó en los reportes.

**Decisión Q1:** conservar H como referencia fija y no repetir entrenamiento ni Q1. Posteriormente también se cerraron [Q2](graph-quality.md) y [Q3](ranker-quality.md): G/RG incumplen Recall; RH/RG incumplen segmentos pese a mejorar MAP promedio. El siguiente paso es ejecutar [Q4](quality-summary.md) sobre los artefactos ya cerrados; test, selección final y T4 siguen pendientes.
