# Entrega académica — 2026-10-24

Tag de entrega: `entrega-academica-2026-10-24`. Es una instantánea del código, documentación y evidencia, no una declaración de que todos los ensayos pasaron.

## Material entregado

- [Informe final](final-report.md), [ficha H](model-card.md) y [mapa del curso](course-coverage.md).
- [Instalación](setup.md), [operación](operations.md), [restauración](reproducibility.md) y [guion](demo.md).
- [Galería de demo sintética](demo-gallery.md), con identidad de imagen, fuentes y huellas.
- [Aceptación sintética](acceptance.md): 60 pruebas aprobadas, cero omisiones; código probado en `8c8b730`, posteriores cambios de entrega sólo documentales.
- Evaluación final H/P y campañas cerradas conservadas en `evidence/`, con ZIP originales referenciados en las auditorías.

## Límites que acompañan la entrega

T8-B conserva **failed** por solapamiento insuficiente: no se acredita concurrencia sostenida. Se documenta operación secuencial y no se declara aceptación global de todos los criterios T8. T8-D documental/visual queda entregado; T8-A y T8-C ya estaban cerrados.

La galería usa la demo histórica en ejecución; no es un despliegue del tag actual. Su cabecera de revisión quedó desactualizada después de publicar perfiles aunque el ranking visible cambió. No se grabó instalación o restauración nueva: se entrega su evidencia histórica auditada y el recorrido visual de recomendación/evento/actualización.

Linux/AMD64, otra cuenta de SO, servicio público multiusuario, alta disponibilidad y impacto comercial online no están acreditados. No se vuelve a abrir el test para mejorar resultados. Los datos, respaldos, secretos y checkpoints privados no forman parte de este tag; reproducir H&M requiere acceso propio autorizado o artefactos privados válidos.

No es necesario repetir campañas para presentar esta entrega. Cualquier ampliación debe definir objetivo y evidencia nueva, conservando estos resultados.

## Corrección posterior al tag: metadatos de interfaz

Se corrigió en código la cabecera desactualizada: el selector ahora conserva únicamente el ID del stream y obtiene revisión, corte, versión y fechas de la respuesta actual. [Regresión](../evidence/ui-metadata-fix-local.json) con Streamlit real y HTTP simulado: falló antes y pasó después, preservando selección en dos actualizaciones. Suite host: 245 aprobadas y una omitida. La prueba se incorpora a events/acceptance para futuras imágenes.

Esta corrección es posterior al tag de entrega y no altera sus capturas. No se reconstruyó ni recreó la UI existente y no se tocó mac24. La aceptación completa de 60 casos corresponde al commit anterior; no se atribuye automáticamente a este cambio.

## Despliegue posterior verificado

La corrección `e478f71` ya está [desplegada en la UI sintética local](ui-metadata-deployment.md). Navegador real confirmó revisión 4→5 y fechas nuevas; API/PostgreSQL intactos. Mac24 no se actualizó y el tag de entrega conserva la evidencia histórica original.
