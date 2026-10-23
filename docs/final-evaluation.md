# T8-C: evaluación final H/P

Estado: lanzador operativo preparado y validado localmente con datos sintéticos. La siguiente ejecución mac24 usa `scripts/mac24_t8_final.py --expected-commit SHA_COMPLETO`. **Todavía no se ha leído el test H&M.** No usar directamente el componente ni los prompts históricos. Una salida `passed` requiere revisión principal; no cambia la selección Q4 ni cierra todo T8.

## Alcance cerrado antes de test

Protocolo `final-hp-v1`. H continúa seleccionado por Q4; P es la referencia. No se entrenan modelos, ajustan ventanas ni seleccionan métodos a partir del resultado final. Preparar estado con todas las transacciones estrictamente anteriores a 2020-09-16. Evaluar compras de `[2020-09-16, 2020-09-23)`, sin muestrear compradores. Historial28d y popularidad7d conservan orden, desempates, deduplicación y respaldo existentes.

Catálogo y perfiles se construyen antes de leer etiquetas. Se evalúan compradores con al menos una compra en el periodo; duplicados comprador/artículo cuentan como una etiqueta. Compradores sin historial permanecen en la población y reciben popularidad. Artículos relevantes ausentes del catálogo permanecen en el denominador. Usuarios sin compra de test no se agregan como ceros a la media.

Reportar MAP@12, NDCG@12 y Recall@200 macro por comprador, deltas H−P, cuatro segmentos, repetición/descubrimiento, exposición, catálogo y denominadores. Son resultados descriptivos de generalización: no constituyen una nueva selección ni una prueba de superioridad estadística. Los archivos por comprador permanecen privados; sólo agregados viajan por GitHub.

## Componente y pruebas

`src/hm_recommender/quality/final.py` usa contratos separados para la ventana final. Reutiliza construcción, rankings y métricas de calidad mediante tipos de artefacto explícitos. `FoldSpec` y el constructor ordinario siguen rechazando test. El componente exige una carpeta nueva, conserva la reserva ante fallo y no reanuda automáticamente. Esa protección local **no sustituye un ledger global**.

Las pruebas `tests/integration/test_final_evaluation.py` crean CSV/ZIP sintéticos temporales, sin montar datos H&M. Comprueban extremos temporales, duplicados, compradores nuevos/inactivos, artículos no disponibles, segmentos, orden H frente a P, denominadores y rechazo de reutilización de destino. La regresión V1–V3 conserva las métricas y rankings previos.

## Ejecución mac24

AC, tapa y Terminal abiertas. No usar la demo/UI para enviar eventos, iniciar DAG o ejecutar otros trabajos durante el ensayo. El clon debe estar limpio y fijado al SHA completo entregado.

```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/mac24_t8_final.py --expected-commit SHA_COMPLETO
```

El lanzador realiza sus preflight en cada ejecución. `--preflight-only` permite revisar los mismos requisitos sin construir imágenes, detener servicios ni reservar test. Comprueba antecedentes T7/T8-A/capacidad/T8-B y selección Q4, sus ZIP/huellas, manifest del dataset, nueve contenedores exactos, imágenes/límites/salud, DAG pausados y sin ejecuciones, fuente API/scheduler, H activo, disco libre≥20GiB, AC y suite host. Nunca repite T8-A/B.

Bajo el bloqueo de trabajos pesados, crea estado durable en `.runtime/t8-final-maintenance` y detiene seis auxiliares por ID: scheduler, dag-processor, API Airflow, API restaurada T7, MLflow y PostgreSQL restaurado T7. API/PostgreSQL/UI **originales** conservan generación. Sus límites suman5GiB; evaluador5GiB+margen1GiB exige11GiB dentro de13.621GiB efectivos. No se amplía Colima ni ninguna cuota. La construcción de imagen ocurre en esta ventana, antes de test; sus recursos no se presentan como los del evaluador.

