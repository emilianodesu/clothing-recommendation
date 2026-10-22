# Iniciar la segunda Mac y devolver resultados

**Paso vigente:** corregir y probar localmente el acceso HTTP del host en [T4-B](serving.md), antes de preparar una nueva ejecución mac24. El [ensayo recibido](../evidence/serving-review-mac24.json) pasó por HTTP interno, pero localhost no responde. Su [prompt](mac24-t4b-prompt.md) es histórico; no repetirlo. [T4-A](releases.md) sigue cerrada. Conservar paquetes y resultados Q1–Q4/T4-A/T4-B.

Esta guía conserva la instalación y las ejecuciones históricas entre las dos Macs. **T1–T3, Q1–Q4 y T4-A están cerradas y revisadas; H fue seleccionado y su paquete conserva la paridad.** No repetir instalación, descarga, entrenamientos ni campañas anteriores. La API, activación y rollback de T4-B están implementados y probados localmente; `serving-check` verifica el ciclo de versiones con los paquetes existentes y devuelve reportes cerrados.

## 1. Obtener el código

Se necesitan Git, Homebrew, Python 3.9+ nativo ARM64 y Ruby con YAML, además de GitHub CLI (`gh`) y una cuenta con permiso de escritura en el repositorio privado. Consulta [la preparación de Docker](setup.md) si falta un requisito. El script instala Docker CLI, Colima, Compose y Buildx cuando faltan; no instala Homebrew ni autentica GitHub por ti. La primera instalación necesita Internet para descargar imágenes y dependencias. No necesita una cuenta de nube ni Kaggle.

Prepara GitHub CLI una vez en esta Mac (si `gh` ya está instalado y autenticado con la cuenta correcta, conserva esa configuración):

```bash
brew install gh
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git --hostname github.com
```

Completa el acceso en el navegador con `emilianodesu` o un colaborador autorizado. No pegues tokens en el repositorio ni transfieras los de la otra Mac. Clona en una carpeta local:

```bash
git clone --branch main https://github.com/emilianodesu/clothing-recommendation.git hm-recommender
cd hm-recommender
git rev-parse HEAD
```

Para un clon existente, conserva primero cualquier cambio propio y actualiza a la revisión acordada. Si recibiste un SHA completo, comprueba que esté disponible con `git fetch origin` y selecciónalo con `git switch --detach SHA_COMPLETO`. No actualices el clon durante una ejecución. El script exige un árbol Git limpio y registra el commit al principio y al final.

El agente del segundo equipo debe leer [AGENTS.md](../AGENTS.md). Puedes darle esta instrucción:

> Lee AGENTS.md y docs/second-mac.md. Ejecuta la comprobación inicial de la segunda Mac con alias mac24. Conserva, verifica y publica el paquete de resultados en GitHub aunque la instalación falle. Devuélveme estado, commit, run_id y URL de la Release. Esta tarea sólo comprueba T1.

## 2. Ejecutar un único script

