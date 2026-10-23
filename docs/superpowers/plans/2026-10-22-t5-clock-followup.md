# T5: seguimiento de relojes y preparación del control temporal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Producir evidencia acotada de relojes/energía en mac24 sin repetir H&M y preparar un control temporal comprobado para el siguiente protocolo.

**Architecture:** Python estándar en el host valida el ZIP anterior, recalcula sus tiempos y resume únicamente eventos de energía dentro de su ventana UTC. El nuevo `events-clock-v1` usa el transporte existente de reportes cerrados. Los controles futuros de coherencia y reposo se prueban separadamente; aún no se conectan a `events-check`.

**Tech Stack:** Python 3.9+, Git, `pmset -g log` en macOS, `caffeinate` para la futura protección de reposo; sin Docker ni nuevas dependencias.

**Spec:** [auditoría y secuencia aprobada al pedir continuar](../../events-memory.md#siguiente-paso-propuesto-sin-nueva-ejecución).

## Global Constraints

- No ejecutar H&M, Docker, entrenamiento, test ni T6; no leer `.env` o secretos.
- Conservar originales, volúmenes, límites y validadores `events-v1`/`events-memory-v1`.
- `SSL_CERT_FILE=/etc/ssl/cert.pem`; GitHub privado es el único intercambio.
- Reportar desconocido cuando falte evidencia; presencia de reposo no prueba causalidad exclusiva.
- Exportar sólo timestamps/categorías/conteos y versiones/implementaciones de reloj. No mensajes libres de `pmset`, nombres de procesos, rutas o motivos de wake.
- Fuentes de energía limitadas a 16 MiB/30 s; truncamiento o lectura incompleta explícitos. No privilegios ni reintento automático.
- El futuro control usa UTC y monotónico, presupuesto 7,200 s y discrepancia máxima de 2 s, fijada antes de nuevas mediciones como control de continuidad, no objetivo de latencia. No aplicarlo retroactivamente.

## Review Focus

- Timestamps sin zona, reloj hacia atrás, NaN/bool o intervalos incongruentes deben rechazarse o impedir aceptación.
- Un log vacío, antiguo, truncado o con formato desconocido nunca prueba ausencia de suspensión.
- Un reporte con protocolo cambiado o agregados manipulados no puede evadir validación ni exportar mensajes privados.
- Fallo/timeout de `pmset` debe producir evidencia incompleta cerrada, sin tocar servicios.
- La protección de reposo debe liberar sólo su propio proceso al fallar, sin prometer impedir cierre de tapa/suspensión forzada.

### Task 1: Cálculos temporales y energía acotada

**Files:** Crear `scripts/events_clock.py`, `tests/host/test_events_clock.py`.

**Interfaces:** `analyze_timing(started_at, finished_at, duration, snapshots) -> dict`; `parse_power(lines, start, end) -> dict`; `collect_power(start, end) -> dict`. Capturas reducidas a fase/servicio/UTC/monotónico, intervalos recalculables. Eventos exportados: `sleep`, `wake`, `darkwake`; zona obligatoria, orden UTC, máximo 256. Estado de cobertura independiente de causalidad.

- [ ] Escribir y observar RED para diferencia UTC 900 s/monotónico 1 s, offsets, log fuera de ventana/privado, desconocidos, falta de cobertura y truncamiento.
- [ ] Implementar cálculos puros y captura acotada del único comando fijo en lectura, sin imprimir stdout/stderr.
- [ ] Ejecutar `python3 -m unittest discover -s tests/host -p 'test_events_clock.py' -v`; comprobar GREEN y commit.

### Task 2: Reporte cerrado y controles para la siguiente versión

**Files:** Crear `scripts/clock_check.py`, `scripts/clock_handoff.py`, `scripts/clock_guard.py`, `tests/host/test_clock_handoff.py`, `tests/host/test_clock_guard.py`. Modificar `scripts/handoff.py`, `scripts/project.py`.

**Interfaces:** `project.py clock-check --machine-id ID --expected-commit SHA --source-report ZIP` crea un nuevo reporte `events-clock-v1` en `artifacts/handoff/`. `clock_handoff.validate_clock(report,result,env,names)` valida identidad/forma/derivados incluso ante falta de logs. `Timeline.mark(label)` captura y conserva ambos relojes; `Timeline.check()` impide aceptar discrepancia >2 s, reloj regresivo o presupuesto >7,200 s. `AwakeGuard` crea y libera su propia aserción temporal macOS, sin cambios persistentes. Son utilidades futuras, no una activación del ensayo.

- [ ] Escribir y observar RED para cierre de reporte incompleto, transporte GitHub, manipulación/downgrade, fuente ajena o inválida; relojes desajustados y limpieza de guard al fallar.
- [ ] Implementar CLI sin `.env`/Docker, commit limpio y fuente del mismo alias de equipo. Guardar identidad/hash de fuente y su estado original. `passed` significa recolección completa, nunca T5 aprobada ni causa confirmada; `failed` conserva faltantes.
- [ ] Ejecutar toda la suite host con loopback; verificar compatibilidad de tres ZIP históricos locales, commit.

### Task 3: Handoff mac24 y revisión

**Files:** Crear `docs/clock-diagnostics.md`, `docs/mac24-t5-clock-prompt.md`, `evidence/events-clock-local.json`; actualizar `AGENTS.md`, `README.md`, guía T5/plan.

- [ ] Documentar la propuesta de memoria para una versión futura: límites efectivos, cero swap/OOM, controles funcionales y continuidad; pico/max/presión como diagnóstico, sin tolerancia ajustada al resultado. No activar criterio ni nueva corrida en esta entrega.
- [ ] Verificar CLI desde clon limpio con fuente sintética del mismo alias; no atribuir resultado local a mac24. Publicar sólo reporte diagnóstico cerrado si se usa GitHub.
- [ ] Ejecutar revisión independiente final según executing-plans, corregir hallazgos importantes con RED→GREEN, preservar evidencia local y publicar por el flujo Git autorizado.
- [ ] Entregar prompt de una recolección en lectura con SHA exacto; exportar incluso incompleto, sin repetir H&M.
