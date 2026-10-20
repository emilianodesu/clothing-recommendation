# Prompt para ejecutar Q2 en mac24

**Histórico: Q2 ya se ejecutó y revisó.** Consulta [el cierre](graph-quality.md#revisión-de-la-máquina-principal-q2-cerrada). No repetir este prompt por defecto ni usarlo para iniciar Q3, que aún necesita su propia implementación y entrega.

Sustituye `SHA_COMPLETO_RECIBIDO` por el commit de entrega indicado por la Mac principal.

> Actualiza desde GitHub y fija `SHA_COMPLETO_RECIBIDO` en un clon limpio, conservando cualquier cambio existente. Lee AGENTS.md, docs/graph-quality.md y evidence/quality-q1-review.json.
>
> Ejecuta Q2 exactamente según la guía: demo V3 → piloto V3 → full V1 → full V2 → full V3, con las cinco referencias Q1 de su tabla. Usa `quality-job --stage graph`, Colima hm-project 14 GiB/4 CPU, recursos mac24 10 GiB/3 CPU/2 hilos y el prefijo `SSL_CERT_FILE=/etc/ssl/cert.pem`. No cambies código ni revisión durante las corridas.
>
> Conserva los datos, features, checkpoints, work privados Q1 y el presupuesto acumulado. Verifica originales antes/después. Espera estado final, exporta, valida y publica cada reporte por GitHub Releases privado, incluso si falla. Un fallo operativo detiene la secuencia; una métrica peor es evidencia válida. Si faltan ZIP, descárgalos; si faltan artefactos privados Q1, devuelve el bloqueo sin repetir Q1.
>
> Mantén las métricas full G separadas del control PPR de hasta 5,000 compradores. No ajustes parámetros, no repitas entrenamiento ni baselines, no uses test y no inicies Q3/Q4/T4.
>
> Al terminar los cinco intentos o el primer fallo, registra evidence/quality-q2-mac24.json y actualiza docs/graph-quality.md con resultados, recursos, disco, presupuesto, hashes y URLs de Release. Redondea resúmenes como indica la guía. Publica esos cambios en GitHub y devuelve el estado real de cada intento. Los datos/modelos/grafos permanecen locales; la otra Mac leerá los reportes publicados.
