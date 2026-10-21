# Q4: selección reproducible del motor

**Q1–Q4 cerradas:** el resumen full de mac24 y sus doce fuentes se descargaron y auditaron. H queda seleccionado para preparar el servicio. La reproducción siguiente es histórica; no repetir Q4 salvo solicitud explícita. El comando de resumen consume las evaluaciones existentes y aplica `quality-v1`, sin entrenar, regenerar rankings, abrir test ni desplegar T4.

## Entradas y verificaciones

`configs/quality/campaign-mac24.json` identifica los doce reportes full: Q1, Q2, RH y RG para V1/V2/V3. Cada entrada necesita su ZIP cerrado en `ARTIFACTS_DIR/handoff` o `incoming`, y sus manifiestos y tablas de métricas en `ARTIFACTS_DIR/data-jobs/RUN_ID/work`. Los ZIP agregados solos no permiten calcular intervalos. Si faltan ZIP pueden descargarse desde las Releases privadas; si faltan tablas privadas, detenerse y reportar el bloqueo, sin reconstruirlas ni sustituir corridas.

Se comprueban inventario y SHA-256 de los ZIP, protocolo/dataset, etapas, población, cadena Q3→Q2→Q1, identidad de rankings y etiquetas, SHA/tamaño/filas de las métricas individuales, segmentos y medias recalculadas. Todos los métodos de un fold deben tener los mismos compradores y denominadores. RH/H y RG/G deben conservar Recall@200 para cada comprador.

Q4 verifica los bytes de las tablas de métricas. Los rankings se vinculan mediante su manifiesto y la huella ya registrada en el reporte fuente; no vuelve a leer todos sus bytes (`ranking_bytes_rehashed=false`). Las predicciones fueron verificadas en Q1–Q3. Esta distinción figura en el resultado. Los artefactos fuente se montan en lectura; sólo la carpeta de la nueva corrida es escribible y el contenedor no tiene red.

## Regla estadística y decisión

Se remuestrea con reemplazo la unión de compradores, conservando juntas sus observaciones en distintas semanas. Cada réplica normaliza por el número de observaciones remuestreadas de cada fold y promedia las tres semanas con igual peso. Se usan 2,000 réplicas, semilla 42, y diferencias pareadas G/RH/RG contra H. Una réplica sin observaciones de algún fold se descarta y vuelve a sortear; el conteo se informa. Esto sólo es plausible en fixtures diminutos.

MAP@12 usa intervalos bilaterales Bonferroni de 98.333333%, manteniendo una familia de tres contrastes aunque falte un candidato. Se incluyen intervalos exploratorios de 95% para MAP/NDCG/Recall. La prueba unitaria contrasta todas las réplicas contra una implementación independiente sobre un fixture de compradores repetidos.

Un candidato sólo es elegible si tiene los tres folds, mejora MAP medio al menos 2%, gana en al menos dos semanas, no pierde más de 2% en ninguna, y el límite inferior del intervalo principal es positivo. NDCG y Recall medios no pueden perder más de 1%; en cada segmento de al menos 1,000 compradores por fold, MAP no puede perder más de 5%. Ante una referencia igual a cero se exige no empeorar; el incremento MAP medio debe ser estrictamente positivo.

Entre elegibles se identifica el máximo MAP y se consideran empatados quienes están a no más de 0.0001 de ese máximo. Se decide por menor coste, menor pico de memoria y orden estable G/RH/RG. El coste suma preparación, ajuste y ranking de las tres fuentes; excluye controles/evaluación. La memoria es el pico del job fuente, incluido su control: una cota superior, no memoria aislada del candidato. P/H no tienen coste separado medido y no intervienen en ese desempate. Si ninguno cumple, se conserva H. No se modifican umbrales, pools ni fallbacks a partir de esta evidencia.

Un candidato declarado explícitamente ausente se informa incompleto y nunca se promueve. Una fuente declarada pero corrupta o ausente es fallo operativo. El manifiesto mac24 declara todos los candidatos; no editarlo para eludir errores. `comparison_complete` exige tres candidatos completos; `selection_finalized` exige además alcance full. La demo nunca congela el motor. `quality_accepted=false` y `test_evaluated=false` permanecen en toda salida Q4.

