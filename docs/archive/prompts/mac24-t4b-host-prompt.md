# Prompt para mac24: acceso HTTP del host, T4-B v2

**Histórico: ejecución terminada y [revisada](../evidence/serving-host-review-mac24.json). No repetir salvo petición explícita.** T4-B está cerrada; conservar este procedimiento y sus reportes para reproducción.

Actualizar main por fast-forward conservando cambios locales. Fijar el SHA
completo recibido en un clon limpio y leer README, AGENTS, docs/serving.md
(sección «Corrección v2 y comprobación desde el host») y docs/architecture.md.

Este seguimiento corrige la publicación del puerto y añade evidencia del host.
El ensayo v1 `serving-mac24-20261021T200054Z-cce4d43e` ya pasó por HTTP interno:
conservarlo sin reescribirlo. No repetir entrenamientos, T4-A/Q1–Q4 ni evaluar
test; no iniciar T5. Preservar datos/features/checkpoints, paquetes, ZIP, work y
ledger, con inventario privado antes/después; no publicar filas ni credenciales.

Conservar Colima hm-project 14 GiB/4 CPU, API 4 GiB/2 CPU/2 hilos y probe
3 GiB/1 CPU/2 hilos. Usar SSL_CERT_FILE=/etc/ssl/cert.pem y
DOCKER_CONTEXT=colima-hm-project. No ampliar límites ni ejecutar jobs pesados.

Seguir los comandos de la sección v2: reconstruir app-check/jobs; ejecutar
verify --suite serving, que ahora incluye una regresión Docker desde el host;
iniciar up --profile serve --skip-build para recrear la API con su nueva red,
conservando PostgreSQL, volúmenes y versión persistida. Si falla preparación,
detenerse y devolver diagnóstico; no cambiar puertos/código/recursos para eludirlo.

Comprobar los paquetes privados y ZIP T4-A originales de demo
release-mac24-20261021T190539Z-02a850f8 y H&M
release-mac24-20261021T190717Z-f0a2d073. Si falta sólo un ZIP, descargarlo del
repositorio privado; si falta el paquete, informar del bloqueo sin reconstruirlo.

Ejecutar serving-check **una sola vez** con --machine-id mac24,
--expected-commit SHA_COMPLETO, --reference-run-id de demo y --candidate-run-id
de H&M. Esperar en primer plano, con equipo despierto. Debe producir un nuevo
run_id con protocolo history-serving-v2: cinco checks internos y cinco checks
desde el host, dos recomendaciones por check host, vinculadas a huellas del
paquete. También exige puerto efectivo sólo en 127.0.0.1, código y versión
correctos tras reinicio y rollback. H&M queda activo si pasa.

No reintentar automáticamente. Validar y publicar el ZIP cerrado anunciado,
también si falla válidamente. Ante corte abrupto conservar carpeta incompleta;
no inventar un reporte. Un fallo puede dejar otra versión activa: inspeccionar
antes de cualquier intervención. Conservar el reporte v1 y todos los originales.

Después del cierre, registrar evidence/mac24-serving-host-t4b.json y resultados
en docs/serving.md: commit, run_id, Release, protocolo, fuentes, paridad interna/
host, publicación, reinicio/rollback, memoria/duración y estado final. Suite y
preservación se declaran separadas del ZIP. Mantener quality_accepted=false,
test_evaluated=false; no afirmar carga T8. Hacer commit/push y avisar al usuario.
