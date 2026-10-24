# Despliegue en servidor: guía del agente receptor

**Objetivo:** instalar una copia de H para operadores del equipo, con dominio, HTTPS y cuentas individuales. El servidor pertenece al equipo, pero todavía no se conocen SO, arquitectura, RAM ni disco. Esta guía permite descubrirlos. La preparación del código no acredita todavía instalación Linux, emisión del certificado público ni acceso desde Internet.

Leer primero [traspaso](handoff.md), [hallazgos](findings.md) y [AGENTS](../AGENTS.md). No ejecutar runners `mac24_*`, entrenamientos, Q1–Q4, T8 ni los prompts antiguos. H no requiere entrenamiento. Las verificaciones siguientes son funcionales del nuevo despliegue.

## 1. Descubrir el servidor

Como usuario normal con acceso autorizado a Docker, clonar el repositorio público y **fijar el commit entregado**. No ejecutar comandos con valores de ejemplo sin sustituirlos. `SOURCE` será el checkout; `DEST` será una ruta nueva y distinta para el servicio.

```sh
git clone https://github.com/emilianodesu/clothing-recommendation.git
cd clothing-recommendation
git checkout --detach COMMIT_COMPLETO_ENTREGADO
python3 scripts/server.py doctor
uname -a
cat /etc/os-release
df -h .
free -h
ss -ltn
ip -j -4 route show table all
```

