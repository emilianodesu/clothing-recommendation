# Continuación de recuperación UI sin llavero personal

El intento `ui-recovery-22fc4a3dcc96` conserva **failed**. El reporte publicado y la traza entregada por el operador identifican un fallo al iniciar BuildKit: Docker intentó obtener credenciales de macOS Keychain desde SSH. No empezó la construcción histórica ni el probe. Los seis auxiliares se recuperaron y los servicios originales quedaron preservados según ese reporte. No ejecutar de nuevo el modo original ni borrar su estado.

La continuación `--public-registry-followup` mantiene el [mantenimiento autorizado](mac24-ui-maintenance-plan.md): pausa únicamente los mismos seis auxiliares, constructor de 2 GiB/1 CPU, probe de 512 MiB/0.5 CPU y recuperación obligatoria. API/PostgreSQL/UI originales permanecen activos. Se prepara una imagen histórica recuperable; la actualización de UI se revisará después.

## Cambio y comprobaciones

1. Verifica el SHA-256 exacto del reporte publicado, su correspondencia con el estado privado y la firma del fallo de llavero. Rechaza antecedentes con build/probe iniciados, recuperación incompleta o logs de construcción inesperados. Compara con el inventario original: sólo los seis auxiliares pueden tener nuevas generaciones tras su recuperación; sus IDs, imágenes y límites deben coincidir. Un constructor previo sólo puede permanecer detenido.
2. Crea `.runtime/ui-recovery-public-v1/` una vez, con configuración Docker privada, contexto dirigido al mismo socket Colima y comprobación del ID del motor. No copia `~/.docker/config.json`, credenciales ni contextos personales. Enlaza únicamente el ejecutable Buildx ya instalado y comprueba su versión.
3. Usa una entrada anónima de Docker Hub sin contraseña, token, `credsStore` ni `credHelpers`. El mapa `auths` no debe quedar vacío: Docker puede detectar automáticamente `osxkeychain` cuando no hay entradas. El entorno de estos subprocesos elimina las variables Docker/Buildx/BuildKit heredadas que podrían cambiar destino o autenticación. Conserva la validación TLS.
4. **Antes de pausar servicios**, consulta los manifiestos públicos de BuildKit y de las dos imágenes base fijadas en el Dockerfile histórico; descarga BuildKit por digest. Una denegación, límite de descargas o error de acceso en esta fase cierra el intento fallido sin parar/arrancar servicios. Verifica de nuevo inventario, espacio y trabajos inactivos antes del mantenimiento.
5. Durante la ventana, todas las llamadas Buildx usan esa configuración aislada. Comprueba que el constructor usa la imagen previamente descargada. Conserva los controles históricos de recursos, probe aislado, salud, identidad, recuperación y ausencia de despliegue.

El digest de BuildKit queda fijado en `scripts/ui_recovery_public.py` y registrado en el reporte. No se atribuye esa imagen al intento anterior, que falló antes de arrancarla. Los Dockerfiles y dependencias de la aplicación histórica no cambian.

## Ejecución por SSH

Usar el SHA completo entregado, `main` limpio y actualizado, AC y tapa abierta. Dentro de la sesión SSH de mac24 y del repositorio:

```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export SSL_CERT_FILE=/etc/ssl/cert.pem
python3 scripts/mac24_ui_recovery.py \
  --public-registry-followup --expected-commit SHA_COMPLETO --publish
```

Mantener la terminal abierta. La nueva carpeta conserva logs y reserva; el intento previo permanece intacto. Sólo `evidence/ui-recovery-public-mac24.json` se publica en el repositorio privado, incluso ante fallo si se confirmó la recuperación/preservación. El resumen incluye la referencia al intento anterior, huella de su estado, imágenes públicas, fase y si empezó el mantenimiento. No contiene configuraciones personales ni datos de clientes.

Ante fallo, conservar todo y revisar la etapa: no repetir la construcción. Si el reporte indica recuperación pendiente por interrupción, el modo acotado de recuperación es:

```bash
python3 scripts/mac24_ui_recovery.py --public-registry-followup \
  --restore-only --expected-commit SHA_DEL_SEGUIMIENTO --publish
```

No ejecutarlo si los auxiliares ya se recuperaron. Si falla sólo el push, publicar el commit conservado no requiere repetir la preparación.

## Evidencia y alcance

La [revisión del fallo](../evidence/ui-recovery-keychain-review-mac24.json) distingue el reporte remoto y la traza recibida del trabajo local. La [validación local](../evidence/ui-recovery-public-local.json) registra pruebas host y consultas reales de los tres manifiestos públicos. Con Docker local, un helper ficticio que falla reprodujo la consulta al llavero con `auths` vacío; la configuración nueva consultó los tres manifiestos sin invocarlo. Esa prueba no inicia contenedores ni acredita la construcción o recuperación en mac24. Buildx local y remoto tienen versiones distintas; la continuación comprueba su versión efectiva y puede fallar antes de pausar si la configuración no es compatible.

Fundamento técnico: [carga de configuración Docker](https://github.com/docker/cli/blob/master/cli/config/config.go), [selección de almacén de credenciales](https://github.com/docker/cli/blob/master/cli/config/configfile/file.go) y [límites del driver docker-container](https://docs.docker.com/build/builders/drivers/docker-container/). No se desbloquea el llavero ni se cambia la configuración personal para esta preparación.
