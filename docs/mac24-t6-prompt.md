# Mac24: preparar y ejecutar T6 una sola vez

T5/E4 ya están cerradas. No repetir T5, sus adaptadores ni diagnósticos. Mantener H, PostgreSQL con su identidad histórica, datos, paquetes, streams, ledger y todos los ZIP. No entrenar ni usar test. Este paso aplica migraciones aditivas, sustituye únicamente API para incorporar métricas y añade Airflow; conserva UI/PostgreSQL. La VM sigue 14 GiB/4 CPU. Alimentación AC, tapa abierta y Terminal abierta.

Desde main limpio y con el SHA completo recibido:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima-hm-project \
python3 scripts/mac24_t6.py --expected-commit SHA_COMPLETO_RECIBIDO
```

El lanzador comprueba T5 aprobado `events-mac24-20261023T030009Z-3f97f2a0`, identidad PostgreSQL, H activo, Q4 original, recursos, GitHub privado/escritura y ausencia de otra corrida T6. Conserva inventario privado de procesados, data-jobs, handoff y quality-budget. No afirma auditar imágenes/ZIP crudos Kaggle. Reserva `.mac24-t6-once.json`; no borrarla para reintentar.

Construye jobs/API/Airflow, inicializa metadatos y ejecuta la suite pipeline con PostgreSQL existente. Materializa un paquete H con corte 2020-09-09 usando el DAG manual, activación deshabilitada. Luego ejecuta una sola vez `pipeline-check --machine-id mac24 --expected-commit SHA --source-run-id events-mac24-20261023T030009Z-3f97f2a0`. Se registra el stream ya sembrado por T5; no vuelve a sembrar H&M.

El ensayo espera dos ciclos programados. Cada refresco puede tardar: el presupuesto de espera no convierte un resultado lento en aprobado; el objetivo de frescura sigue 600 s. No cambies memoria, consultas, fechas ni umbrales durante el intento. Al finalizar se pausan ambos DAG, se publica el ZIP válido incluso si falló y se actualiza `evidence/pipeline-mac24.json`. Sólo agregados y huellas viajan a GitHub.

Ante fallo de preparación: conserva `.runtime/mac24-t6-*` y la reserva; no continuar ni repetir automáticamente. Ante corte durante el ensayo: revisa servicios, carpeta parcial y tareas activas. Si existe un ZIP cerrado pero falla su transporte, usa únicamente `read-handoff.py` y `github-handoff.py upload`; no vuelvas a lanzar el adaptador. Salida 1 del lector es fallo válido; salida 2 es ZIP inválido, no publicable.

Devuelve estado, SHA, run_id, URL, duración, frescura, recursos, errores y preservación declarada. No cerrar T6/E5 ni iniciar T7 antes de la revisión principal. El acceso SSH desde Terminal y GitHub privado están autorizados; no configurar claves ni cambiar Sharing/permisos.
