# T4-A: paquete portable de historial y popularidad

H quedó seleccionado al cerrar Q4. Esta entrega construye su estado local y comprueba
paridad offline al **2020-09-09**, sin entrenamiento. API, activación y rollback
pertenecen a T4-B; test y aceptación final siguen reservados para T8.
**T4-A cerrada tras revisar demo y H&M en mac24 el 2026-10-21.** La
[auditoría](../evidence/releases-review-mac24.json) verifica reportes, referencias
y paridad; el estado se registra en [evidence/releases.json](../evidence/releases.json).
Los comandos siguientes se conservan para reproducción, no para repetir la etapa
ya aprobada. El siguiente desarrollo es corregir el acceso HTTP del host en T4-B.

## Paquete local

Cada ejecución crea `artifacts/data-jobs/RUN_ID/work/release/`:

| Archivo | Contenido privado o contrato |
| --- | --- |
| catalog.parquet | Artículos observados antes del corte, primera fecha y tres atributos para filtros futuros |
| customers.parquet | Todos los compradores observados antes del corte, incluso sin compras recientes |
| histories.parquet | Pares comprador/artículo de los últimos 28 días, última compra y frecuencia |
| popularity.parquet | Catálogo completo ordenado por frecuencia en siete días, incluyendo frecuencia cero |
| rules.json | Regla H congelada: historial, popularidad, desempates y deduplicación |
| validation-reference.json | Referencias Q4/Q1/diagnóstico con sus huellas y el corte original de validación |
| manifest.json | Schema2, identidad de contenido, commit, fuente, corte, inventario, filas, tamaños, SHA-256 y software |

Se conservan **todos** los artículos únicos de cada historial y el orden completo
de popularidad; limitar antes de aplicar filtros cambiaría el resultado futuro.
El núcleo `rank_history` filtra elegibilidad, deduplica conservando la primera
aparición y devuelve hasta 200 artículos. Historial: última fecha descendente,
frecuencia descendente e ID ascendente. Popularidad: frecuencia descendente e ID
ascendente. Compras previas siguen siendo elegibles; un usuario sin historial
elegible recibe popularidad.

El constructor sólo lee eventos con fecha estrictamente anterior al corte y
no recibe etiquetas de validación. Para incluir compradores antiguos sin
historial reciente se añade `customers.parquet` al inventario previsto.
No usa la lista de compradores de validación para seleccionar perfiles.

La salida se construye en un directorio temporal hermano, se verifica y se renombra
atómicamente. Se rechazan schema1, archivos extra, enlaces, rutas no canónicas,
filas o hashes incorrectos, IDs duplicados, orden incorrecto y fechas fuera de
ventana. Un destino existente sólo se reutiliza si identidad e integridad coinciden;
un destino parcial o de otra versión bloquea. Los temporales fallidos se conservan
para diagnóstico. Un paquete completo con paridad fallida sigue siendo un candidato
local sin aprobación; su existencia nunca activa un servicio.

La identidad incluye el commit y las versiones de software: otro commit puede
producir otra identidad aunque el ranking sea idéntico. Los archivos son relativos,
sin rutas del autor. El núcleo de ranking no importa Torch. La imagen de jobs
todavía instala las dependencias experimentales del lock; no se afirma que sea
una imagen de servicio mínima.

## Paridad y referencias

El job limita el corte a 2020-09-09. Primero construye el paquete; después utiliza
las etiquetas V3 hasta 2020-09-16 exclusivo para verificar:

1. Catálogo y población histórica completos contra el snapshot Q1.
2. Top200 exacto para todos los compradores V3 contra el Parquet privado Q1-H.
3. Métricas, segmentos, denominadores, exposición y objetivos repetición/descubrimiento
   iguales a Q1, con tolerancia absoluta 1e-10 y relativa cero.
4. Paridad con los wrappers del diagnóstico histórico; en H&M también con las
   métricas del ZIP del diagnóstico.

H&M requiere los **72,019 compradores de validación y 102,967 artículos** del
reporte revisado. El paquete incluye además todos los compradores anteriores al
corte. Los tres ZIP congelados están en `configs/releases/history-v1.json`:

- Selección Q4: `quality-mac24-20261021T042757Z-915df40a`.
- Paridad Q1-V3: `quality-mac24-20261020T201805Z-63eb4569`.
- Diagnóstico histórico: `diagnose-mac24-20261020T184112Z-4b76ee72`.

