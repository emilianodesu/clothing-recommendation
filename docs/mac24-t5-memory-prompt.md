# Mac24: una corrida T5 con diagnóstico de memoria

**Procedimiento histórico, ejecutado y revisado. No repetir.** La corrida `events-mac24-20261022T184956Z-e167c9d2` conserva `failed`; consulta [la auditoría principal](../evidence/events-memory-review-mac24.json) y [los hallazgos de memoria y tiempo](events-memory.md#revisión-en-la-mac-principal-2026-10-22). No hay un nuevo ensayo pendiente. Las instrucciones siguientes preservan el procedimiento original, no autorizan otra ejecución.

Lee `AGENTS.md`, `docs/events-memory.md` y `evidence/events-review-mac24.json`. La tarea es obtener un reporte diagnóstico nuevo; el intento anterior conserva estado fallido. No repetir el prompt v1. No aumentar memoria, limpiar contadores, recrear PostgreSQL, borrar streams/volúmenes, entrenar, evaluar test ni iniciar T6. El usuario ya autorizó el intercambio privado de código y reportes por GitHub.

1. Conserva cambios existentes, actualiza `main` por fast-forward al SHA completo recibido y comprueba clon limpio. Fija `T5_MEMORY_COMMIT` a ese SHA. No actualices código durante la ejecución. Usa siempre `SSL_CERT_FILE=/etc/ssl/cert.pem` y `DOCKER_CONTEXT=colima-hm-project`; Colima permanece 14 GiB/4 CPU. No copies credenciales ni generes otro `.env`.
2. Verifica que no haya trabajos pesados. Conserva inventario/huellas privados de originales, datos/features/checkpoints, paquetes T4-A, Q1–Q4, ledger, reportes y el ZIP `events-mac24-20261022T043907Z-ddcd2e42`. Necesitas T2 en `data/processed/hm-v1`, paquete full `artifacts/data-jobs/release-mac24-20261021T190717Z-f0a2d073/work/release` y su ZIP cerrado. La versión activa debe ser `h-d12e30cb4d8c44cc22468e86c634db566e6f126ddc704e36723fe09d9a8a7b0a`. Los ZIP no reconstruyen privados faltantes.
3. La API/UI y PostgreSQL del intento anterior deben seguir ejecutándose. Registra en un archivo local sólo ID y hora de arranque de PostgreSQL para comprobar que no se recreó; no exportes `docker inspect` completo. Si se reinició o falta el servicio, informa con honestidad del cambio: no lo reinicies para ocultarlo ni continúes como si conservara el contador histórico. Esta entrega sólo cambia scripts de host/diagnóstico; no necesita sustituir el servicio activo.
4. Reconstruye **sólo jobs** desde el nuevo código. Desde la raíz:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from bootstrap import read_configuration
from project import ROOT, compose
compose(ROOT, read_configuration(ROOT / '.env'), 'build', 'jobs')
PY
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py verify --suite events
```

La suite usa bases temporales propias y comprueba publicación en un entorno aislado. Guarda sus logs localmente; comprueba de nuevo la identidad PostgreSQL. Si la suite falla o cambia esa identidad, detente y publica el diagnóstico agregado, sin iniciar el ensayo. No uses `up --profile serve`, `down`, `demo` ni `--force-recreate` para preparar esta corrida.
5. Mantén terminal/equipo despiertos y ejecuta **una sola vez**:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 scripts/project.py events-check \
  --machine-id mac24 --expected-commit "$T5_MEMORY_COMMIT" \
  --source-run-id release-mac24-20261021T190717Z-f0a2d073 \
  --memory-diagnostics
```

Crea otro stream `simulation` conservando el anterior y reinicia sólo API como parte de la prueba de persistencia. Mantiene API 4 GiB/2 CPU, semilla 3 GiB/1 CPU/2 hilos, PostgreSQL/UI 512 MiB. Un `failed` por pico histórico es evidencia útil, no motivo para reintentar ni ampliar el límite.
6. Espera el ZIP cerrado. Valida con `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip` y publica con `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip`, también si el lector devuelve 1 (fallo válido). Salida 2 impide publicar: conserva el archivo y reporta la causa. No reintentes automáticamente.
7. Compara inventario privado antes/después. Crea **`evidence/events-memory-mac24.json`**, sin reemplazar `evidence/events-mac24.json`. Incluye run_id, commit, protocolo, estado, archive SHA/tamaño, URL, entorno y resultado exactos del ZIP, capturas completas/parciales, fallo estructurado, tiempos, recursos, delta `max`/OOM/swap/presión por fase e identidad PostgreSQL conservada o cambiada. Separa pruebas/preservación declaradas de lo contenido en el ZIP. Añade nota a `docs/events-memory.md`, publica por Git privado y termina.

Devuelve estado real, `diagnostics.complete`, criterio fallido si existe, paridad/persistencia, conteos, recursos y URL de Release. No declares T5/E4 cerradas. La Mac principal descargará y revisará; no hay monitor ni activación automática del otro agente.
