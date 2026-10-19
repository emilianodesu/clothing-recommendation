# Iniciar la segunda Mac y devolver resultados

Esta guía permite que otra Mac Apple Silicon de 24 GB reproduzca la infraestructura y devuelva evidencia a la Mac de desarrollo. **El objetivo actual es T1; todavía no hay un comando de entrenamiento.** No hace falta copiar H&M para esta comprobación.

## 1. Obtener el código

Se necesitan Git, Homebrew, Python 3.9+ nativo ARM64 y Ruby con YAML, además de acceso al repositorio privado. Consulta [la preparación de Docker](setup.md) si falta un requisito. El script instala Docker CLI, Colima, Compose y Buildx cuando faltan; no instala Homebrew ni autentica GitHub por ti. La primera instalación necesita Internet para descargar imágenes y dependencias. No necesita una cuenta de nube ni Kaggle.

Autentica Git con una cuenta autorizada en esta Mac y clona en una carpeta local, fuera de carpetas sincronizadas automáticamente:

```bash
git clone --branch main https://github.com/emilianodesu/clothing-recommendation.git hm-recommender
cd hm-recommender
git rev-parse HEAD
```

Para un clon existente, conserva primero cualquier cambio propio y actualiza a la revisión acordada. Si recibiste un SHA completo, comprueba que esté disponible con `git fetch origin` y selecciónalo con `git switch --detach SHA_COMPLETO`. No actualices el clon durante una ejecución. El script exige un árbol Git limpio y registra el commit al principio y al final.

El agente del segundo equipo debe leer [AGENTS.md](../AGENTS.md). Puedes darle esta instrucción:

> Lee AGENTS.md y docs/second-mac.md. Ejecuta la comprobación inicial de la segunda Mac con alias mac24. Conserva y verifica el paquete de resultados aunque falle. Devuélveme estado, commit, run_id y ruta del ZIP. Esta tarea sólo comprueba T1.

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

El circuito inicial es manual:

```text
Mac de desarrollo → Git privado: código, documentación y commit acordado
Segunda Mac       → clona/actualiza, ejecuta y cierra un ZIP de resultados
Usuario           → copia ese ZIP mediante AirDrop, disco o carpeta compartida
Mac de desarrollo → verifica el ZIP, analiza el resultado y publica el siguiente cambio
```

**Git no transporta los reportes ni permite consultar la otra Mac.** Tampoco hay comunicación automática entre los agentes o seguimiento en vivo. Una carpeta compartida sirve como transporte; el script trabaja en disco local y sólo se copia el ZIP terminado. No hay que abrir puertos de PostgreSQL, Qdrant ni otros servicios para leerlo aquí.

En la Mac de desarrollo, coloca el ZIP en `artifacts/incoming/` dentro del clon. Ese directorio ya está excluido de Git. Desde su raíz:

```bash
mkdir -p artifacts/incoming
python3 scripts/read-handoff.py artifacts/incoming/NOMBRE-EXACTO.zip
```

El lector no extrae ni ejecuta archivos: valida formato, inventario, límites y SHA-256, y muestra el resumen. Se puede añadir `--json` para leer el manifiesto. Sus códigos de salida son **0: instalación reportada como satisfactoria; 1: paquete válido con instalación fallida/interrumpida; 2: paquete inválido o inaccesible**. Un paquete válido con fallo sigue siendo útil para diagnosticar.

Después de copiar, envía en esta conversación: «Ya copié `artifacts/incoming/NOMBRE-EXACTO.zip`; revisa los resultados». El agente de aquí podrá abrirlo en su workspace. Para revisar un log concreto, léelo como texto dentro del ZIP, sin ejecutar su contenido. No subas el ZIP al repositorio ni publiques logs sin revisarlos.

Formato breve que debe devolver el agente de la segunda Mac:

```text
Estado: passed / failed / interrupted
Commit: SHA completo
Run ID: identificador de manifest.json
Primera etapa fallida: nombre, o ninguna
Avisos: los que indique el diagnóstico
Paquete: ruta absoluta al ZIP
Acción: copiarlo a artifacts/incoming/ en la Mac de desarrollo
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

Tras aceptar el reporte de T1, el siguiente entregable es T2: importación por lotes, validación del ZIP y Parquet. Después vendrá T3: entrenamiento PyTorch CPU con checkpoints y métricas exportables. Cada entrega incluirá un commit, comando y resultados esperados antes de pedir su ejecución en la otra Mac. El [diseño entre equipos](superpowers/specs/2026-10-19-entrenamiento-dos-macs-design.md) describe el traslado único del dataset y los contratos futuros; esos comandos de entrenamiento aún no existen.