El worker exige las tablas y manifiestos originales de Q1 en
`artifacts/data-jobs/quality-mac24-20261020T201805Z-63eb4569/work/`.
Se validan sus huellas y relaciones con el ZIP cerrado. **Los ZIP no contienen
predicciones ni sustituyen esos archivos privados.** Una ausencia bloquea; no
regenerar Q1 ni cambiar la referencia para continuar.

La demo crea una referencia sintética con las mismas reglas, 33 compradores de
validación, 32 compradores históricos y 24 artículos disponibles. No ejecuta la
campaña Q1–Q4 ni modifica su ledger. La selección Q4 figura como procedencia de
la regla, con `selection_archive_verified=false` en el paquete sintético.
En H&M ese campo exige el ZIP Q4 real y es verdadero. Ningún paquete hereda
métricas para otra fecha: la referencia conserva su corte original y declara
`metrics_measured_for_this_package=false`; la medición de paridad pertenece al
reporte separado.

## Preparación en mac24

Actualizar main por fast-forward, preservar cualquier cambio y fijar el SHA completo
recibido. No cambiar código, rama o recursos entre inicio y cierre del job.
Conservar originales T2/T3/Q1–Q4, ZIP, checkpoints y ledger.
Usar Colima `hm-project` con 14 GiB/4 CPU y recursos `mac24`
de 10 GiB/3 CPU/2 hilos. El job requiere 20 GiB libres al iniciar y se detiene
si quedan menos de 5 GiB o supera 7,200 s; este límite es por intento T4-A y
no toca el presupuesto cerrado Q4. No ejecutar dos jobs pesados a la vez.

Desde la raíz del clon, con su propio `.env` ya preparado por T1:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project docker compose --env-file .env --file compose.yaml build jobs
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite releases
```

`verify` usa la imagen existente; **reconstruirla primero**. La suite ejecuta
unitarias, paquetes/paridad, regresión de H y pruebas host. Una instalación nueva
debe seguir primero `docs/second-mac.md`; no copiar entornos ni credenciales.

Descargar únicamente los ZIP congelados que falten en `artifacts/handoff` o
`artifacts/incoming`, usando `scripts/github-handoff.py download RUN_ID`.
Cada Mac conserva sus datos de Kaggle y genera sus paquetes.

## Ejecutar y devolver evidencia

Sustituir `SHA_COMPLETO` por el commit recibido, no por un valor abreviado.
Primero la demo; esperar cierre, exportar, validar y publicar antes de full:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py release-job start --dataset synthetic --data-as-of 2020-09-09 --expected-commit SHA_COMPLETO --machine-id mac24 --profile hm-project --resources mac24
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py release-job status --run-id RUN_ID_DEMO
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py release-job export --run-id RUN_ID_DEMO
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA_DEL_ZIP
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA_DEL_ZIP
```