Desde la raíz del clon:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/start-second-mac.py --machine-id mac24
```

Si recibiste una revisión concreta, añade `--expected-commit SHA_COMPLETO`. El script no cambia de revisión ni ejecuta `git pull` por su cuenta. No necesita activación de un entorno Python: sus dependencias del host son la biblioteca estándar.

Prepara un perfil Colima separado llamado `hm-project`, con **4 CPU, 8 GiB de RAM y disco virtual de hasta 100 GiB**. Es la referencia inicial para comparar instalaciones; el presupuesto ampliado de entrenamiento se implementará después. Un perfil adicional consume recursos adicionales: evita mantener otras VM innecesarias. El preparador conserva los recursos mayores de un perfil existente y no lo reinicia si necesita cambios y tiene contenedores activos.

El script selecciona el contexto sólo para sus procesos, descarta variables heredadas que podrían dirigir Compose a otro proyecto y exige Docker local sobre Linux ARM64. No modifica el contexto global elegido por el usuario. Ejecuta en orden:

1. Preparación de Docker/Colima.
2. Diagnóstico inicial de arquitectura, recursos y montajes.
3. Prueba aislada de construcción, HTTP, DNS y persistencia de Docker.
4. Bootstrap: credenciales locales, imágenes, PostgreSQL, Qdrant y migraciones.
5. Comprobación de importaciones y conexiones.
6. Suite de pruebas unitarias, de intercambio y de reinstalación en otra ruta.
7. Diagnóstico final y comprobación de que el código no cambió.

Mantén la terminal abierta y la Mac despierta, conectada a corriente. Es un instalador en primer plano, no un planificador de trabajos. Muestra la etapa actual y el archivo donde escribe su log; se puede consultar ese archivo mientras trabaja. Los logs grandes conservan los primeros y últimos 2 MiB. Un aviso de capacidad se registra y debe revisarse antes de procesar datos; no certifica que el entrenamiento completo quepa.

Si ya existe un perfil preparado, se puede usar `--profile NOMBRE --skip-docker-setup`; esta opción no instala ni arranca el motor. En la Mac de desarrollo, con su perfil original, la comprobación equivalente es:

```bash
python3 scripts/start-second-mac.py --machine-id author-check --profile default --skip-docker-setup
```

## 3. Conservar el resultado

Al finalizar aparece `Estado: passed`, `failed` o `interrupted` y la ruta exacta del paquete:

```text
artifacts/handoff/bootstrap-mac24-FECHA-ID.zip
```

Cada intento tiene un nombre único. El script conserva también la carpeta de trabajo, no sobrescribe paquetes anteriores y empaqueta el resultado ante fallos normales. Salida 0 significa comprobaciones satisfactorias; otro código requiere revisar el reporte. Ante interrupción normal intenta producir un paquete con estado `interrupted`. Un corte de energía, cierre forzado o disco lleno puede impedirlo: conserva la carpeta parcial y vuelve a ejecutar cuando el problema esté resuelto. Sólo un ZIP final validado cuenta como paquete terminado.

Contenido del ZIP:

| Archivo | Qué permite revisar |
| --- | --- |
| `manifest.json` | Commit, alias, estado, etapas, códigos de salida, tiempos y SHA-256 de archivos |
| `summary.md` | Resumen legible |
| `environment.json` | SO, arquitectura, Python, perfil y recursos solicitados |
| `commit.txt` | Revisión Git, cuando pudo obtenerse |
| Logs por etapa | Diagnóstico, recursos efectivos y resultados de pruebas |
| `preflight.log`, si aplica | Motivo de rechazo o fallo previo/final |

El paquete incluye únicamente los archivos previstos. No adjunta `.env`, tokens, dataset ni modelos. Los logs pueden incluir rutas locales; el intercambio sigue siendo privado. Las huellas detectan cambios/corrupción en el contenido y no son una firma de identidad del equipo.

El agente puede verificarlo allí con:

```bash
python3 scripts/read-handoff.py artifacts/handoff/NOMBRE-EXACTO.zip
```

Si falla una etapa, las posteriores no se ejecutan. Devuelve el ZIP fallido; no borres volúmenes, credenciales ni datos para forzar una salida exitosa. Después de resolver la causa, repetir el script crea otro intento y reutiliza la instalación de forma idempotente.

## 4. Comunicar el resultado a la Mac de desarrollo

Toda la comunicación entre Macs usa el repositorio privado de GitHub:

```text
Mac de desarrollo → push de código, documentación y commit acordado
Segunda Mac       → clona/actualiza, ejecuta y publica su ZIP en GitHub Releases
Mac de desarrollo → lista, descarga y valida el reporte; publica la siguiente entrega
```

Los ZIP son **adjuntos de Releases**, no archivos versionados con `git add`. El entrenamiento continúa siendo local: no se usan runners de GitHub Actions. La publicación no activa al otro agente y no hay seguimiento en vivo. Para revisar resultados basta con pedir al agente de esta Mac que consulte GitHub; no hay que mover archivos ni abrir puertos entre equipos.

En la segunda Mac, después de que el instalador muestre la ruta final:

```bash
python3 scripts/github-handoff.py upload artifacts/handoff/NOMBRE-EXACTO.zip
```

Usa la ruta exacta del intento, sin comodines. El comando valida el ZIP y exige que el repositorio sea privado y la cuenta tenga escritura. Crea una Release marcada como prerelease, con nombre `handoff-RUN_ID`, vinculada al commit del reporte; no la marca como última versión de la aplicación. Adjunta únicamente el ZIP de diagnóstico (límite de este protocolo: 50 MiB), lo descarga para comparar su SHA-256 y devuelve la URL. Un reporte fallido también se puede publicar. Si falla la conexión, conserva el ZIP y repite el mismo comando: reutiliza contenido idéntico y rechaza sobrescribir contenido distinto. Una publicación incompleta recuperable permanece como borrador hasta terminar.

Si el fallo ocurrió antes de obtener un commit, la Release usa la rama predeterminada como referencia técnica, pero el manifiesto conserva `source_commit: null`; no se atribuye una revisión comprobada a ese intento. Para publicar un reporte de un commit propio, ese commit debe estar previamente disponible en el remoto.

Si GitHub dejó un adjunto incompleto en un borrador, el comando puede rechazarlo para evitar sobrescribir contenido: conserva el ZIP original y devuelve el error al agente de desarrollo. No se borran adjuntos automáticamente.

En la Mac de desarrollo:

```bash
python3 scripts/github-handoff.py list
python3 scripts/github-handoff.py download RUN_ID
```

El primer comando muestra las ejecuciones disponibles y sus URL. Copia el `run_id` completo del resultado al segundo comando. La descarga se guarda bajo `artifacts/incoming/RUN_ID.zip`, verifica el inventario y las huellas antes de publicar el archivo local, y muestra el resumen. Un archivo local diferente con ese nombre no se sobrescribe. No uses un reporte `draft: true`: el emisor debe reintentar la publicación.

Puedes volver a leer el paquete sin conexión:

```bash
python3 scripts/read-handoff.py artifacts/incoming/RUN_ID.zip
```

`github-handoff.py` devuelve **0 si el transporte fue correcto y 1 si falló**. Un transporte exitoso puede contener una instalación fallida: revisa siempre el estado del resumen. El lector local distingue **0: instalación reportada como satisfactoria; 1: reporte válido de fallo/interrupción; 2: paquete inválido o inaccesible**. Ninguno extrae ni ejecuta archivos del paquete.

El destino predeterminado es `emilianodesu/clothing-recommendation`. Para usar otro repositorio privado autorizado, pasa `--repo PROPIETARIO/REPOSITORIO` antes de `upload`, `download` o `list`. No depende del directorio personal, de un token incluido en el código ni de un contexto GitHub implícito. El repositorio elegido debe seguir siendo privado.

Después de publicar, puedes escribir aquí «Revisa los reportes de mac24 en GitHub» o dar el `run_id`. El agente de aquí podrá descargarlos y analizarlos. Los reportes contienen logs privados, potencialmente con rutas locales; sus adjuntos sólo deben permanecer en el repositorio privado. La verificación de huellas detecta corrupción, pero no sustituye la confianza en la cuenta que publicó el reporte.

Formato breve que debe devolver el agente de la segunda Mac:

```text
Estado de instalación: passed / failed / interrupted
Publicación: completada / fallida
Commit: SHA completo, o no disponible
Run ID: identificador de manifest.json
Primera etapa fallida: nombre, o ninguna
Avisos: los que indique el diagnóstico
Release: URL confirmada, o diagnóstico del fallo de publicación
```

## 5. Parar y continuar después

Al terminar quedan PostgreSQL y Qdrant disponibles. Para detener sólo los contenedores de este proyecto, conservando los volúmenes:

```bash
DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py down
```

Para detener la VM del perfil, una vez terminados sus trabajos:

```bash
colima --profile hm-project stop
```

La siguiente ejecución normal del instalador arranca el perfil y reutiliza los volúmenes. Si cambiaste el nombre del perfil, usa ese nombre en estos comandos.

T1 ya está aceptada y T2 está implementada: importación por lotes, validación del ZIP, Parquet y features temporales. El procedimiento T2 aparece en la sección siguiente. Después vendrá T3: entrenamiento PyTorch CPU con checkpoints y métricas exportables. Cada entrega incluirá un commit, comando y resultados esperados antes de pedir su ejecución en la otra Mac. El usuario confirmó que H&M se descargará directamente de Kaggle en la segunda Mac con sus propias credenciales, sin enviarlo por GitHub. El [diseño entre equipos](superpowers/specs/2026-10-19-entrenamiento-dos-macs-design.md) describe la comprobación del dataset y los contratos futuros; esos comandos de entrenamiento aún no existen.

## Verificación disponible

El 19 de octubre de 2026 se ejecutó el instalador completo en la Mac de desarrollo, reutilizando su perfil Colima `default`: las siete etapas y las 26 pruebas pasaron; el lector verificó el ZIP con salida 0. La [evidencia sanitizada](../evidence/second-mac-script-2026-10-19.json) registra el commit y los resultados. Esto comprueba el recorrido local y el empaquetado; la prueba física posterior de mac24 ya pasó y está registrada en [la revisión](../evidence/mac24-bootstrap-review-2026-10-19.json).

Después se añadió el transporte GitHub: la suite completa pasó con **35 pruebas**. Se publicó ese ZIP de instalación, se listó, descargó y leyó mediante los comandos nuevos; su SHA-256 coincide con el original y con GitHub. Un segundo envío reutilizó el mismo adjunto. La [evidencia del transporte](../evidence/github-handoff-2026-10-19.json) registra estas operaciones; el ZIP de prueba sigue correspondiendo a la ejecución anterior de infraestructura.

## Referencias del transporte

- [GitHub: Releases, permisos y adjuntos](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
- [GitHub CLI: creación y publicación de Releases](https://cli.github.com/manual/gh_release_create).
- [GitHub CLI: descarga de adjuntos](https://cli.github.com/manual/gh_release_download).

## T2: preparar los datos en mac24

La instalación física pasó el 19 de octubre: `bootstrap-mac24-20261019T171240Z-52229e97`, commit `0df375fea67b41eb51d7e0581e8e7978f8873183`, 35 pruebas. El primer intento falló por certificados; se resolvió usando `SSL_CERT_FILE=/etc/ssl/cert.pem`. Mantén ese prefijo. El aviso de puerto 5000 ocupado se resolverá al desplegar MLflow en T3; no bloquea el procesamiento T2.

1. Conserva tus cambios, actualiza el clon desde GitHub y selecciona el SHA completo recibido. Comprueba `git status --short` y `git rev-parse HEAD`. Para aprovechar mac24, sigue [la preparación de recursos](data.md#salidas-y-recursos): Colima `hm-project` con 14 GiB/4 CPU y `--resources mac24` con 10 GiB/3 CPU/2 hilos. Verifica que no haya trabajos activos antes de redimensionar. El presupuesto `base` de 5 GiB sigue disponible para equipos menores.
2. Lee [docs/data.md](data.md), configura tu propia credencial Kaggle y comprueba acceso. Ejecuta `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py download-data`. El descargador continúa un temporal incompleto y valida el ZIP antes de publicarlo. Conserva al menos 20 GiB libres adicionales para T2.
3. Ejecuta primero el trabajo sintético, que no necesita Kaggle:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job start --machine-id mac24 --profile hm-project --resources mac24 --dataset synthetic --expected-commit SHA_COMPLETO
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job status --run-id RUN_ID_SINTETICO
```

