# Prompt para mac24: T4-B

Actualizar main por fast-forward preservando cambios locales. Fijar el SHA
completo recibido en un clon limpio y leer README, AGENTS, docs/serving.md y
docs/architecture.md. Esta tarea verifica API, activación, reinicio y rollback
con los paquetes T4-A existentes; no entrenar ni repetir T4-A/Q1–Q4, evaluar test
o iniciar T5.

Conservar inventario privado antes/después de originales: paquetes T4-A,
datos/features/checkpoints, work/ZIP Q1–Q4 y ledger. No publicar sus filas o IDs.
Usar Colima hm-project 14 GiB/4 CPU, SSL_CERT_FILE=/etc/ssl/cert.pem y
DOCKER_CONTEXT=colima-hm-project. API tiene 4 GiB/2 CPU/2 hilos; probe temporal
3 GiB/1 CPU/2 hilos. No ejecutar otros trabajos pesados. Mantener el equipo
despierto y la terminal/agente disponibles hasta que termine el ensayo.

Seguir docs/serving.md: reconstruir app-check y jobs, verificar --suite serving,
iniciar up --profile serve --skip-build. No ampliar límites para evitar fallos.
Comprobar los paquetes y ZIP cerrados de demo
release-mac24-20261021T190539Z-02a850f8 y H&M
release-mac24-20261021T190717Z-f0a2d073. Si falta sólo un ZIP, descargarlo del
repositorio privado; si faltan archivos privados del paquete, detenerse y
reportar el bloqueo. No sustituirlos con ZIP de agregados.

Ejecutar serving-check una sola vez con --machine-id mac24,
--expected-commit SHA_COMPLETO, --reference-run-id de demo y --candidate-run-id
de H&M según el comando exacto de la guía. Corre en primer plano. Esperar cierre;
no cambiar código/recursos ni repetir automáticamente. El ensayo activa demo,
activa H&M, reinicia API, comprueba H&M, vuelve a demo por rollback y termina
sirviendo H&M. Si había otra versión activa, detenerse sin reemplazarla.

Validar y publicar el ZIP anunciado, también si es un fallo válido. Conservar
la carpeta incompleta ante corte abrupto; no confundirla con un reporte cerrado.
No enviar recomendaciones individuales, tokens ni archivos del paquete.

Después del cierre, registrar evidence/mac24-serving-t4b.json y resultados de
docs/serving.md: commit, run_id, URL, fuentes, versiones, muestras, paridad HTTP,
reinicio/rollback, recursos y estado final. Reportar preservación y suite por
separado como verificaciones locales. Mantener quality_accepted=false y
test_evaluated=false; latencia bajo carga y aceptación final siguen en T8.
Hacer commit/push y avisar al usuario. Publicar no activa al agente principal.
