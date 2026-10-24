# Inventario de entrega T8-D

Estado consolidado al 2026-10-24. **T8-D documental y visual entregado; T8-A y T8-C cerradas. T8-B conserva failed y no se declara aprobación global T8/E8.**

La validación adicional [h-cycle-v1](mac24-h-cycle.md) quedó aceptada tras
[revisión principal](../evidence/h-cycle-review-mac24.json): construcción 6.821 s,
12,000 respuestas correctas, p95 global 14.151 ms y 132 llegadas solapadas con
p95 8.909ms. Publicación completada. Acredita un ciclo breve, conserva T8-B
histórico failed y no constituye una nueva evaluación test.

El tag `entrega-academica-2026-10-24` conserva la instantánea académica original. La corrección UI `e478f71` es posterior; está desplegada en la demo local y en mac24. El [despliegue mac24](../evidence/ui-deployment-review-mac24.json) tiene validación operativa y render AppTest; la [revisión visual posterior en Chrome](../evidence/ui-visual-review-mac24.json) también quedó aceptada, en lectura. Véanse [notas de versión](release-notes.md) y [despliegue local](ui-metadata-deployment.md).

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
| H-cycle posterior | `h-cycle-review-mac24.json` | Ciclo breve real aceptado; no sustituye T8-B |

Los archivos de la tabla están en `evidence/`; cada auditoría identifica fuentes, revisión y alcance. Las pruebas locales no se atribuyen a mac24. Los ZIP agregados no contienen datos suficientes para recalcular métricas por comprador. Los logs privados, paquetes, datos y checkpoints siguen siendo necesarios para una reproducción científica autorizada.

## Versiones y recursos

`uv.lock` fija dependencias Python; `pyproject.toml` declara el proyecto; `compose.yaml` y Dockerfiles fijan imágenes/configuración; `configs/resources/` contiene presupuestos de jobs. Los reportes registran identidad efectiva de imagen, fuente y ejecución. No hay una sola imagen histórica común a todas las campañas: no construir sobre tags mutables y afirmar que eso reproduce sus bytes. El informe distingue el commit de cada evidencia de la revisión documental de entrega.

## Entregables completados y límites pendientes

| Elemento | Estado y evidencia |
| --- | --- |
| Aceptación sintética | 60 pruebas aprobadas, sin omisiones, en `8c8b730`; [reporte](../evidence/acceptance-local.json). No se atribuye esa ejecución a cambios posteriores. |
| Limpieza y recuperación local | Recursos temporales retirados y 22 contenedores originales restaurados; misma evidencia de aceptación. |
| Capturas de demo | [Galería](demo-gallery.md), con identidad y alcance; son capturas sintéticas, no una nueva instalación o restauración. |
| Tag académico | `entrega-academica-2026-10-24`; conserva la entrega original y sus limitaciones. |
| Corrección posterior de UI | [Regresión](../evidence/ui-metadata-fix-local.json) y [despliegue local verificado](ui-metadata-deployment.md); API/PostgreSQL conservados. |
| UI de mac24 | Despliegue operativo aceptado, sin rollback; regresión sintética y render contra API correctos, seis auxiliares saludables. [Auditoría](../evidence/ui-deployment-review-mac24.json). [Revisión visual en Chrome](../evidence/ui-visual-review-mac24.json) aceptada en lectura. |
| Concurrencia sostenida | No acreditada: T8-B permanece failed. H-cycle acredita sólo una construcción breve con tres auxiliares pausados; conservar la política de admisión de recursos. |

## Cómo continuar

Para presentar el proyecto, seguir [el recorrido del evaluador](demo.md): informe, selección de H, cobertura del temario, evaluación final, operación y demo sintética. No se necesita otra campaña de entrenamiento, evaluación test ni carga para presentar la evidencia existente.

La UI corregida de mac24 ya quedó activa. Conservar su override e imágenes y seguir las [notas de operación](mac24-ui-deploy.md); no volver a ejecutar el lanzador ni `--restore-only`. La revisión visual de lectura ya se realizó, sin registrar eventos ni refrescar perfiles; puede continuarse con la presentación del proyecto. El procedimiento local no autoriza copiar su `.env`, rutas, imágenes históricas o datos a otra máquina.

Se mantienen como límites declarados Linux/AMD64, otra cuenta de SO (requisito retirado por el usuario), servicio público multiusuario, alta disponibilidad e impacto comercial online. No son resultados demostrados por las pruebas existentes.

## Conservación de resultados

No repetir los adaptadores históricos `mac24_t8_*`, abrir nuevamente test, modificar umbrales ni convertir T8-B a passed. Los experimentos, ZIP y reservas originales permanecen intactos. Los Markdown versionados son la documentación de entrega; los planes históricos describen etapas anteriores y no constituyen una cola de comandos por ejecutar.

Preparación UI mac24 aceptada: `ui-recovery-5f15b3471c1a`; [auditoría y límites](../evidence/ui-recovery-review-mac24.json). La imagen histórica se reconstruyó y pasó imports/hash/health aislados; seis auxiliares recuperados y servicios originales conservados según el reporte remoto. Se conservan `blocked_capacity` y los dos intentos failed. La actualización posterior `ui-deploy-f37f5b3b0443` también quedó aceptada en su alcance operativo; ver [guía](mac24-ui-deploy.md). No repetir preparación, despliegue ni `--restore-only`.