4. Si termina `passed`, exporta y publica ese reporte. Inicia después el trabajo completo:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job start --machine-id mac24 --profile hm-project --resources mac24 --dataset hm --expected-commit SHA_COMPLETO
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job status --run-id RUN_ID_REAL
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py data-job export --run-id RUN_ID_REAL
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py artifacts/handoff/RUN_ID_REAL.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload artifacts/handoff/RUN_ID_REAL.zip
```

Sustituye los marcadores por el SHA y los run_id reales. Publica también los reportes fallidos: la salida 1 del lector significa que el paquete es válido pero la ejecución falló. No publiques datos, ZIP H&M, `.env` ni tokens. Si se corta la descarga, repite `download-data`; si se interrumpe el procesamiento, consulta estado/exporta el diagnóstico antes de `data-job resume --run-id RUN_ID`.

Instrucción para el agente de mac24:

> Lee AGENTS.md, docs/second-mac.md y docs/data.md. Actualiza de forma segura y fija el commit indicado. Usa SSL_CERT_FILE=/etc/ssl/cert.pem en los intentos. Prepara Colima hm-project con 14 GiB/4 CPU sin interrumpir trabajos ni borrar volúmenes. Los perfiles ya están implementados: ejecuta T2 sintético y después H&M completo con --resources mac24 (10 GiB/3 CPU/2 hilos), descargando el dataset directamente de Kaggle. Espera resultados, valida y publica ambos reportes mediante github-handoff, incluso ante fallos. Devuelve commit, run_id, URL, recursos solicitados/efectivos, conteos, duración, memoria y errores si los hay. No inicies entrenamiento T3 ni cambies límites para ocultar fallos.

En esta Mac recibimos los resultados con los comandos existentes `github-handoff.py list`, `download RUN_ID` y `read-handoff.py`. El nuevo tipo `data-handoff` viaja en una Release privada `handoff-data-...`, con el mismo control de integridad y límite de 50 MiB. GitHub es el único canal entre equipos; la subida no activa automáticamente al otro agente.


### Resultado T2 recibido de mac24

Las corridas sintética `data-mac24-20261019T182008Z-b0c98090` y completa `data-mac24-20261020T000332Z-c95306b5` terminaron `passed` con el commit `8e62bfdc55024428a08c7f16d58d718560116509`. Ver [resultados y Releases](data.md#resultado-físico-en-mac24--19-de-octubre-de-2026) y [evidencia agregada](../evidence/mac24-t2-2026-10-19.json).

En la máquina principal, después de actualizar este repositorio, descargar y validar los paquetes:

```bash
python3 scripts/github-handoff.py download data-mac24-20261019T182008Z-b0c98090
python3 scripts/github-handoff.py download data-mac24-20261020T000332Z-c95306b5
```

Estos comandos reciben sólo los reportes. H&M y las salidas procesadas permanecen en mac24. La publicación no inicia automáticamente al agente de la máquina principal ni autoriza entrenamiento T3.

## T3: entrenar y devolver evidencia desde mac24

Lee `docs/evaluation.md`. T2 ya está validada; reutiliza `data/processed/hm-v1` y `artifacts/features/hm-v1`. No descargues ni reproceses H&M salvo que falten o fallen sus verificaciones. Fija el commit recibido en un clon limpio y conserva cambios ajenos.

1. Comprueba Colima `hm-project` con 14 GiB/4 CPU, directorios montados y al menos 10 GiB libres adicionales. No ejecutes dos trabajos pesados. Usa los perfiles existentes: VM mediante `--profile`, presupuesto mediante `--resources mac24` (10 GiB/3 CPU/2 hilos).
2. Mantén la Mac encendida, conectada a corriente y sin suspensión; puede usarse `caffeinate -i` en otra terminal mientras duren los trabajos. Cerrar Codex no detiene el contenedor, pero suspender macOS sí detiene el cómputo.
3. Prepara tracking y verifica T3 con el contexto correcto:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py tracking-up --port 5001
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite model
```

