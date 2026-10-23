# T7 en mac24: una ejecución, después de T6 auditada

Lee AGENTS.md, docs/reproducibility.md y evidence/pipeline-review-mac24.json. Conserva H, PostgreSQL/API/UI/Airflow, datos, streams, contadores, paquetes y ledger. No repetir T5/T6, entrenar ni abrir test. Conecta AC, abre la tapa y mantén Terminal abierta.

1. Con `SSL_CERT_FILE=/etc/ssl/cert.pem`, `DOCKER_CONTEXT=colima-hm-project` y main limpio, fija el SHA completo recibido. No cambiar de código durante el ensayo. VM14GiB/4CPU; no ampliar presupuestos.
2. Ejecuta `python3 scripts/mac24_t7.py --expected-commit SHA_COMPLETO`. Construye sólo jobs/app y ejecuta suite portability. No recrea el servicio original. Preserva inventario privado y verifica identidades. Reserva un único intento.
3. El lanzador respalda H y PostgreSQL, restaura en `.runtime/T7 restored installation` con credenciales y volúmenes nuevos, puerto localhost18024. Compara seis consultas y comprueba operación sin red externa en el destino; conserva el original activo. No inicia Airflow del destino.
4. Espera reporte cerrado; se publica por GitHub privado y se registra `evidence/portability-mac24.json`. Un `failed` válido también se conserva/publica. El respaldo grande **no se sube**. No repetir ante fallo: devolver la etapa/error y conservar logs privados, ZIP y destino. Una falla de preparación puede no producir ZIP; conservar sus logs.
5. `passed` requiere revisión principal: no cerrar T7/E6 ni iniciar T8. No afirmar cambio de usuario/arquitectura si la prueba sólo usa nuevas credenciales y ruta en la misma Mac.

El informe cerrado contiene huellas y agregados; no compartir logs privados por chat. Para recibirlo en la Mac principal se usa `scripts/github-handoff.py download RUN_ID` y el lector existente.
