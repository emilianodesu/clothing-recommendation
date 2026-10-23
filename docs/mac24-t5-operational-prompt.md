# Mac24: una ejecución de T5 operativo con continuidad temporal

**Alternativa autorizada:** [adaptador desde Terminal/SSH](mac24-terminal.md). Ejecuta la misma secuencia; no ejecutar este prompt si el adaptador ya reservó o inició un intento.

Lee `AGENTS.md`, `docs/events-operational.md` y `evidence/events-clock-review-mac24.json`. Ejecuta el protocolo nuevo una sola vez con el SHA completo recibido. Los prompts anteriores son históricos; no repetir `clock-check`, aumentar memoria, recrear PostgreSQL, borrar streams/volúmenes o resetear contadores. Conserva H, originales, ledger, Q1–Q4, T4-A/T4-B y los tres ZIP T5 fallidos. No entrenar, abrir test ni iniciar T6.

1. Actualiza `main` por fast-forward conservando cambios existentes. Comprueba clon limpio y guarda el SHA recibido en `T5_OPERATIONAL_COMMIT`. No actualices código durante la corrida. Usa `SSL_CERT_FILE=/etc/ssl/cert.pem`, `DOCKER_CONTEXT=colima-hm-project` y Colima 14 GiB/4 CPU; no generes otro `.env` ni copies credenciales.
2. Comprueba que no haya otros trabajos pesados. Necesitas datos T2 `data/processed/hm-v1`, paquete full `artifacts/data-jobs/release-mac24-20261021T190717Z-f0a2d073/work/release`, su ZIP T4-A y versión activa `h-d12e30cb4d8c44cc22468e86c634db566e6f126ddc704e36723fe09d9a8a7b0a`. Los reportes no sustituyen archivos privados faltantes. Conserva un inventario privado antes/después; sólo exporta conteos y huellas.
3. Registra privadamente ID y hora de arranque de PostgreSQL, sin volcar `docker inspect` completo. API/UI/PostgreSQL deben seguir activos. Si PostgreSQL se reinició desde el diagnóstico, informa del cambio y detente antes del ensayo; no reconstruyas el historial ni continúes como si su identidad fuera la misma. No uses `up --profile serve`, `demo`, `down` ni `--force-recreate` para preparar esta corrida.
4. Reconstruye sólo jobs y ejecuta la suite; el código científico del servicio y sus recursos no cambian:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project python3 - <<'PY'
import sys
sys.path.insert(0,'scripts')
from bootstrap import read_configuration
from project import ROOT,compose
compose(ROOT,read_configuration(ROOT/'.env'),'build','jobs')
PY
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project \
python3 scripts/project.py verify --suite events --existing-services
```

Conserva logs locales y comprueba otra vez identidad PostgreSQL. Un fallo de pruebas o cambio inesperado de identidad detiene la secuencia; devuelve diagnóstico agregado, sin lanzar el ensayo.

5. Con alimentación AC, tapa abierta y terminal activa, ejecuta **una sola vez**:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project \
python3 scripts/project.py events-check \
  --machine-id mac24 --expected-commit "$T5_OPERATIONAL_COMMIT" \
  --source-run-id release-mac24-20261021T190717Z-f0a2d073 --operational
```

El runner administra su proceso `caffeinate`, crea un stream nuevo, mide operaciones y reinicia sólo API para comprobar persistencia. Mantiene API 4 GiB/2 CPU, semilla 3 GiB/1 CPU/2 hilos, PostgreSQL/UI 512 MiB. Conserva todos los valores de pico/contadores. La versión nueva trata el pico histórico como diagnóstico, pero exige límites efectivos, cero swap/OOM, generaciones, continuidad y controles funcionales. No eleves recursos o umbrales tras medir.

6. Espera estado final; guarda el ZIP anunciado, incluso ante fallo. No reintentes automáticamente. Valida y publica:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/read-handoff.py RUTA_EXACTA.zip
SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/github-handoff.py upload RUTA_EXACTA.zip
```

Un lector con salida 1 valida evidencia fallida y debe publicarse; salida 2 impide publicar. Conserva una carpeta incompleta tras corte abrupto y explica el bloqueo. El intercambio privado está autorizado. No exportes tokens, clientes ni respuestas HTTP.

7. Crea `evidence/events-operational-mac24.json` con run_id, commit, protocolo, estado, tamaño/SHA/URL del ZIP y `result`/`environment` exactos. Separa declaraciones de suite, preservación y comparación histórica de identidad. Resume UTC/monotónico, coherencia, guard liberado, operaciones, capturas, pico/max/presión, swap/OOM, recursos, paridad y persistencia. Añade nota a `docs/events-operational.md`, publica por Git y termina.

Devuelve estado real y URL, sin declarar T5/E4 cerradas: la Mac principal debe revisar el reporte. No ejecutes otros experimentos al terminar ni interpretes el aprobado como capacidad bajo carga T8.
