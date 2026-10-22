# Mac24: reproducir T5 una sola vez

**Procedimiento histórico, no repetir.** El intento `events-mac24-20261022T043907Z-ddcd2e42` terminó fallido y ya fue [auditado](../evidence/events-review-mac24.json). T5 sigue abierta por el criterio de memoria PostgreSQL. Antes de otra corrida se preparará instrumentación y un nuevo prompt; estas instrucciones conservan el procedimiento original.

Lee `AGENTS.md`, `docs/events.md` y `evidence/serving-host-review-mac24.json`. T4-A/T4-B y Q1–Q4 están cerradas. La tarea es comprobar eventos, perfiles e interfaz con H&M, devolver evidencia y detenerse. No entrenes, no evalúes test, no repitas campañas, no inicies T6 ni cambies H.

1. Conserva modificaciones existentes. Actualiza `main` con fast-forward y verifica clon limpio. Registra el SHA completo recibido como `T5_COMMIT`; no cambies código durante la ejecución. Usa `SSL_CERT_FILE=/etc/ssl/cert.pem` en todos los comandos de host, contexto `colima-hm-project`, Colima 14 GiB/4 CPU. No cambies credenciales ni copies `.env`.
2. Verifica que no hay trabajos pesados. Conserva inventario/huellas privados de datos/features/checkpoints, Q1–Q4, paquetes T4-A, reportes y ledger. La versión full activa debe ser `h-d12e30cb4d8c44cc22468e86c634db566e6f126ddc704e36723fe09d9a8a7b0a`, con fuente `release-mac24-20261021T190717Z-f0a2d073`. Necesitas su ZIP cerrado en `artifacts/handoff` o `artifacts/incoming`, su paquete local bajo `artifacts/data-jobs/<run>/work/release` y T2 bajo `data/processed/hm-v1`. Si falta algo, informa; no reconstruyas privados desde ZIP.
3. Reconstruye y arranca con `SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py up --profile serve`. Aplica la migración 003 y añade UI preservando PostgreSQL, versiones y volúmenes. Verifica `SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite events`. Conserva log local y conteos reales de pruebas. Si falla, detente y devuelve el diagnóstico; no ajustes código para esconderlo.
4. Ejecuta **una sola vez**, manteniendo terminal y equipo despiertos:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py events-check \
  --machine-id mac24 --expected-commit "$T5_COMMIT" \
  --source-run-id release-mac24-20261021T190717Z-f0a2d073
```

El ensayo crea un entorno `simulation` independiente, siembra sólo compras anteriores a 2020-09-09, prueba cuatro interacciones nuevas y sus reintentos, actualiza perfiles y reinicia API. Registra límites efectivos y pico/swap/OOM de API/PostgreSQL/UI/semilla. Mantiene el paquete y el endpoint base inmutables. No ejecutes `demo` en el clon H&M: es para un clon sintético separado.

5. Espera salida final y conserva el ZIP anunciado aunque el ensayo falle. Valida con `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip`; salida 1 significa reporte válido de fallo, salida 2 paquete inválido. Publica un reporte válido con `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip`. No reintentes automáticamente, no subas clientes/tablas/modelos ni el dataset. Un corte sin ZIP es evidencia incompleta, no éxito.
6. Compara el inventario privado original; guarda esos listados localmente y publica sólo conteos y huellas agregadas. Actualiza `evidence/events-mac24.json` y una nota en `docs/events.md` con estado real, commit, run_id, fuente, checks, tiempo, recursos, pruebas ejecutadas, preservación declarada y URL. No declares T5 cerrada: requiere revisión en la Mac principal. Publica código/documentación por el Git privado autorizado al terminar, sin alterar fuentes históricas.
7. Devuelve estado, ZIP/Release y enlace UI `http://127.0.0.1:8501`. Los servicios pueden quedarse disponibles; no elimines volúmenes. Termina aquí.
