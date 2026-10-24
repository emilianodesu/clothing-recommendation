# Plan de traspaso al servidor

> **For agentic workers:** usar superpowers:executing-plans para la implementación; el receptor sigue docs/server-deployment.md para desplegar, no este plan ya ejecutado.

**Goal:** entregar herramientas y documentación reproducibles para desplegar H con HTTPS y acceso del equipo.
**Architecture:** Compose existente + restauración lógica en instalación nueva + gateway Caddy independiente. Diagnóstico previo y credenciales locales.
**Tech Stack:** Python estándar en host, Docker/Compose, PostgreSQL, FastAPI, Streamlit, Caddy.
**Spec:** [diseño](../specs/2026-10-24-server-handoff-design.md).

## Restricciones

No tocar mac24, datos privados, campañas, reservas ni test final. Repo público permitido. Sólo gateway 80/443 público, usuarios individuales del equipo. Linux ARM64/AMD64 por diagnosticar; verificación física destino pendiente.

## Revisión enfocada

- Docker ausente, remoto o con cargas ilimitadas: diagnóstico comprensible y rechazo antes de mutaciones.
- Dominio/usuario malformados y credenciales: validación estricta; contraseñas sólo por stdin.
- Respaldo incompleto, equivocado o destino existente: checksum y recuperación existente; no sobrescribir.
- Puertos/proxy de otra aplicación: no detenerlos ni publicar una UI sin autenticación.
- Proceso interrumpido: conservar reportes/volúmenes; no reiniciar campañas o restauración parcial.

## Tareas

- [x] Diagnóstico/admisión + tests negativos y rutas con espacios (`scripts/server_support.py`, `tests/host/test_server.py`).
- [x] Orquestación nueva sin runners históricos (`scripts/server.py`): preparar, construir, restaurar, UI, respaldo y comprobaciones.
- [x] Gateway fijo, cuentas y tests de configuración/auth (`deploy/`, `scripts/server_gateway.py`).
- [x] Traspaso documental, archivo histórico, reporte consolidado y referencias.
- [x] Ejecutar tests pertinentes, revisar cambios y publicar commit reproducible. Registrar límites de validación.

## Resultado de preparación

[Verificación](../../../evidence/server-handoff-local.json): 334 pruebas host, 333 aprobadas y una omisión prevista; proxy nativo sintético con TLS/auth/WebSocket y render Compose correctos. Revisión independiente sin hallazgos prioritarios pendientes. No se ejecutó la instalación física Linux ni ACME público; el receptor completa la guía de despliegue.
