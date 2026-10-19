# Iniciar la segunda Mac y devolver resultados

Esta guía permite que otra Mac Apple Silicon de 24 GB reproduzca la infraestructura y devuelva evidencia a la Mac de desarrollo. **El objetivo actual es T1; todavía no hay un comando de entrenamiento.** No hace falta copiar H&M para esta comprobación.

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
python3 scripts/start-second-mac.py --machine-id mac24
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

Tras aceptar el reporte de T1, el siguiente entregable es T2: importación por lotes, validación del ZIP y Parquet. Después vendrá T3: entrenamiento PyTorch CPU con checkpoints y métricas exportables. Cada entrega incluirá un commit, comando y resultados esperados antes de pedir su ejecución en la otra Mac. El usuario confirmó que H&M se descargará directamente de Kaggle en la segunda Mac con sus propias credenciales, sin enviarlo por GitHub. El [diseño entre equipos](superpowers/specs/2026-10-19-entrenamiento-dos-macs-design.md) describe la comprobación del dataset y los contratos futuros; esos comandos de entrenamiento aún no existen.

## Verificación disponible

El 19 de octubre de 2026 se ejecutó el instalador completo en la Mac de desarrollo, reutilizando su perfil Colima `default`: las siete etapas y las 26 pruebas pasaron; el lector verificó el ZIP con salida 0. La [evidencia sanitizada](../evidence/second-mac-script-2026-10-19.json) registra el commit y los resultados. Esto comprueba el recorrido local y el empaquetado; la instalación desde cero en la Mac 24 GB continúa pendiente hasta recibir su reporte.

Después se añadió el transporte GitHub: la suite completa pasó con **35 pruebas**. Se publicó ese ZIP de instalación, se listó, descargó y leyó mediante los comandos nuevos; su SHA-256 coincide con el original y con GitHub. Un segundo envío reutilizó el mismo adjunto. La [evidencia del transporte](../evidence/github-handoff-2026-10-19.json) registra estas operaciones; el ZIP de prueba sigue correspondiendo a la ejecución anterior de infraestructura.

## Referencias del transporte

- [GitHub: Releases, permisos y adjuntos](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
- [GitHub CLI: creación y publicación de Releases](https://cli.github.com/manual/gh_release_create).
- [GitHub CLI: descarga de adjuntos](https://cli.github.com/manual/gh_release_download).