Si 5001 está ocupado, elige otro puerto libre explícito y repite `tracking-up --port PUERTO`; no interrumpas al dueño del puerto. El servicio queda en `http://127.0.0.1:5001` si elegiste 5001.

4. Ejecuta **uno por uno**, sustituyendo `SHA_COMPLETO` por el commit recibido:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job start --machine-id mac24 --profile hm-project --resources mac24 --config demo --expected-commit SHA_COMPLETO
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job status --run-id RUN_ID
```

Cuando la demo pase, repite start con `--config hm-small`, luego `hm-medium`, y finalmente `hm-full`. Conserva cada run_id. Espera estado final y exporta cada intento **antes de iniciar el siguiente**. Si un intento falla o se interrumpe, exporta diagnóstico y no avances a un tamaño mayor. No cambies código/hiperparámetros/recursos para ocultar un fallo. Los pilotos y full son experimentos nuevos, no `resume` del tamaño anterior.

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job export --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

Un reporte válido fallido devuelve 1 en el lector; aun así se publica. ZIP inválido devuelve 2 y no debe publicarse. Publicar cada reporte en el repositorio privado está autorizado.

Para interrupciones recuperables:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job stop --run-id RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/project.py train-job resume --run-id RUN_ID
```

Resume devuelve **otro run_id** de intento con el mismo experiment_id. Usa siempre el intento más reciente. Recupera el checkpoint verificado con mismo commit/configuración/recursos. No reinicia una corrida ya aprobada ni bifurca un intento antiguo.

