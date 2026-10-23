# T7: suite sintética desde otra cuenta de macOS

Complementa la recuperación H&M ya auditada (`portability-mac24-20261023T204855Z-579f1842`). No repite ese ensayo, no importa el respaldo H&M, no descarga Kaggle ni inicia T8. Verifica la suite portability desde un UID/home propios, ruta con espacios, credenciales nuevas y motor vacío independiente. El cierre requiere revisión conjunta de ambas evidencias.

## 1. Cuenta y requisitos

En mac24, desde la cuenta administradora, crear una cuenta **Estándar**, por ejemplo `hmtest`, en Ajustes del Sistema → Usuarios y grupos → Añadir usuario. No usar Invitado ni habilitar administración. [Guía oficial de Apple](https://support.apple.com/guide/mac-help/mchl3e281fc9/mac).

Cambiar a esa cuenta mediante cambio de usuario, manteniendo abierta la sesión de emilianodesu para conservar sus procesos. No reiniciar el equipo ni cerrar/detener Colima original. Mantener alimentación AC y tapa abierta. No hace falta habilitar SSH para la cuenta nueva: usar Terminal local de mac24.

Usar las herramientas Homebrew ya instaladas y legibles en `/opt/homebrew/bin`: Python, Git, gh, Colima, Docker, Compose, Buildx y Ruby. No cambiar permisos/propietario de Homebrew ni instalar herramientas con sudo desde la cuenta nueva. Si falta una herramienta o un permiso, detenerse y comunicar el requisito.

Se crea sólo el perfil propio `hm-portability-user`: **6 GiB/3 CPU**, disco máximo60GiB. Junto con los14GiB del perfil original, los límites suman20GiB en mac24; cerrar aplicaciones personales de alto consumo. No ejecutar otros trabajos pesados. El presupuesto no garantiza ausencia de presión del host; si aparece, conservar diagnóstico sin ampliar límites ni detener el original. Sólo se inician PostgreSQL512MiB y jobs5GiB de forma secuencial; no UI, API, Airflow, MLflow o Qdrant. Tener al menos25GiB libres para imágenes/construcción. No limpiar recursos históricos para obtener espacio.

## 2. Autenticación propia y clon

Desde Terminal de la **cuenta nueva**:

```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export SSL_CERT_FILE=/etc/ssl/cert.pem

gh auth login -h github.com -p https --web
gh auth setup-git
mkdir -p "$HOME/Documents"
gh repo clone emilianodesu/clothing-recommendation "$HOME/Documents/T7 User Check"
cd "$HOME/Documents/T7 User Check"
git checkout --detach SHA_COMPLETO_RECIBIDO
```

La autenticación GitHub es nueva en esta cuenta; no copiar tokens, llaveros, `.env`, `.docker` ni `.colima` de emilianodesu. El nombre de la cuenta macOS no requiere crear una cuenta GitHub distinta. Se necesita acceso al repositorio privado y permiso para publicar el reporte.

## 3. Preparar sólo el motor de esta cuenta

Comprobar herramientas existentes antes de usar el preparador (éste podría intentar instalar faltantes):

```bash
brew list --versions docker colima docker-compose docker-buildx
python3 --version
git --version
ruby --version
```

Si todas están disponibles:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/setup-docker-macos.py \
  --profile hm-portability-user --cpus 3 --memory 6 --disk 60
export DOCKER_CONTEXT=colima-hm-portability-user
```

No ejecutar bootstrap/demo/start-second-mac ni iniciar otro contenedor: el lanzador exige motor sin contenedores ni volúmenes y ausencia de `.env`. La preparación Colima no necesita el socket del usuario original; si algo solicita acceso al home original, detenerse. No copiar claves ni configurar sockets compartidos.

## 4. Un ensayo y publicación

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/os_user_check.py \
  --expected-commit SHA_COMPLETO_RECIBIDO
```

El lanzador valida usuario real distinto, home/propiedad, socket local propio (incluidos enlaces), motor ARM64 vacío6GiB/3CPU y clon limpio. Reserva el intento, genera configuración propia, construye jobs, inicia PostgreSQL y ejecuta `verify --suite portability --existing-services`. Esta suite incluye pruebas unitarias, integración de export/import/restore sintético y host. No representa otro ensayo H&M ni una prueba de carga.

Los logs detallados quedan en `.runtime/`; el ZIP cerrado contiene estados agregados y metadatos, con prefijo `bootstrap-mac24-os-user-` y formato de instalación ya soportado por github-handoff. El nombre bootstrap corresponde al sobre de reporte; el alcance T7 está en environment.json y la etapa verify-portability. Se publica automáticamente por GitHub privado. No sube datos privados ni secretos. Conservar URL/run_id y devolverlos a la Mac principal.

Ante fallo, no repetir, borrar `.env`/reserva, recrear el perfil o limpiar volúmenes. Conservar reporte y logs. Si sólo falla publicación, subir únicamente el ZIP cerrado con `python3 scripts/github-handoff.py upload RUTA.zip`. Los fallos anteriores a la reserva pueden no producir ZIP. No cerrar T7/E6 ni ejecutar T8 automáticamente.

Después de recibir y revisar evidencia se podrá detener **sólo el perfil nuevo**, conservando volúmenes. No usar el comando de parada del perfil original.