## Ejecución en mac24

Conservar Colima `hm-project` 14 GiB/4 CPU, perfil job `mac24` 10 GiB/3 CPU/2 hilos y equipo sin suspensión. Un único trabajo pesado a la vez. Usar clon limpio en el commit recibido, sin actualizarlo durante la corrida. Conservar `.env` propio, datos, checkpoints, `work`, ZIP y registro `quality-budget`; no borrar ni reiniciar el presupuesto. El lanzador exige 20 GiB libres al iniciar y supervisa 5 GiB durante el job.

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
docker compose --env-file .env --file compose.yaml build jobs
python3 scripts/project.py verify --suite quality
python3 scripts/project.py quality-job summarize --campaign configs/quality/campaign-mac24.json --machine-id mac24 --profile hm-project --resources mac24 --expected-commit SHA_COMPLETO_RECIBIDO
```

Actualizar Git no actualiza la imagen Docker. El paso `build jobs` anterior incluye Q4 antes de verificar; usar el `.env` y `.runtime/build-ca.pem` propios ya preparados en mac24. No copiar certificados ni credenciales entre equipos. `verify` sólo usa fixtures sintéticos. Si pasa, lanzar una única corrida Q4 full. `summarize` construye la imagen desde código limpio y usa la reserva acumulada `summary` de 1,800 s, independiente de las 23.5 h asignadas a Q1–Q3. Registra límites efectivos, RSS/cgroup/swap/OOM, disco, duración y consumo acumulado. No hay reanudación ni ampliación automática del presupuesto. Si falla, conservar y publicar el intento; detenerse antes de reintentar o cambiar parámetros.

Guardar el `run_id` anunciado, consultar hasta estado final y cerrar el intercambio:

```bash
python3 scripts/project.py quality-job status --run-id RUN_ID
python3 scripts/project.py quality-job export --run-id RUN_ID
python3 scripts/read-handoff.py RUTA_EXACTA_ZIP
python3 scripts/github-handoff.py upload RUTA_EXACTA_ZIP
```

Publicar también el reporte válido de fallo. `read-handoff` devuelve 0 si pasó, 1 si documenta un fallo y 2 si el paquete es inválido. No publicar un ZIP inválido: conservarlo y explicar el problema. Si el lanzador falla antes de anunciar una corrida, guardar el error y el diagnóstico disponible. La publicación de estos reportes privados ya está autorizada.

## Entregables y cierre

El ZIP contiene únicamente configuración, entorno, logs y resultado agregado. Incluye doce fuentes/huellas, quince identidades de evaluación, poblaciones, métricas y segmentos, deltas/intervalos, costes, razones de elegibilidad y procedimiento elegido. Las tablas por comprador y las réplicas bootstrap quedan locales en `work`; no viajan a GitHub como código ni adjuntos.

Después del estado final, mac24 puede agregar `evidence/mac24-quality-q4.json` con identidad, resultado agregado, recursos, presupuesto y URL de Release, sin compradores, rutas sensibles ni réplicas. Commit/push de evidencia sólo después de exportar/publicar; nunca cambiar código durante el job. La Mac principal descargará el ZIP, verificará su contrato y reconciliará las fuentes, deltas, costes y decisión. No podrá recalcular los intervalos sin las tablas que permanecen en mac24.

La revisión ya produjo `evidence/quality-decision-mac24.json`, congeló H y confirmó los contratos de T4-A/T4-B y T5. Se reutiliza el diseño de historial y popularidad preparado. T8 evaluará una sola vez el procedimiento congelado y P/H en test, sin cambiar el ganador por ese resultado. La API aún deberá demostrar 20 solicitudes/s, p95 < 200 ms y errores < 1%; el tiempo offline de Q4 no acredita esas metas.

## Resultado revisado y límites

Los reportes actuales muestran mejoras de MAP medio G +6.47%, RH +4.03% y RG +14.48% frente a H. G/RG incumplen el límite de Recall; RH/RG incumplen segmentos. La decisión formal selecciona H bajo la regla predeclarada. El bootstrap completa la incertidumbre y la trazabilidad, sin borrar esos incumplimientos. Un framework más complejo no explica por sí solo la calidad: importan el pool, las señales, la ventana temporal y el criterio de evaluación. La [cobertura del curso](course-coverage.md) distingue experimentos realizados de temas pendientes.

La [evidencia local Q4](../evidence/local-quality-q4.json) registra 155 pruebas distintas aprobadas (77 científicas, 70 host, 8 de infraestructura Docker), una prueba externa de MLflow omitida, revisión independiente y demo desacoplada de 33 compradores. Se conservaron los reportes del primer fallo de preparación y del nuevo intento aprobado. La prueba de escala sintética de 152,019 compradores no representa calidad H&M ni tiempo de IO del resumen completo.

## Cierre de mac24 y congelación

La [auditoría principal](../evidence/quality-decision-mac24.json) valida el ZIP `quality-mac24-20261021T042757Z-915df40a`, ejecutado desde `de04e5c`, y reconcilia sus doce fuentes Q1/Q2/RH/RG, quince evaluaciones, medias, deltas, costes y motivos. La evidencia recibida coincide exactamente con el resultado cerrado. La comparación abarca 190,944 compradores únicos y 228,094 observaciones comprador–semana; V1/V2/V3 contienen 80,253/75,822/72,019 compradores. Las 2,000 réplicas no descartaron ningún sorteo por fold vacío.

| Candidato frente a H | MAP medio | Cambio relativo | IC98.3333% del delta MAP absoluto | Motivo de exclusión |
| --- | ---: | ---: | --- | --- |
| G | 0.02366238 | +6.47% | [0.00126171, 0.00162986] | Recall −4.39%, límite −1% |
| RH | 0.02311949 | +4.03% | [0.00062544, 0.00114128] | MAP por segmento hasta −27.66%, límite −5% |
| RG | 0.02544361 | +14.48% | [0.00294479, 0.00348630] | Recall y segmentos |

H tiene MAP medio 0.02222467, NDCG 0.03300888 y Recall 0.19526924. RG obtiene el mayor MAP; H se elige porque ningún candidato satisface todos los controles fijados antes de medir. Los tres intervalos positivos confirman la mejora promedio de MAP bajo este análisis, sin anular las regresiones ni demostrar un óptimo global.

El job duró 5.026059 s; se cobraron 5.186838 s al presupuesto summary de 1,800 s. Pico RSS 0.513779 GiB y cgroup 0.498383 GiB, sin swap ni OOM, con límites efectivos 10 GiB/3 CPU/2 hilos. El tiempo offline no mide latencia de servicio. Las 46 pruebas unitarias, 15 integraciones y 70 pruebas host previas, y la preservación de 2,143 archivos, son declaraciones de mac24; sus inventarios privados no se transfirieron. Aquí se verificaron los reportes y se recalcularon agregados/criterios, no las réplicas a partir de tablas privadas.

Se congela H, ventanas 28/7, orden por fecha/frecuencia/ID del historial y frecuencia/ID de popularidad, deduplicación conservando la primera aparición y hasta 200 candidatos. `quality-v1` y su configuración permanecen inalterados; `recent-history-v1` identifica las reglas históricas compatibles. No requiere entrenamiento ni refit de pesos, sólo preparar estado estrictamente anterior al corte. Las huellas de dataset, configuración, código, campaña y evidencia están en la auditoría.

[T4-A](releases.md) implementa el paquete con catálogo, compradores históricos, historial, popularidad, reglas, manifiesto y referencias de validación/selección; falta reproducir H&M en mac24. T4-B añadirá API, activación y rollback; T5 actualizará ventanas desde transacciones o conteos diarios. T8 reconstruirá estado al 2020-09-16 y evaluará una única vez H y P (H coincide con elegido/referencia, sin duplicar método), sin ajustar a partir de test. T4-B no se inició y `quality_accepted=false` permanece hasta la aceptación final.
