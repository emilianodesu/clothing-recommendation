# Preparación local de Docker

> Estado actual: para el nuevo servidor Linux con HTTPS usar [server-deployment.md](server-deployment.md). Las referencias a futuros T5–T8 o test reservado en esta guía describen etapas históricas ya superadas; no reabren campañas. El repositorio puede ser público, los datos y respaldos no.

Esta guía prepara el motor de contenedores y comprueba su funcionamiento. Después, la [primera entrega del proyecto](bootstrap.md) permite construir sus imágenes y preparar PostgreSQL y Qdrant. El modelo personalizado y su API siguen pendientes. La configuración de `docker/smoke/` es exclusivamente una prueba del motor Docker.

## macOS

Requisitos del host: Homebrew, Python 3.9 o posterior, Ruby con YAML, Bash y curl. Se utiliza Ruby incluido en macOS para leer los montajes existentes sin perder su configuración. Si falta, instalarlo con `brew install ruby`. No se utiliza el Python de Codex ni el entorno temporal de validación. Si falta Homebrew, instalarlo desde [brew.sh](https://brew.sh); si falta Python, instalarlo con `brew install python`.

Desde la raíz del proyecto:

```bash
python3 scripts/setup-docker-macos.py
export DOCKER_CONTEXT=colima
python3 scripts/doctor.py
bash scripts/docker-smoke.sh
```

El preparador instala con Homebrew los paquetes que falten: Docker CLI, Colima, Compose y Buildx. Conserva los paquetes ya instalados. Si Docker no encuentra un plugin, crea un enlace en el directorio `cli-plugins` del usuario sin modificar las credenciales de Docker. Este mecanismo sigue el [directorio de plugins por usuario de Docker](https://docs.docker.com/compose/install/linux/); Homebrew también documenta la alternativa de [configurar un directorio adicional](https://formulae.brew.sh/formula/docker-compose).

La configuración inicial es 4 CPU, 8 GiB de RAM y disco virtual de hasta 100 GiB. El disco crece según uso; el límite configurado no equivale a una reserva física inmediata de 100 GiB. El espacio disponible dentro de Docker y el espacio del host se comprueban por separado. Los originales de H&M permanecerán en el directorio de datos del proyecto, fuera del disco de contenedores.

El preparador asegura que la carpeta donde está este proyecto sea visible y escribible dentro de Colima. Conserva los montajes existentes y agrega el proyecto si hace falta; un perfil nuevo comparte sólo el proyecto. Si Colima ya tiene los recursos y el montaje necesarios, no lo reinicia. Si necesita cambiarlos y hay contenedores activos o Kubernetes habilitado, se detiene con un mensaje. Antes de modificar un perfil existente conserva una copia privada de `colima.yaml`. No reduce discos ni elimina contenedores o volúmenes. Se conserva el contexto global seleccionado; el `export` anterior selecciona Colima sólo para la terminal actual.

En un equipo compartido puede usarse un perfil separado:

```bash
python3 scripts/setup-docker-macos.py --profile hm-project --cpus 4 --memory 8 --disk 100
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/doctor.py
bash scripts/docker-smoke.sh
```

Un perfil adicional consume recursos adicionales. El preparador deja al menos 4 GiB para el host al validar la memoria solicitada; corresponde al usuario considerar también otras VM y aplicaciones en ejecución. Si el perfil tiene otros servicios internos, como Kubernetes, preparar uno separado para este proyecto.

## Linux

Usar Docker Engine con los plugins Compose y Buildx de los repositorios oficiales de la distribución compatible. En Ubuntu, seguir la [instalación mediante el repositorio APT de Docker](https://docs.docker.com/engine/install/ubuntu/). Después de configurar ese repositorio, los paquetes son:

```bash
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin python3 curl
sudo systemctl start docker
```

En una instalación nueva, configurar el acceso del usuario siguiendo la [guía posterior a la instalación](https://docs.docker.com/engine/install/linux-postinstall/). Pertenecer al grupo `docker` otorga control administrativo del host; también puede usarse la instalación rootless oficial. No se necesita Docker Desktop ni Colima en Linux.

Cuando `docker info` funcione con el usuario elegido, desde la raíz del proyecto:

```bash
python3 scripts/doctor.py
bash scripts/docker-smoke.sh
```

No copiar configuraciones, sockets ni credenciales de otra persona. En Linux las cuotas y el almacenamiento del motor deben adaptarse a la máquina; la preparación automática de Colima sólo se aplica a macOS. Esta guía Linux aún no se ha ejecutado en un segundo equipo.

## Qué se comprueba

`doctor.py` sólo consulta el entorno. Informa versiones de cliente, motor, Compose y Buildx, arquitectura, CPU y memoria del motor, espacio libre del proyecto y, cuando se usa Colima, espacio del disco de Docker y visibilidad de los archivos del proyecto. Devuelve código 1 si no puede consultar un componente requerido y muestra avisos si los recursos están por debajo de la referencia. No comprueba capacidad de entrenamiento ni instala nada.

`docker-smoke.sh` construye una imagen mínima y arranca un proyecto Compose con nombre único. Verifica:

1. Construcción de imagen y arranque con comprobación de salud.
2. Publicación HTTP en un puerto aleatorio de `127.0.0.1` y acceso desde el host.
3. DNS y conexión HTTP entre contenedores.
4. Lectura de un archivo del proyecto montado como sólo lectura.
5. Persistencia de un archivo en un volumen al recrear el contenedor.

La prueba retira sus propios contenedores, red, volumen e imagen al terminar, incluso ante un error normal. No ejecuta `prune` ni elimina recursos de otros proyectos. Conserva la imagen base en caché. Utiliza un digest de imagen fijo y un contexto de construcción pequeño que no contiene el dataset ni credenciales. Requiere un motor local accesible por socket Unix, Bash y curl.

Para guardar el diagnóstico sin rutas personales ni credenciales:

```bash
python3 scripts/doctor.py --json > docker-diagnostic.json
```

La evidencia de esta preparación se conserva en `evidence/docker-preparation-2026-10-18.json`. El sistema completo seguirá necesitando sus propias pruebas.

## Arranque y parada habituales

En macOS con el perfil por defecto:

```bash
colima start
export DOCKER_CONTEXT=colima
docker info
```

Los recursos configurados se conservan entre arranques. Al terminar el trabajo se puede ejecutar `colima stop`; esto detiene los contenedores de ese perfil. No se configuró arranque automático del sistema ni se instalaron servicios de pago.

## Versiones y reproducción

El equipo de preparación usa Colima 0.10.3, Docker CLI 29.6.1, Docker Engine 29.5.2, Compose 5.6.0 y Buildx 0.35.0, sobre ARM64. Son versiones comprobadas, no una garantía de funcionamiento del stack futuro.

Homebrew y APT pueden ofrecer otras versiones al repetir la instalación. El preparador no fuerza versiones antiguas del motor; se registra el resultado y se exige que pase la misma prueba. La imagen de prueba sí está fijada por digest. Las dependencias e imágenes de la aplicación se bloquearán cuando se implemente. No se considera probado Linux x86_64 por haber ejecutado esta prueba en ARM64.

## Resolución de problemas

| Síntoma | Comprobación y acción |
| --- | --- |
| `docker compose` no existe | Ejecutar el preparador en macOS o instalar `docker-compose-plugin` en Linux; comprobar `docker compose version` |
| Conexión fallida a `/var/run/docker.sock` en macOS | Arrancar Colima y seleccionar `export DOCKER_CONTEXT=colima` |
| El preparador encuentra contenedores activos | Detener conscientemente esos trabajos o usar un perfil separado |
| Plugin existente que no funciona | Revisar el enlace indicado por el error; el preparador no sobrescribe plugins ajenos |
| Error al descargar la imagen | Revisar conexión y acceso al registro; volver a ejecutar la prueba tras resolverlo |
| Aviso de disco | Comprobar host y disco de Colima; ampliar capacidad antes de procesar H&M completo |
| `DOCKER_HOST` o contexto remoto | Elegir un contexto local y quitar `DOCKER_HOST` de esa terminal antes de la prueba |

La asignación de recursos de Colima está documentada en su [guía oficial](https://github.com/abiosoft/colima#customizing-the-vm).

## Restaurar sin el entorno del autor

La [guía T7](reproducibility.md) permite restaurar tablas y paquetes H en una ruta nueva, con credenciales propias e imágenes precargadas. No requiere Kaggle ni MLflow original. Sigue sus verificaciones antes de declarar utilizable el destino.