Devuelve por cada intento: configuración, estado, commit, run_id, experiment_id, attempt, filas, época, tiempo, RSS, pico cgroup/swap/OOM, MAP@12, NDCG@12, Recall@200, cobertura, popularidad, ID MLflow y URL de Release. Para full confirma 22,674,665 filas y una época terminada. No afirmes éxito de full por haber pasado los pilotos. No evalúes test ni implementes T4.

### Recibir aquí

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py list
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download RUN_ID
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py artifacts/incoming/RUN_ID.zip --json
```

La descarga valida inventario, SHA-256 y la identidad del paquete; repetirla es idempotente y rechaza contenido distinto. Los reportes se leen sin ejecutar nada ni importar pesos. La publicación no despierta automáticamente al agente de la otra Mac: avisa aquí cuando esté disponible.


### Resultado T3 de mac24 disponible para la máquina principal

Las cuatro corridas terminaron `passed` en el commit `6ba7e6e7972c655fb780cdade995a2bf8f54db36`. Full completó una época de 22,674,665 ejemplos; ver [métricas y limitaciones](evaluation.md#evidencia-física-de-mac24) y [evidencia agregada](../evidence/mac24-t3-2026-10-19.json). Cada run_id coincide con experiment_id, intento 1.

| Configuración | Reporte publicado y verificado |
| --- | --- |
| demo | [train-mac24-20261020T004703Z-a09df9b2](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-train-mac24-20261020T004703Z-a09df9b2) |
| hm-small | [train-mac24-20261020T004742Z-07e2f111](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-train-mac24-20261020T004742Z-07e2f111) |
| hm-medium | [train-mac24-20261020T004826Z-f8b7869f](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-train-mac24-20261020T004826Z-f8b7869f) |
| hm-full | [train-mac24-20261020T004913Z-9d2fb726](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-train-mac24-20261020T004913Z-9d2fb726) |

Para recibirlos, usa `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download RUN_ID` con cada identificador de la tabla. La descarga valida el paquete. Los IDs MLflow y las métricas/segmentos están en la evidencia; no es necesario acceder a MLflow de mac24 para leerlos. Revisar la cobertura baja antes de aceptar calidad o decidir nuevos experimentos. Esta publicación no inicia T4 ni ejecuta al agente de la máquina principal.

## T3: diagnóstico sobre el checkpoint ya entrenado

Demo, pilotos y full ya pasaron en mac24. Continúa con [el procedimiento de diagnóstico](diagnostics.md), primero sobre el origen demo y después sobre el full. `diagnose-job` fija origen/commit/recursos y produce reportes privados separados; no repitas entrenamiento ni abras test. La guía incluye comandos, interpretación de métricas y entrega al agente de desarrollo.


### Retorno del diagnóstico T3 de mac24

Los diagnósticos demo y full pasaron en `b4248cc80337dc524ce1d505b4c96248278a3119` y reprodujeron exactamente las métricas originales. Ver [resultados, controles y Releases](diagnostics.md#resultado-físico-en-mac24) y [evidencia agregada](../evidence/mac24-t3-diagnostics-2026-10-19.json). Para recibirlos en la máquina principal:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download diagnose-mac24-20261020T012621Z-0f44f776
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py download diagnose-mac24-20261020T012703Z-46890b00
```

La descarga valida los paquetes. La conclusión requiere revisión: full pierde frente a popularidad de 7 días y comparte muchas recomendaciones entre usuarios. `passed` acredita ejecución e integridad; `quality_accepted` sigue en false. No se inició T4 ni se repitieron entrenamientos. La publicación no activa automáticamente al agente principal.

## T3: siguiente experimento de tres épocas

Continúa con [el protocolo de tres épocas](three-epochs.md). Reutiliza datos y modelos originales; crea un candidato aparte y publica cuatro reportes cerrados. Esta secuencia reemplaza repetir los pilotos históricos. Todo el intercambio sigue por GitHub, sin SSH.
