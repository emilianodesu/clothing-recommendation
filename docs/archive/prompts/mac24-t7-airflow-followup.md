# Seguimiento único T7: invocación configurada de Airflow

El intento `portability-mac24-20261023T203910Z-ed135689` conserva failed. Su ZIP (2,855 bytes) tiene SHA-256 `9c62660615e4f98a9b6b3f2cf25822b5ad27131969e8be3edf90fd036cfe78be`. La traza ubica el fallo en la llamada directa de pausa; el lanzador configurado de T6 sustituye esa llamada. El usuario confirmó después conexión Airflow OK y ambos DAG pausados. Esto no demuestra que el respaldo/restauración H&M haya pasado.

Con main limpio y el SHA completo recibido, ejecutar una sola vez:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/mac24_t7.py --airflow-followup --expected-commit SHA_COMPLETO
```

Mantener AC, tapa y Terminal abiertas. No usar la demo ni enviar eventos durante el ensayo. Conservar Colima 14 GiB/4 CPU y servicios/volúmenes originales. No entrenar, evaluar test o repetir T5/T6.

El adaptador exige el ZIP original íntegro, la reserva original y ausencia de otros ensayos. Comprueba la conexión configurada y ambos DAG pausados antes de construir, antes del ensayo y al comprobar preservación. Construye sólo jobs/app y ejecuta suite portability sin recrear API/PostgreSQL originales.

- Reserva nueva: `artifacts/handoff/.mac24-t7-airflow-followup-once.json` (bajo ARTIFACTS_DIR configurado).
- Destino nuevo: `.runtime/T7 restored installation airflow followup`, puerto localhost18024.
- Evidencia nueva: `evidence/portability-airflow-followup-mac24.json`, con enlace y huella del intento previo. El archivo original permanece intacto.
- Cada nuevo ZIP mantiene `portability-v1`: respaldo privado, seis consultas de paridad, seis consultas sin red externa, credenciales nuevas y preservación del estado original. El antecedente se enlaza en la evidencia y reserva; el esquema del ZIP no cambia.

GitHub recibe sólo agregados y huellas. No se publican respaldos, filas privadas, contraseñas ni logs internos. Un fallo conserva su ZIP y se publica; no se elimina reserva ni se repite. Si falla sólo la publicación, subir únicamente el ZIP cerrado con github-handoff.py. Fallos de preparación pueden no producir ZIP.

Devolver URL y estado. No cerrar T7/E6 ni iniciar T8 antes de revisión principal. Las pruebas host verifican las restricciones del lanzador; no sustituyen este ensayo físico.