Sólo si demo pasó, iniciar full una vez:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py release-job start --dataset hm --data-as-of 2020-09-09 --expected-commit SHA_COMPLETO --machine-id mac24 --profile hm-project --resources mac24
```

Repetir status/export/lectura/upload con el run_id full. No es necesario mantener
la terminal abierta después de `started`, pero sí mantener mac24 despierta.
No exportar un job abierto. El estado `passed` exige proceso terminado, exit 0,
sin OOM, pruebas, paquete válido y todas las comprobaciones de paridad.

Ante fallo, conservar run_id, temporales, salida y ZIP. Publicar también el reporte
fallido válido (el lector devuelve 1); un ZIP inválido devuelve 2 y bloquea el envío.
No reintentar automáticamente ni modificar reglas, recursos o código para ocultarlo.
Un fallo detiene la secuencia. No hay `resume` de T4-A: un reintento autorizado
creará otra identidad de job y conservará el anterior.

Sólo se exportan agregados, huellas, recursos y logs acotados. Los siete archivos
del paquete, tablas de paridad, IDs, transacciones y modelos **no viajan por
GitHub**. El transporte de paquetes privados completos será T7 con contrato propio.

Al finalizar ambas corridas, guardar `evidence/mac24-release-t4a.json` con estado,
commit, run_id, identidad/hash/tamaño del paquete, compradores históricos, filas,
compradores V3, catálogo, paridad, métricas, tiempo, disco, RSS/cgroup/swap/OOM,
límites e URLs de Releases. Registrar preservación de originales por separado
como comprobación local de mac24. Actualizar documentos y hacer commit/push sólo
después del cierre; no marcar aceptación final ni T4-B implementada.

## Interfaz de biblioteca y CLI de desarrollo

`build_history_release(dataset, data_as_of, output_dir, evidence_archive=None)`
requiere `HMREC_SOURCE_COMMIT` con SHA completo; el job lo toma del clon limpio.
`verify_history_release(directory)` verifica el paquete sin activarlo.
Dentro de jobs existen `hmrec release build --dataset RUTA --data-as-of FECHA
--output DESTINO [--evidence-archive ZIP_Q4]` y `hmrec release verify DIRECTORIO`.
Estos comandos de desarrollo no crean evidencia de ejecución oficial ni evalúan
calidad. La biblioteca permite otros cortes explícitos para futuros usos; el
job T4-A mantiene la restricción V3.

## Verificación local de esta entrega

175 pruebas distintas aprobadas: 93 científicas, 74 host y ocho de infraestructura
Docker. Una prueba externa MLflow se omitió en la suite científica general; no es
una prueba de servicio de H. La suite releases también pasó desde un clon limpio
con configuración propia. La demo desacoplada `release-dev-20261021T045736Z-ccdf867f`
completó paridad exacta de 33 compradores, con 32 compradores históricos y 24
artículos disponibles; duró 5.019 s, RSS máximo 0.338 GiB y pico cgroup 0.292 GiB,
sin swap/OOM. Su paquete local ocupa 9,324 bytes. Estas cifras son sintéticas.

La revisión independiente no encontró bloqueos técnicos. Se corrigió un aviso
histórico contradictorio de la guía; queda diferido añadir códigos de fase/error
controlados. Hoy un fallo interno se reporta por clase de excepción para evitar
filtrar filas privadas y puede requerir inspección adicional en mac24.

## Resultados mac24 (2026-10-21)

Ejecución T4-A completada en `d59e14775f1e6a51ea5725d4ba9d0d9a61310236`.
Mac24 declara imagen jobs reconstruida y suite releases aprobada: 60 unitarias,
cuatro de integración y 74 host; un aviso de invariantes sparse de PyTorch.
La [evidencia agregada](../evidence/mac24-release-t4a.json) conserva métricas,
identidad/hashes, recursos efectivos y URLs verificadas de ambos reportes.

- Synthetic: `release-mac24-20261021T190539Z-02a850f8`, passed, 5.012 s,
  paquete de 9,324 bytes, 32 compradores históricos y 33 de validación.
- H&M: `release-mac24-20261021T190717Z-f0a2d073`, passed, 29.048 s,
  paquete de 59,229,801 bytes, 1,351,314 compradores históricos, 949,748
  pares de historial, 102,967 artículos y 72,019 compradores V3.

Ambos verificaron top200 exacto y métricas iguales (atol 1e-10, rtol 0).
H&M reprodujo MAP@12 0.02458901804435885, NDCG@12 0.03549285064696758 y
Recall@200 0.1907855419943058. Pico RSS 2,028,105,728 bytes y cgroup
2,413,895,680 bytes, sin swap ni OOM. Estos tiempos son offline, no latencia
de servicio. Límites efectivos: 10 GiB/3 CPU/2 hilos sobre Colima 14 GiB/4 CPU.

La [revisión desde la Mac principal](../evidence/releases-review-mac24.json)
validó ambos ZIP y los tres reportes fuente congelados, identidad del código y
configuración, límites, aislamiento, conteos y huellas de ranking/métricas contra
Q1-V3. Las métricas también coinciden con el diagnóstico histórico. Los seis
archivos de contenido de la demo tienen las mismas huellas que la demo local.
Cada ZIP incluye el preflight de 16 pruebas aprobadas con un aviso conocido de
pytest (`cache_dir`); esos logs no son la suite independiente completa.

Mac24 declara 3,049 archivos originales sin cambios, incluido el ledger. La suite
independiente y esa preservación no se comprobaron físicamente aquí: no se
transfirieron su inventario, paquete H&M ni tablas privadas. La paridad completa
fue calculada por el worker en mac24; aquí se contrastaron sus agregados y huellas
con los reportes anteriores. **T4-A queda cerrada operativamente** y se conservan:
`quality_accepted=false`, `test_evaluated=false`, `activated=false`.
La [revisión T4-B](../evidence/serving-review-mac24.json) ya verifica carga H&M,
HTTP interno, activación y rollback en mac24, pero la publicación hacia localhost
falló. Sigue [corregir y comprobar el acceso desde el host](serving.md). La duración
offline y el tamaño del paquete no acreditan latencia de servicio ni carga T8.
Publicar evidencia no activa al otro agente.
