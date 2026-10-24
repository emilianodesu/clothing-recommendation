# Inventario de entrega T8-D

**Actualización:** el comando de [aceptación sintética](acceptance.md) ya está implementado; su ejecución Docker integral sigue pendiente de un motor dedicado con capacidad. La preparación anterior que lo describe como inexistente es histórica. No ejecutar sobre el motor mac24 ocupado.

Fecha: 2026-10-24. Base auditada: `eb66c5a` (cierre T8-C); este cambio añade documentación, sin ejecutar H&M ni modificar servicios. T8 global permanece abierto.

## Documentación disponible

| Entregable | Archivo |
| --- | --- |
| Informe académico | [final-report.md](final-report.md) |
| Ficha del motor | [model-card.md](model-card.md) |
| Subtemas y límites | [course-coverage.md](course-coverage.md) |
| Guion y guía del evaluador | [demo.md](demo.md) |
| Instalación | [setup.md](setup.md), [bootstrap.md](bootstrap.md), [second-mac.md](second-mac.md) |
| Servicio y contratos | [architecture.md](architecture.md), [serving.md](serving.md), [events.md](events.md) |
| Operación y recuperación | [operations.md](operations.md), [reproducibility.md](reproducibility.md) |
| Selección y experimentos | [model-selection.md](model-selection.md), [quality-summary.md](quality-summary.md), [graph-quality.md](graph-quality.md), [ranker-quality.md](ranker-quality.md) |

## Evidencia principal conservada

| Área | Auditoría en Git | Estado |
| --- | --- | --- |
| Q1–Q4 | `quality-q1-review.json`, `quality-q2-review.json`, `quality-q3-review.json`, `quality-decision-mac24.json` | Cerradas; selección H |
| T4 | `releases-review-mac24.json`, `serving-host-review-mac24.json` | Cerradas |
| T5 | `events-operational-review-mac24.json` | Cerrada bajo protocolo operativo; fallos anteriores conservados |
| T6 | `pipeline-review-mac24.json` | Cerrada; frescura y recuperación acotadas |
| T7 | `portability-review-mac24.json` | Cerrada con alcance acordado, sin otra cuenta de SO |
| T8-A | `benchmark-review-mac24.json` | Baseline aceptada |
| T8-B | `capacity-review-mac24.json`, `concurrency-review-mac24.json` | Inventario aceptado; ensayo concurrente failed válido |
| T8-C | `final-review-mac24.json` | Test único cerrado; no repetir |

Los archivos de la tabla están en `evidence/`; cada auditoría identifica fuentes, revisión y alcance. Las pruebas locales no se atribuyen a mac24. Los ZIP agregados no contienen datos suficientes para recalcular métricas por comprador. Los logs privados, paquetes, datos y checkpoints siguen siendo necesarios para una reproducción científica autorizada.

## Versiones y recursos

`uv.lock` fija dependencias Python; `pyproject.toml` declara el proyecto; `compose.yaml` y Dockerfiles fijan imágenes/configuración; `configs/resources/` contiene presupuestos de jobs. Los reportes registran identidad efectiva de imagen, fuente y ejecución. No hay una sola imagen histórica común a todas las campañas: no construir sobre tags mutables y afirmar que eso reproduce sus bytes. El informe distingue el commit de cada evidencia de la revisión documental de entrega.

## Pendientes antes de declarar E8 completo

1. Verificar integralmente el contrato de aceptación final sintética implementado en `verify --suite acceptance`. Debe comprobar recorrido crítico aislado, sin test real, entrenamiento ni nueva carga histórica.
2. Ejecutar y registrar esa aceptación en un entorno con presupuesto explícito. Conservar fuente/imagen/resultados, también si falla; una comprobación estática de documentos no acredita ejecución.
3. Capturar la demo sintética según el guion, con versión y entorno. No publicar datos reales ni credenciales.
4. Revisar los artefactos finales y crear un tag de entrega sobre el commit validado. No se creó todavía un tag final ni se declaró E7/E8 completos.

T8-B no se convertirá a passed: la limitación de concurrencia sostenida debe figurar en la entrega. La política secuencial queda documentada, sin prometer frescura o rendimiento mientras se ejecutan trabajos pesados.

## Decisiones de esta entrega documental

Se mantiene el checkout compartido autorizado y se conservan todos los experimentos. Se usan documentos Markdown versionados para que mac24 y el evaluador los lean sin herramientas adicionales. No se reejecuta `benchmark` como sugería el plan histórico: T8-A ya está auditado y T8-C consumió test. Los comandos de aceptación aún inexistentes se dejan explícitamente pendientes, evitando que un agente intente usarlos o interprete esta documentación como permiso para repetir campañas.
