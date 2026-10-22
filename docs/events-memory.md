# Diagnóstico T5 de memoria por fase

El intento H&M original conserva `failed` y T5/E4 siguen abiertas. Su [auditoría](../evidence/events-review-mac24.json) encontró un pico acumulado PostgreSQL de 512.258 MiB frente a 512 MiB. Esta entrega mide dónde aparece la presión, sin aumentar recursos, cambiar H ni reinterpretar el reporte original.

## Protocolo y criterios

`events-check --memory-diagnostics` genera `events-memory-v1` dentro del mismo transporte `events-handoff`. Sin la opción sigue disponible `events-v1`. El nuevo lector admite ambos; un lector antiguo no puede validar un resultado aprobado del protocolo nuevo. El SHA de todos los scripts Python del host queda en `environment.json`, además de código científico y lock. Cada corrida exige clon limpio y SHA completo.

Se conserva el criterio de pico estrictamente inferior al límite, cero swap y ausencia de OOM, junto con todos los controles funcionales v1. Además, se exigen capturas completas, límites efectivos coherentes, contadores consistentes y generaciones esperadas. Los contadores OOM deben ser cero en todas las capturas. La API cambia de generación al reiniciarse; UI/PostgreSQL permanecen estables. No se fija un umbral nuevo de presión mirando esta corrida.

**`diagnostics.complete=true` sólo significa que se tomaron las doce capturas.** Puede coexistir con `status=failed`. Un pico histórico superior al límite seguirá causando fallo, pero ahora habrá evidencia para separar el estado heredado de la actividad nueva. Ningún resultado cierra T5 sin revisión en la Mac principal.

## Qué se mide

Se capturan API, UI y PostgreSQL en cuatro momentos: antes de semilla, después de semilla, después de replay y después del reinicio de API. Se guardan:

- Identidad del contenedor, imagen, hora de arranque, número de reinicios e inode del cgroup; límites Docker y del kernel.
- Memoria actual, pico de vida, swap actual/pico, contadores `low`, `high`, `max`, `oom`, `oom_kill`, `oom_group_kill`.
- Memoria anónima, archivos/caché, kernel y memoria compartida; esta última puede formar parte de la memoria de archivos, no se suman como categorías disjuntas.
- Totales de presión `some` y `full`, en microsegundos, y tiempos de captura/fase.

Las diferencias sólo se calculan dentro de la misma generación del cgroup. Después del reinicio API se registra `generation_changed` y diferencias nulas. Una caída de contador sin cambio de identidad queda inválida. El crecimiento de `memory.peak` indica aumento del máximo histórico: **no es el pico independiente de ese intervalo**. Una diferencia cero puede ocultar picos inferiores al máximo anterior. La semilla conserva sus límites/pico/swap/OOM aislados; no se añaden contadores internos al worker.

Las lecturas son secuenciales y usan pequeños procesos `docker exec`, por lo que añaden algo de tiempo/memoria y no son una instantánea atómica. La identidad se verifica antes/después de cada captura. No se modifica ni reinicia el contador. No se exportan variables de entorno, configuración Docker, rutas de montajes, SQL ni datos por comprador.

Referencia de semántica: [interfaces de memoria cgroup v2 del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files).

## Fallos y lectura

La fase final se identifica como `final-validation`; un fallo de recursos incluye componente, criterio, observado y esperado, sin copiar textos de excepciones que puedan contener secretos. Capturas incompletas conservan el prefijo disponible y el tipo de error, y generan un reporte cerrado ante fallos controlados. Un corte de energía/proceso puede impedir el cierre: nunca interpretar una carpeta incompleta como éxito.

El lector recomputa los derivados y rechaza capturas/intervalos incoherentes incluso en reportes fallidos. El contrato v1 no se relaja. Un `peak_below_limit` fallido sigue siendo un fallo del ensayo, no corrupción del ZIP: lector salida 1; ZIP inválido salida 2.

## Ejecución

Para una prueba sintética usa un clon limpio con configuración propia:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem DOCKER_CONTEXT=colima python3 scripts/project.py demo \
  --machine-id development --memory-diagnostics
```

Para H&M sigue [el nuevo prompt mac24](mac24-t5-memory-prompt.md). Hace una única corrida con el paquete original activo. El reporte v1, sus streams, datos, ledger y volúmenes se preservan. No entrenar, abrir test ni iniciar T6.

## Evidencia local y revisión

Dos ensayos sintéticos desde clones limpios produjeron doce capturas completas y reportes aprobados; el último usa `7332cea42df360fd6910b3100412b01948844ed5`, dura 5.245 s y confirma el cambio de generación API sin reiniciar PostgreSQL. [Reporte privado](https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-events-development-20261022T050923Z-da5ae7f6), [evidencia agregada](../evidence/events-memory-local.json). Son pruebas locales, no una medición de capacidad H&M.

La revisión independiente encontró y se corrigieron con regresiones dos problemas del lector: evasión mediante cambio de protocolo y causas/recursos contradictorios en reportes fallidos. No quedaron menores aplazados. La suite host final pasó 112 casos; la suite oficial events y las ocho pruebas de bootstrap Docker también pasaron. La suite científica completa pasó 133 casos y omitió la prueba dedicada a MLflow. El primer comando genérico `pytest` dentro de jobs encontró siete errores de colección de pruebas que pertenecen al host; el comando corregido separa ambos entornos y sus resultados están registrados en la evidencia.

Decisiones conservadas: el criterio `peak < limit` permanece, con el coste de que el diagnóstico H&M puede volver a terminar fallido por un pico histórico; la semilla mantiene métricas aisladas del job, sin añadir contadores internos por fase al worker. T5/E4 siguen pendientes de la nueva evidencia H&M y su revisión.
