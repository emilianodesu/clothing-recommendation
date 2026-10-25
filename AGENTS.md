# Instrucciones para agentes

## Tarea vigente y entrada

El servidor del equipo ya está publicado. El usuario pidió simplificar la UI para demostrar recomendaciones, sin hashes/versiones ni controles de implementación. Leer [recorrido y verificación UI](docs/ui-demo.md). Confirmó actualización automática desde el repositorio; verificar el resultado en navegador y conservar autenticación, datos y volúmenes. La siguiente guía de preparación se conserva para instalaciones nuevas. **Leer README.md, docs/handoff.md y docs/server-deployment.md antes de actuar.** El usuario delegó descubrir SO, arquitectura, RAM y disco al agente receptor: `python3 scripts/server.py doctor` es diagnóstico en lectura. No asumir que es mac24, ni ejecutar sus lanzadores históricos.

La preparación del repositorio no equivale a haber desplegado en el servidor. El receptor debe registrar su commit, diagnóstico, entradas privadas, imágenes efectivas, checks locales/HTTPS y comprobación de navegador. Docker/Linux ARM64 y AMD64 están contemplados; la evidencia académica física previa es Mac ARM64. Si faltan dominio/DNS, acceso al servidor o respaldo privado, completar lo independiente y reportar el pendiente concreto.

## Reglas vigentes

- La UI usa [perfiles descriptivos](docs/demo-profiles.md) seleccionados de historiales reales, a petición del usuario. No sustituirlos por personajes con compras inventadas ni inferir género de compradores. Las categorías de moda y estampados requieren metadatos del catálogo; si faltan, omitirlas. Conservar H y sus reglas. Desplegar API/UI conjuntamente, sin migraciones ni campañas.

- Repo público o privado según decisión del usuario; no exigir privacidad ni cambiar visibilidad. Código y agregados pueden viajar por GitHub. Nunca publicar `.env`, tokens, datos H&M, compradores, predicciones individuales, backups o paquetes privados.
- H es historial28d+popularidad7d, reglas `recent-history-v1`, seleccionado bajo `quality-v1`. Mantener ventanas, desempates, deduplicación y cortes. H no necesita entrenamiento, Qdrant, MLflow ni PyTorch en inferencia. Conservar esos experimentos y sus resultados.
- Q1–Q4 y test final están consumidos y cerrados. No repetir campañas, test, aceptación mac24, carga ni runners históricos para instalar. El test no puede usarse para elegir otro método. Las comprobaciones funcionales nuevas del servidor no son otra evaluación académica.
- T8-B histórico sigue failed; H-cycle pasó bajo protocolo propio de un ciclo breve. No afirmar concurrencia sostenida, capacidad máxima ni aprobación T8 global. Consultar docs/findings.md y evidence/h-cycle-review-mac24.json.
- UI corregida desplegada y aceptada en mac24. Conservar sus overrides privados, imágenes de rollback, originales, reservas, ZIP y servicios. La solicitud de preparar otro servidor no autoriza nuevas pausas o recreaciones en mac24.
- El nuevo servidor expone sólo gateway 80/443. API/UI base permanecen localhost; PostgreSQL sin puertos públicos. Airflow/MLflow no se publican. Todos los usuarios web son operadores del equipo; no hay aislamiento por comprador ni auditoría de acciones por usuario web.
- Diagnosticar/admitir recursos por fase; no parar otros servicios ni aumentar cuotas para ocultar fallos. No usar `prune`, `down -v`, borrar intentos o reemplazar credenciales de instalaciones existentes. Restaurar en ruta/volúmenes nuevos; conservar fuente y copia privada del respaldo.
- No copiar `.env` ni configuración Docker entre hosts. Generar credenciales propias. Fijar commit e IDs de imágenes. El SHA y las consultas de paridad verifican el respaldo, no sustituyen obtenerlo por canal privado autorizado.
- En las Macs anteriores usar `SSL_CERT_FILE=/etc/ssl/cert.pem` para red, cuando corresponda. En Linux usar el almacén CA del sistema, sin copiar esa ruta ni deshabilitar TLS. SSH ya está autorizado donde el usuario proporcionó acceso; no crear claves/cuentas ni cambiar permisos ajenos automáticamente.
- Para cambios: pruebas pertinentes y evidencia real, no declarar ejecutados los checks físicos del servidor aún no disponible. No ignorar hooks Git ni controles de publicación.

## Fuentes de verdad

- docs/delivery-status.md: estado y límites de la entrega.
- docs/final-report.md, docs/model-card.md, docs/course-coverage.md: explicación académica.
- docs/server-deployment.md y docs/server-operations.md: procedimientos del nuevo servidor.
- docs/reproducibility.md: contrato lógico de backup/restore.
- evidence/*review*.json: auditorías; conservar los reportes failed originales.
- docs/superpowers/plans/2026-10-24-server-handoff.md: preparación actual y verificación.

La [historia completa de estas instrucciones](docs/history/AGENTS-2026-10-24.md) se conserva como referencia, no como instrucciones vigentes. Los «siguientes pasos» antiguos y «test reservado» quedaron superados. No leer ni ejecutar todos los prompts históricos para empezar el despliegue.
