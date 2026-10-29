# Prompt para ejecutar Q3 en mac24

**Histórico, ya ejecutado:** las diez corridas se descargaron y [revisaron](../evidence/quality-q3-review.json). No volver a usar este prompt por defecto. Conservar los artefactos privados para Q4; su comando y prompt se entregarán cuando estén implementados.

Sustituir `SHA_COMPLETO_RECIBIDO` por el commit de entrega de la Mac principal.

> Actualiza desde GitHub y fija `SHA_COMPLETO_RECIBIDO` en un clon limpio, conservando cambios existentes. Lee AGENTS.md, docs/ranker-quality.md y evidence/quality-q2-review.json. Q1/Q2 están cerradas: no repetirlas ni reentrenar PyTorch.
>
> Ejecuta Q3 según la guía: demo H → pilot H → demo G → pilot G → full H V1/V2/V3 → full G V1/V2/V3. Usa `quality-job --stage ranker --pool H|G`, las cinco referencias Q2 de la tabla, el mismo commit, Colima hm-project 14 GiB/4 CPU y recursos mac24 10 GiB/3 CPU/2 hilos. Conserva `SSL_CERT_FILE=/etc/ssl/cert.pem`. Una corrida por vez, equipo sin suspensión; no cambies código, parámetros ni presupuesto.
>
> Conserva dataset T2, features/checkpoints, los work y ZIP Q1/Q2 y el ledger de presupuesto. Inventaría hashes originales antes/después; deja esos inventarios privados. Si faltan datos/artefactos o permisos, devuelve el bloqueo: los ZIP no contienen grafos ni modelos. Tras la primera demo H, ejecuta verify --suite quality antes del piloto.
>
> Espera cada estado final; exporta, valida y publica el ZIP por GitHub Releases privado antes del siguiente intento, incluso ante fallo. Un fallo operativo detiene la secuencia; una métrica peor es evidencia válida y no autoriza ajustes. No publiques paquetes inválidos ni archivos privados de clientes/modelos/grafos. Verifica la descarga del adjunto mediante el script existente.
>
> Actualiza evidence/quality-q3-mac24.json y docs/ranker-quality.md con estado, commit, run_id, fold/tamaño/pool, referencias, métricas, conteos de las cuatro semanas, positivos no recuperados, grupos descartados, iteraciones/árboles, costes, RSS/cgroup/swap/OOM, disco y presupuesto. Incluye SHA/URL de Releases y preservación agregada de originales. Redondea métricas a ocho decimales y GiB a seis en Git; conserva precisión completa en ZIP. Publica la actualización y devuelve un resumen. Mantén calidad pendiente de revisión: no inicies Q4, T4 ni test.
