# Ejecutar T5 desde Terminal mediante SSH

El usuario autorizó este canal para iniciar comandos desde Terminal. Codex todavía recibe `No route to host`; Terminal sí llega a mac24 y solicita contraseña. Este adaptador no configura claves ni permisos y no abre SSH por sí mismo. Código y evidencia siguen publicándose en GitHub privado; las tablas y credenciales permanecen en mac24.

Con mac24 conectada a corriente, tapa abierta y sin otro trabajo pesado, abre **Terminal de la Mac principal**. El comando entregado en el chat conecta con `ssh -t`, entra al repositorio y actualiza `main` por fast-forward al SHA indicado, sólo si está limpio. Ejecuta después:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project \
python3 scripts/mac24_terminal.py --expected-commit SHA_COMPLETO_RECIBIDO
```

El script exige main limpio, remoto privado esperado, Apple Silicon/24 GiB, alimentación AC, contexto Colima `hm-project` y capacidad compatible con 14 GiB/4 CPU. Comprueba servicios, versión H full activa, paquete/ZIP T4-A y la identidad histórica de PostgreSQL contra el ZIP de memoria auditado. Un reinicio o reemplazo previo bloquea el ensayo; no borra ni recrea servicios para corregirlo. Se detiene ante contenedores activos que no reconozca como servicios de infraestructura.

Guarda un inventario privado con SHA-256 de los originales en `data/processed`, `artifacts/data-jobs`, `artifacts/handoff` y `artifacts/quality-budget` (resolviendo DATA_DIR/ARTIFACTS_DIR desde el `.env` propio). No incluye el ZIP ni las imágenes crudas de Kaggle y no afirma haberlas auditado. Comparar originales permite archivos nuevos, pero rechaza cambios o eliminaciones. El inventario no viaja a GitHub; sólo su conteo/huella y resultado.

Reconstruye únicamente jobs, ejecuta la suite events con `--existing-services` (sin `up` sobre PostgreSQL) y comprueba otra vez el clon y las identidades de los tres servicios. Sólo entonces reserva atómicamente `artifacts/handoff/.mac24-operational-once.json` y lanza el protocolo vigente una vez. Mantiene el bloqueo de lanzamiento pesado del proyecto durante la secuencia. Los tres intentos mac24 históricos están permitidos; cualquier otro intento o reserva bloquea otra ejecución, incluidos directorios incompletos.

El runner mantiene los límites T5 y reinicia sólo API. El adaptador conserva los logs bajo `.runtime/mac24-terminal-*`. Mantén Terminal abierta: no es un servicio desacoplado ni promete sobrevivir a un corte SSH. Si hay interrupción o corte, conserva todo y revisa el estado antes de cualquier acción. **No borres la reserva para reintentar.**

Al terminar, el script valida el único ZIP nuevo y lo publica incluso si contiene un fallo válido. Guarda `evidence/events-operational-mac24.json`, anexa una nota a la guía, hace commit de esos dos archivos y publica main. No incluye logs, tablas ni credenciales. Ante conflicto de Git o fallo de red se detiene y conserva el ZIP y los cambios; no fuerza push ni vuelve a ejecutar el ensayo. Una discrepancia de preservación se registra separadamente y requiere investigación aunque el ZIP operativo sea válido.

Para recuperar **sólo el transporte** de un ZIP cerrado tras un fallo de publicación:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

Salida 1 del lector significa fallo válido; 2 significa ZIP inválido y no se publica. Si el ensayo ya terminó, nunca vuelvas a lanzar el adaptador para publicar: usa sólo el transporte y conserva cualquier evidencia/documentación pendiente de commit. Tras recibir la URL o el aviso de actualización, la Mac principal descarga y audita. T5/E4 siguen abiertas hasta esa revisión; no ejecutar entrenamiento, test ni T6.

Verificación de este adaptador: 165 pruebas host aprobadas en la Mac principal, incluida la regresión de `--existing-services` y errores de inspección Docker. Incluye pruebas de reserva persistente, intento previo incompleto, identidad de servicio, conservación de archivos y publicación de un fallo válido. No se ha ejecutado el adaptador sobre H&M desde la Mac principal.
