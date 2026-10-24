# H&M · Plataforma reproducible de recomendaciones

Proyecto final de Datos Masivos: datos masivos de compras, evaluación temporal, experimentos de grafos y modelos, API de recomendaciones, eventos, pipeline y recuperación. **El motor desplegado es H: historial de 28 días + popularidad de 7 días**, seleccionado por calidad bajo restricciones. No necesita entrenamiento neuronal ni Hopsworks para servir.

## Desplegar en el servidor del equipo

**Comenzar por [la guía del agente receptor](docs/server-deployment.md).** Incluye diagnóstico automático, construcción con recursos acotados, restauración, cuentas individuales del equipo, dominio/HTTPS, verificación y operación.

```sh
python3 scripts/server.py doctor
```

Este comando sólo consulta el equipo. No instala, detiene servicios, descarga datos ni inicia entrenamientos. El agente debe identificar Linux ARM64/AMD64, Docker, recursos, puertos y red antes de continuar. El servidor real y su dominio aún no se han validado.

El repositorio puede ser público. Contiene código, documentación, configuraciones y reportes agregados; **no contiene los datos H&M, paquetes con compradores, respaldos, tokens ni contraseñas**. La vía preparada restaura un respaldo privado autorizado en una instalación nueva. Las imágenes de artículos requieren el ZIP Kaggle propio; sin él se muestran sustitutos. El acceso web es para operadores de confianza del equipo, con usuario/contraseña sobre HTTPS; no es un portal público de compradores ni tiene roles individuales dentro de la aplicación.

## Resultado y entregables

| Lectura | Contenido |
| --- | --- |
| [Traspaso y mapa del proyecto](docs/handoff.md) | Qué recibe el compañero, entradas que debe obtener, estructura e índice de evidencia/planes |
| [Informe académico](docs/final-report.md) | Metodología, comparaciones, métricas, arquitectura y hallazgos |
| [Selección de modelos](docs/model-selection.md) / [ficha H](docs/model-card.md) | Por qué se eligió H y qué limitaciones tiene |
| [Cobertura del temario](docs/course-coverage.md) | Subtemas implementados y no implementados |
| [Hallazgos consolidados](docs/findings.md) | Resultados negativos, incidencias y conclusiones operativas |
| [Estado de entrega](docs/delivery-status.md) | Auditorías y alcance vigente |
| [Operación del servidor](docs/server-operations.md) | Usuarios, respaldos, actualización, recuperación y diagnóstico |
| [Referencias](docs/references.md) | Bibliografía, fuentes técnicas y recursos de aprendizaje |
| [Demo y capturas](docs/demo.md) / [galería](docs/demo-gallery.md) | Demostración sintética de eventos/recomendaciones |

Test final, 68,984 compradores: MAP@12 H **0.02628451** / popularidad **0.00874768**; Recall@200 **0.20463536 / 0.18077114**. La mejora se concentra en recompra; H pierde en descubrimiento. RG obtuvo mayor MAP medio en validación, pero incumplió restricciones. H no es un óptimo universal.

T8-A y T8-C están cerradas; T8-D entregada. **T8-B conserva failed** por solapamiento insuficiente. El [H-cycle posterior](evidence/h-cycle-review-mac24.json) acredita un ciclo breve bajo carga, no concurrencia sostenida ni aprobación global T8. UI corregida desplegada y revisada en mac24. No hay una nueva campaña/test pendiente para desplegar una copia.

## Desarrollo y conservación

Host: Python estándar; runtime: Python 3.12 en imágenes con `uv.lock`. `compose.yaml` conserva los perfiles de experimentación y servicio. `deploy/compose.gateway.yaml` añade exclusivamente el gateway. `scripts/server.py` es la entrada del nuevo servidor; los runners `mac24_*` son procedimientos históricos, no instaladores genéricos.

```sh
python3 -m unittest discover -s tests/host
```

Algunas pruebas de integración requieren Docker aislado y capacidad admitida: consultar [aceptación](docs/acceptance.md). No ejecutar campañas cerradas ni `verify --suite acceptance` en una instalación con datos. No borrar volúmenes ni reservas para superar comprobaciones.

El tag `entrega-academica-2026-10-24` conserva la entrega original; la preparación del servidor es posterior. Los [README](docs/history/README-2026-10-24.md) e [instrucciones](docs/history/AGENTS-2026-10-24.md) cronológicos se preservan como antecedentes, sin órdenes operativas vigentes.