`doctor` no cambia el host. Si Docker falta, imprime el diagnóstico; no interpreta ausencia como capacidad cero disponible. Preparar **Linux de 64 bits, ARM64 o AMD64**, Python host ≥3.10, Git, `iproute2`, Docker Engine, plugin Compose y Buildx. El Python científico 3.12 está dentro de las imágenes. Usar la [instalación oficial correspondiente al SO](https://docs.docker.com/engine/install/) y registrar versiones; no instalar Colima en Linux, ni copiar sockets o configuración de otra computadora. Si el servidor es Windows/macOS, usar una VM Linux dedicada o diseñar una adaptación antes de continuar.

La pertenencia al grupo Docker da acceso administrativo al motor: usar la cuenta de despliegue autorizada. No crear usuarios del SO ni conceder privilegios automáticamente. No ejecutar este flujo como root. Mantener verificación TLS; en Linux no exportar una ruta CA de macOS que no exista.

### Recursos por fase

| Fase | Admisión del script | Observación |
| --- | --- | --- |
| Construcción inicial | Motor local sin contenedores activos, ≥4 CPU, ≥9 GiB Docker y ≥40 GiB libres | Constructor acotado a 6 GiB/3 CPU, sin swap adicional; 8 GiB de presupuesto + 1 margen |
| Restauración inicial | Motor sin contenedores activos, ≥4 CPU, ≥8 GiB Docker y ≥40 GiB libres | Jobs 5 GiB; PostgreSQL y API arrancan en fases; el restaurador añade control de 3×ZIP + 1 GiB |
| Servicio | API 4 GiB, PostgreSQL 512 MiB, UI 512 MiB, proxy 256 MiB | 5.25 GiB de cuotas; no representan memoria observada ni usuarios ilimitados |
| Backup con servicio activo | Límites activos + job 5 GiB + margen 1 GiB | Con el servicio mínimo, ≥11.25 GiB Docker; no detener otros servicios implícitamente |

**Recomendación de partida: servidor dedicado de 16 GiB, 4 CPU y 100 GiB de disco.** Es presupuesto de ingeniería, no una capacidad medida en Linux. Si se conserva el ZIP completo para imágenes, añadir ~31 GB y espacio de descarga/verificación; el control de 40 GiB no lo reserva automáticamente. Si comparte el motor con otras aplicaciones, no pararlas para superar la admisión: acordar un motor/VM separado. No basta mirar RAM libre; se suman cuotas de contenedores y se rechazan cargas sin límite. También se comprueba el disco del checkout/destino y `DockerRootDir` por separado. Si ese directorio no puede consultarse con la cuenta elegida, resolver el acceso de diagnóstico con el administrador; no suponer que comparte disco con el checkout. El padre de `DEST` debe existir.

Los puertos 80/443 deben estar disponibles para Caddy; 8000/8501 quedan en localhost. Si existe otro proxy, conservarlo y revisar una integración explícita antes de publicar. La restauración usa subredes libres en `10.253.0.0/16`; el script rechaza una ruta del host/VPN que se solape. No cambiar redes ajenas para forzar el despliegue.

## 2. Construir desde fuente y locks

En checkout limpio y motor dedicado, sin servicios activos:

```sh
python3 scripts/server.py build
```

Genera `.env` con secretos propios y permisos 0600, crea el secreto CA de build, construye secuencialmente jobs/app con BuildKit acotado, comprueba su código y precarga PostgreSQL/Caddy. Conserva logs en `.runtime/server-build/` y recibo en `.runtime/server-build.json`. No inicia PostgreSQL, API, UI ni proxy. Al terminar elimina sólo su builder temporal conservando su estado de caché. Si falla, leer esos logs; no borrar datos ni imágenes de otros proyectos.

Caddy está fijado por digest multiarch; BuildKit e imágenes base también. `uv.lock` fija paquetes Python. Esto permite reconstrucción controlada, pero **no promete imágenes bit a bit idénticas**: repositorios apt, disponibilidad de wheels y plataforma pueden influir. Se registran los IDs efectivos del destino. No relajar versiones automáticamente si un registro falla; documentar el bloqueo y revisar el cambio.

## 3. Obtener el respaldo privado

El ZIP de un Release `handoff-*` contiene reportes, **no** catálogo/perfiles para servir. Se necesita un archivo producido por `hmrec portability backup` o `scripts/backup.py`, protocolo `hm-app-backup-v1`, y su SHA-256 recibido de la fuente autorizada.

Existe un respaldo T7 probado de **918,506,419 bytes**, SHA-256:

```text
b40ed253fee3c5913a0d94ca9549d5db5ed3683065530d5fdb85c7c5cd022f50
```

La instalación fuente lo conserva en `artifacts/backups/backup-9a298451dcbc45fabd8ef0cb03fd566c.zip`. Pedir al responsable que confirme existencia/huella y autorice su copia privada. Sirve como instantánea histórica; no incorpora eventos posteriores. No ejecutar otra vez T7 ni restaurar/pausar mac24 para obtenerlo. Si se desea un backup más reciente, planificarlo con el responsable y revisar recursos/escrituras antes: la preparación de este servidor no reabre las ventanas de mantenimiento de mac24.

El equipo puede copiarlo por SSH/SFTP privado o almacenamiento privado autorizado; **nunca un Release/commit público**. Ejemplo desde el destino, con alias/ruta entregados por el responsable:

```sh
mkdir -m 700 -p "$HOME/hm-private"
scp FUENTE_AUTORIZADA:/ruta/privada/backup.zip "$HOME/hm-private/backup.zip"
chmod 600 "$HOME/hm-private/backup.zip"
sha256sum "$HOME/hm-private/backup.zip"
```

El archivo contiene datos individuales y no incluye cifrado propio. Conservarlo en almacenamiento privado y no enviarlo al agente por el chat ni imprimir tablas. Si no se dispone de él, el despliegue real queda pendiente de esa entrada; descargar Kaggle por sí solo no recupera los streams/eventos. La alternativa es reconstruir H con los comandos de [paquetes](releases.md) y [eventos](events.md), bajo un procedimiento nuevo con recursos y procedencia definidos, sin repetir evaluación final. No fingir que el reporte agregado sustituye los datos.

## 4. Restaurar a una instalación nueva

Definir rutas reales en la sesión:

```sh
SOURCE="$HOME/clothing-recommendation"
DEST="$HOME/hm-service"
BACKUP="$HOME/hm-private/backup.zip"
BACKUP_SHA="SHA256_CONFIRMADO_POR_LA_FUENTE"
python3 "$SOURCE/scripts/server.py" --root "$SOURCE" restore "$BACKUP" "$DEST" --sha256 "$BACKUP_SHA" --port 8000
```

`DEST` debe ser absoluto y no existir. Se valida checksum, imágenes, espacio y redes; se copian sólo fuentes inventariadas, se generan credenciales propias y se restauran tablas/paquetes en volúmenes nuevos. Arrancan PostgreSQL/API y se comparan seis consultas. `.runtime/server-images.json` fija los IDs efectivos para todas las futuras operaciones Compose de esa instalación. La copia de servicio tiene `source-inventory.json`, no `.git`; el checkout de construcción se conserva aparte. No ejecutar `git pull` dentro de ella.

Ante fallo conservar destino, respaldo, volúmenes y logs. No volver a lanzar restore sobre el destino parcial ni borrar el bloqueo para intentarlo: inspeccionar la causa y diseñar otra instalación con nombre/ruta nuevos. No se modifica la fuente/mac24.

```sh
python3 "$DEST/scripts/server.py" start-ui
python3 "$DEST/scripts/server.py" check
```

Esto comprueba readiness, fuentes, imágenes efectivas, salud, bindings privados y presencia de streams. Sólo se inicia UI; Airflow/MLflow/Qdrant quedan fuera. Los DAG no se reactivan automáticamente. Se conserva el corte servido del respaldo; no activar el paquete del test.

### Fotografías opcionales

La API busca el ZIP en `data/kaggle_hm/h-and-m-personalized-fashion-recommendations.zip`. Sin él muestra **Imagen no disponible**, pero catálogo, eventos y recomendaciones funcionan. Para fotografías, usar credenciales Kaggle propias, aceptar términos y descargar/verificar según [configuración y dataset](bootstrap.md). La ruta genérica disponible desde `DEST` es:

```sh
python3 scripts/project.py download-data --check-access
python3 scripts/project.py download-data
python3 scripts/project.py validate-data --mode full
```

Ejecutar en `DEST`; antes del último comando admitir el job de 5 GiB junto a los servicios, y revisar espacio para ~31 GB adicionales. No se necesita extraer las fotografías ni reentrenar. No versionar token ni ZIP. No copiar un token de otro integrante.

## 5. Dominio y acceso del equipo

Usar un subdominio exclusivo, por ejemplo `recomendaciones.sudominio.mx` (sustituir por el real). Configurar A y, sólo si funciona IPv6, AAAA hacia el servidor. Abrir TCP 80 y 443 en el proveedor/router/host; SSH sólo para administración autorizada. Revisar CGNAT/NAT: si no hay entrada pública o reenvío posible, resolverlo antes del certificado. No abrir 5432, 8000, 8501, 8080, 5000 ni 6333 al exterior. Los puertos Docker pueden eludir reglas UFW; seguir las [indicaciones de Docker sobre firewall](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

Crear una cuenta web por integrante; no es otra cuenta del SO ni cuenta de proveedor:

```sh
python3 "$DEST/scripts/server.py" user --domain recomendaciones.sudominio.mx --name ana
python3 "$DEST/scripts/server.py" user --domain recomendaciones.sudominio.mx --name emiliano
python3 "$DEST/scripts/server.py" publish
python3 "$DEST/scripts/server.py" check-public --user ana
```

La contraseña se solicita de forma oculta, mínimo 16 caracteres y máximo 72 bytes. Se guarda sólo bcrypt en archivos privados 0600. `publish` valida configuración y crea/recarga únicamente el gateway; no recrea API/PostgreSQL/UI. Caddy obtiene y renueva TLS automáticamente. Sus volúmenes `/data` y `/config` persisten; no borrarlos para reintentar emisión. Propagación DNS, conectividad y límites ACME pueden demorar la primera publicación; conservar logs y corregir la causa, sin bucles de recreación.

La autenticación HTTP Basic muestra el diálogo de usuario/contraseña del navegador. No es un formulario propio, SSO, MFA ni RBAC. Todo el equipo tiene permisos de operador, incluido registrar eventos y refrescar simulaciones. El proxy protege todas las rutas y WebSocket y elimina Authorization antes de enviar a Streamlit. La API administrativa y PostgreSQL nunca se publican. No atribuir acciones de la base de datos a una cuenta web: ese registro individual no está implementado.

## 6. Aceptación del destino

`check-public` exige TLS válido, 401 sin credenciales y con credencial incorrecta, UI/salud 200 con credenciales, WebSocket protegido y redirección HTTP→HTTPS. No deshabilita la validación del certificado ni sigue redirecciones que podrían reenviar la contraseña.

Desde **otra red/equipo**, abrir HTTPS en ventana privada: verificar que primero exige credenciales, renderiza interfaz/catálogo, conserva WebSocket activo y carga recomendaciones. Registrar una interacción de simulación sólo si se desea demostrar escritura; conservar su UUID y datos en privado. Comprobar que 8000/8501/5432 no son accesibles externamente. El script no sustituye esta prueba de navegador y red externa.

Guardar `.runtime/server/local-check.json`, `public-check.json`, `server-origin.json`, IDs de imágenes, versiones, diagnóstico y resultado de navegador. Publicar únicamente un resumen sin secretos/clientes según [operación](server-operations.md). El despliegue se considera terminado después de estas comprobaciones, no sólo porque Docker marque healthy. Los resultados de latencia mac24 no son un SLO de esta nueva web.
