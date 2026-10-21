# Prompt para mac24: T4-A

**Histórico: T4-A terminó y fue revisada el 2026-10-21. No volver a ejecutar este
prompt salvo solicitud explícita de reproducción.** Véase
[la auditoría](../evidence/releases-review-mac24.json). El siguiente desarrollo
es T4-B; su implementación y nuevo prompt todavía están pendientes.

Actualizar main por fast-forward preservando cambios locales y fijar el SHA
completo recibido. Leer README, AGENTS y docs/releases.md. Mantener el clon limpio
y ese commit durante las corridas. Esta tarea es T4-A: construir el paquete local
de H y comprobar paridad; no entrenar, repetir Q1–Q4, activar API ni evaluar test.

Conservar datos/features/checkpoints, work y ZIP Q1–Q4 y ledger. Comprobar y dejar
inventario privado de huellas de originales antes/después; no publicar sus filas
ni IDs. Usar Colima hm-project 14 GiB/4 CPU y recursos mac24 10 GiB/3 CPU/2 hilos,
siempre SSL_CERT_FILE=/etc/ssl/cert.pem. Mantener el equipo despierto y un solo job
pesado. Debe haber 20 GiB libres al iniciar.

Seguir docs/releases.md: reconstruir jobs, verificar --suite releases y ejecutar
release-job start primero con --dataset synthetic y después, sólo si pasó y se
publicó su reporte, --dataset hm. Ambas órdenes requieren --data-as-of 2020-09-09,
--expected-commit SHA_COMPLETO, --machine-id mac24 --profile hm-project
--resources mac24. No cambiar ventanas, catálogo, pool ni desempates.
Los ZIP Q4/Q1/diagnóstico faltantes pueden descargarse del repositorio privado;
si faltan las tablas privadas originales de Q1-V3, detenerse y reportarlo.
No reconstruirlas desde ZIP ni repetir la campaña.

Esperar cada estado final; exportar, validar y publicar cada ZIP cerrado, también
ante fallo válido. No reintentar automáticamente; cualquier fallo detiene la
secuencia. Confirmar top200 exacto, métricas atol 1e-10/rtol 0, 72,019 compradores
V3 y 102,967 artículos en full, y que el paquete incluye todos los compradores
anteriores al corte. Reportar tamaño/identidad del paquete, tiempo, memoria,
disco, swap/OOM y límites reales; no confundir duración offline con latencia.

Después del cierre, actualizar evidence/mac24-release-t4a.json y los documentos
de resultados, hacer commit/push y devolver run_id, SHA y enlaces a ambas Releases.
Los paquetes y tablas se quedan locales; GitHub sólo recibe reportes agregados.
Conservar quality_accepted=false, test_evaluated=false y activated=false.
No iniciar T4-B. Avisar al usuario al terminar: publicar no activa al agente de
la otra Mac.
