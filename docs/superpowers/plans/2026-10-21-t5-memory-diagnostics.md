# T5: diagnóstico de memoria por fase

Spec: `docs/events.md`, sección Siguiente paso de ingeniería, y `evidence/events-review-mac24.json`.

## Global constraints

Instrumentación, sin cambiar recomendaciones ni límites. Mantener events-v1 y ZIP histórico. Nuevo protocolo opt-in events-memory-v1: observa y conserva el mismo criterio peak < limit; no autoriza cierre T5. Un diagnóstico completo puede tener status failed. No T6, test, entrenamiento ni ejecución H&M aquí. Publicar código y prompt por el canal Git privado ya autorizado.

## Task 1: capturas y diferencias de cgroup

Files: scripts/events_memory.py; tests/host/test_events_memory.py.
Interfaces: capturas serializables de api/ui/postgres antes de semilla, después de semilla, después de replay, después de reinicio. El job semilla conserva su medición aislada existente. Identidad Docker + StartedAt + inode de cgroup; límites, current/peak/swap, events, stat seleccionado y pressure total. Diferencias sólo con identidad estable; no resetear contadores. Captura parcial ante fallos queda explícita.

1. Escribir regresiones de pico histórico, presión nueva, OOM/swap, contador decreciente y cambio de generación; ejecutar en rojo.
2. Implementar captura acotada sin secretos y análisis determinista; ejecutar en verde.
3. Ejecutar `python3 -m unittest discover -s tests/host -v` y commit. Expected: suite completa verde.

## Task 2: runner y reporte versionado

Files: scripts/events_check.py, scripts/events_handoff.py, scripts/handoff.py, scripts/project.py, tests/host/test_events_memory.py.
Interfaces: --memory-diagnostics opt-in; mantener ruta v1. events-memory-v1 añade diagnostics; lector valida forma incluso si falla y coherencia de captura/derivados. Identificar final-validation y componente/criterio sin copiar excepciones con secretos. Fuente del host scripts debe tener huella en el entorno.

1. Regresiones: CLI opt-in, captura parcial y ZIP fallido, final-validation explícita, reporte alterado rechazado, v1 conserva fallo pico; observar rojo.
2. Implementar puntos de captura y validación estricta de diagnósticos. No elevar recursos ni reinterpretar v1.
3. Suite host completa, commit. Expected: verde con compatibilidad histórica.

## Task 3: comprobación Docker y entrega mac24

Files: docs/events-memory.md, docs/mac24-t5-memory-prompt.md, AGENTS.md, README.md, evidence/events-memory-local.json.
Interfaces: clon sintético limpio; mismo límite PostgreSQL. Una validación local con Docker real y reporte cerrado. Revisión independiente de toda la rama al terminar, resolver críticos/importantes con regresiones. Publicación autorizada a main, preservar reportes.

1. Ejecutar suite events y ensayo sintético opt-in desde clon limpio; validar ZIP. Expected: diagnóstico completo, explícito aunque un criterio de memoria falle.
2. Escribir evidencia con alcance local, contratos y prompt de una sola ejecución H&M con commit limpio, inventario privado y publicación del ZIP incluso fallido; no repetir v1, no resetear PostgreSQL.
3. Validar JSON/enlaces/diff, revisión independiente, commit y merge/push siguiendo el flujo autorizado. Expected: main limpio y prompt reproducible.

## Review Focus

Contadores heredados y cgroups reiniciados; no atribuir pico de vida a intervalo ni usar cero por métricas faltantes. Fallo Docker/captura/validación debe cerrar ZIP sin exponer configuración ni secretos. Rechazar diagnósticos alterados incluso dentro de reportes failed; conservar v1. No cambiar límites ni usar test. Una captura extra puede perturbar levemente la memoria, debe declararse. Preservar streams/volúmenes y el reporte fallido original.
