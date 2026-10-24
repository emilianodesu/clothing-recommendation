# Traspaso al compañero y su agente

La implementación académica y sus resultados están disponibles. Esta entrega añade la ruta para instalar una copia en un servidor Linux, accesible al equipo mediante HTTPS y cuentas individuales. **El próximo trabajo es desplegar y comprobar el servidor real**, no volver a investigar modelos.

## Orden de lectura y trabajo

1. Leer [AGENTS](../AGENTS.md) y [despliegue](server-deployment.md).
2. Ejecutar `python3 scripts/server.py doctor`; identificar SO/arquitectura, Docker, RAM/disco, puertos y rutas/VPN. El usuario no conoce esos recursos y delega averiguarlos.
3. Obtener dominio/DNS y un respaldo privado con SHA-256; no confundir reportes GitHub con paquetes de datos. Construir en checkout fijado y motor dedicado, restaurar a ruta nueva, verificar API y abrir UI local.
4. Configurar usuarios individuales, DNS/HTTPS, comprobar desde una red externa y navegar. Entregar URL y credenciales por canal privado. No publicar contraseñas ni respaldo.
5. Documentar resultado, imágenes y pendientes en el formato de [operación](server-operations.md). Linux/AMD64 no queda probado por las auditorías mac24.

No hace falta otra cuenta de macOS, Hopsworks, una cuenta de nube, GPU, TensorFlow o un nuevo entrenamiento. La autorización de acceso web cubre a operadores de confianza del equipo; todos pueden registrar eventos y actualizar perfiles de simulación.

## Lo incluido y lo externo

| Incluido en Git | El receptor debe obtener o generar |
| --- | --- |
| Código, migraciones, Dockerfiles, lock Python, imágenes base fijadas, configuración de recursos | Docker Engine/Compose/Buildx y almacenamiento en su Linux |
| Diagnóstico, build, restore, pin de imágenes, proxy HTTPS y gestión de usuarios | Dominio bajo su control, DNS apuntando al servidor, 80/443 y acceso de administración |
| Recomendador H y contratos de paquetes/restauración | Respaldo lógico **privado**, o una reconstrucción separada con Kaggle si no puede obtenerse |
| Soporte de catálogo con imágenes desde ZIP | ZIP Kaggle propio de ~30.8 GB, opcional para fotografías; el backup no lo contiene |
| Documentación/evidencia de experimentos, pruebas sintéticas y capturas | Datos, checkpoints PyTorch, tablas de evaluación y artefactos completos permanecen en mac24 |
| Configuración de auth por hashes y guía de respaldo | Contraseñas nuevas por integrante, almacenamiento privado y copia externa del backup |

La restauración soportada conserva el estado del respaldo elegido; no promete incorporar interacciones posteriores a su fecha. El respaldo T7 probado es una instantánea, no un enlace vivo a mac24. La reproducción estadística de campañas y el despliegue del motor son actividades distintas.

## Estructura

| Ruta | Responsabilidad |
| --- | --- |
| `src/hm_recommender/data/` | Descarga, verificación e ingestión particionada |
| `src/hm_recommender/model/`, `quality/` | Dos torres, historia/popularidad, grafos, rankers y evaluaciones |
| `src/hm_recommender/releases/`, `portability/` | Paquetes verificables, exportación, respaldo/restauración lógica |
| `src/hm_recommender/api/`, `events/`, `ui/` | API, streams/eventos, publicación de perfiles e interfaz |
| `src/hm_recommender/pipeline/`, `dags/` | Preparación H y refresco programado; no iniciar DAG automáticamente en servidor |
| `configs/`, `migrations/`, `docker/`, `compose.yaml`, `uv.lock` | Contratos, base de datos y runtime reproducible |
| `scripts/server*.py`, `deploy/` | Entrada del servidor y gateway independiente |
| `scripts/mac24_*`, otros lanzadores experimentales | Procedimientos históricos especializados, conservar sin ejecutar |
| `tests/host/`, `tests/integration/`, `tests/unit/` | Pruebas; consultar requisitos de cada suite |
| `evidence/` | Agregados auditados y huellas; no contienen los artefactos para servir |
| `docs/` | Informe, cobertura, hallazgos, referencias y procedimientos |
| `data/`, `artifacts/`, `.runtime/`, `.env` | Generados y privados; ignorados por Git |

## Modelos, resultados y planes

El [informe](final-report.md) reúne metodología y resultados. [Selección](model-selection.md), [ficha H](model-card.md), [hallazgos](findings.md) y [temario](course-coverage.md) explican por qué se sirve H y se conservan los otros modelos como experimentos. [Referencias](references.md) reúne bibliografía y fuentes.

| Plan / diseño | Estado y uso |
| --- | --- |
| [Plataforma T1–T8](superpowers/plans/2026-10-18-plataforma-local-hm.md) | Implementada; alcance de cierre en delivery-status, T8-B failed preservado |
| [Entrenamiento dos Macs](superpowers/specs/2026-10-19-entrenamiento-dos-macs-design.md) | Histórico ejecutado; no es instalador del servidor |
| [Calidad Q1–Q4](superpowers/plans/2026-10-20-calidad-temporal-grafos-ranking.md) | Cerrada; no reabrir test ni ajustar selección |
| [T5 operativo](superpowers/plans/2026-10-22-t5-operational.md) | Cerrado tras preservar fallos de memoria/relojes |
| [UI mac24](mac24-ui-deployment-plan.md) | Desplegada y revisada; no reutilizar sus IDs en Linux |
| [H-cycle](superpowers/plans/2026-10-24-h-cycle.md) | Un ciclo breve aceptado; no carga sostenida |
| [Servidor](superpowers/plans/2026-10-24-server-handoff.md) | Herramientas preparadas; aceptación del destino físico pendiente |

Las auditorías principales están enlazadas en [delivery-status](delivery-status.md). Las notas antiguas en documentos especializados conservan el contexto temporal; README y AGENTS actuales prevalecen. El [archivo de instrucciones](history/AGENTS-2026-10-24.md) permite reconstruir decisiones sin tratar comandos antiguos como tareas pendientes.