Construye sólo jobs, fija su digest y verifica que el código instalado coincide con `src`. Ejecuta tests sintéticos y regresión V1–V3 en esa imagen, sin datos reales montados. Después crea de forma exclusiva `artifacts/.final-test-once.json`, ligada al commit, imagen, fuentes y evidencias. Reserva y directorio de mantenimiento nunca se borran automáticamente, incluso si el fallo sucede antes de leer etiquetas.

El worker real usa5GiB/2CPU/dos hilos, swap deshabilitado, red `none`, sistema raíz de sólo lectura y dataset montado en lectura. Sólo escribe en su directorio privado. Verifica particiones antes y después. Espera una señal del host; éste captura el cgroup antes de autorizar el hijo. El supervisor limita al hijo a5400s, termina y, si es necesario, mata el proceso tras10s adicionales. No reintenta. Conserva su contenedor inactivo en espera para capturar los contadores finales; el host lo detiene sin eliminarlo.

Se capturan cgroups del evaluador cada5s y al final: límites efectivos, identidad estable, huecos≤35s, cero swap y sin nuevos OOM. Se conservan picos y eventos max sin reiniciarlos. Se exige cobertura de la duración del hijo y discrepancia UTC/monotónico≤2s. No mide latencia del servicio ni concurrencia sostenida. Espacio libre inferior a5GiB o pérdida de supervisión detienen el flujo; no autorizan otro intento.

Al terminar se recuperan los mismos seis contenedores, en orden PostgreSQL restaurado→API restaurada→MLflow→API Airflow→dag-processor→scheduler, y se comprueban salud, H activo, pausa de DAG, generaciones originales y estado agregado de BD. Los auxiliares tendrán nuevos tiempos de arranque. El reporte no afirma igualdad fila por fila de la base.

## Corte de conexión o fallo

No repetir el comando de evaluación ni borrar reservas. El hijo puede continuar en Docker después de perder SSH; su límite es90min más terminación. El modo siguiente **sólo recupera**:

```bash
python3 scripts/mac24_t8_final.py --restore-only --expected-commit SHA_COMPLETO
```

Comprueba el mismo commit y estado guardado. Si el worker sigue activo y no hay constancia de finalización, se niega a arrancar los auxiliares. Tras finalizar, detiene ese contenedor y recupera los seis IDs. No vuelve a evaluar ni reescribe reportes cerrados; un corte abrupto puede dejar evidencia incompleta para diagnóstico principal.

Ante fallo controlado se intenta recuperar y se publica el ZIP agregado `final-mac24-*` por GitHub privado, también con `failed`. Si sólo falla la subida, usar `scripts/github-handoff.py upload RUTA_ZIP` sobre el ZIP ya cerrado. Rankings, etiquetas, logs privados, particiones y tablas por comprador nunca se incluyen. El lector valida estructura, identidades, métricas, denominadores y contadores. La revisión principal decidirá el cierre; un resultado adverso no autoriza ajustes mirando test.

## Límite operativo T8-B

La auditoría T8-B conserva `failed`: sólo hubo 101 llegadas durante 5.061 s de worker, inferiores al mínimo fijado. No se repetirá para alargar artificialmente el trabajo. T8-A acredita el servicio a 20 solicitudes/s en su configuración ensayada; T8-B no acredita concurrencia sostenida. La entrega documentará esta limitación y una política de mantenimiento secuencial para trabajos pesados. Esto no cambia el estado del experimento histórico ni convierte T8 completo en aprobado.

Verificación local: 223 pruebas host, tres bootstrap nativas y 106 componentes Docker pasaron; 61 integraciones requieren servicios no iniciados. Dos bootstrap fallaron por certificados dentro de jobs y pasaron en host; el intento conjunto inicial también carecía de Git/evidencia montada para pruebas host. Detalle en `evidence/final-evaluation-local.json`. No equivale a una suite con PostgreSQL ni a ejecución mac24.

Validación del lanzador: ver `evidence/final-launcher-local.json`. El smoke Docker local usó código montado y fixtures sintéticos; no representa la construcción ni el ensayo H&M de mac24. El lanzador exige verificar nuevamente el código instalado y los tests en la imagen que construya allá.
