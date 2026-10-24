# Hallazgos consolidados y decisiones

Este índice reúne conclusiones y problemas resueltos sin convertir el historial en instrucciones de ejecución. Los detalles y números originales se conservan en los documentos/auditorías enlazados. [Informe completo](final-report.md) · [Mapa de entrega](handoff.md).

## Calidad del recomendador

| Hallazgo | Evidencia y consecuencia |
| --- | --- |
| La recencia importa en esta tarea | Popularidad7d superó a popularidad histórica y a las dos torres aisladas en la validación comparada. No demuestra causalidad ni inferioridad general de redes neuronales |
| La señal explícita del historial reciente mejora recompra | H utiliza los artículos comprados en 28d y respalda con popularidad7d. La torre de usuario original no recibía esa lista; es una explicación plausible de la diferencia, no un diagnóstico causal |
| Más entrenamiento no resolvió MAP/NDCG | Tres épocas empeoraron ambas frente a una, aunque mejoraron Recall/cobertura. [T3 ampliado](three-epochs.md) |
| Un grafo puede ordenar mejor y recuperar menos relevantes | G mejoró MAP medio +6.47%, pero Recall −4.39%, fuera del límite −1%. [Q2](graph-quality.md) |
| El promedio oculta regresiones importantes | RH mejoró MAP +4.03% con Recall igual, pero incumplió segmentos. RG logró mayor MAP (+14.48%), con pérdida de Recall y segmentos. [Q3](ranker-quality.md) |
| Significancia global no elimina una restricción incumplida | IC98.3333% positivos para G/RH/RG; ninguno elegible por todos los controles. Q4 seleccionó H antes del test. [Q4](quality-summary.md) |
| H no optimiza descubrimiento | Test: MAP H/P 0.02628451/0.00874768 y Recall 0.20463536/0.18077114, pero MAP de descubrimiento 0.00616/0.00872. [Auditoría final](../evidence/final-review-mac24.json) |
| Personalización y fallback deben distinguirse | Sin historial reciente, H devuelve popularidad. No inventar un motivo personalizado. Los clics/vistas se guardan, pero sólo compras cambian el ranking |

El [modelo seleccionado](model-card.md) no ajusta pesos; construye estado anterior al corte. PyTorch, LightGBM y los grafos siguen como experimentos académicos. No se sustituyó TensorFlow por otro framework para forzar un ganador. No se utiliza Hopsworks. Qdrant existe para experimentación y no participa en el servicio H.

## Infraestructura y diagnóstico

| Problema observado | Resolución y límite conservado |
| --- | --- |
| Límite fijo del job aunque Colima tuviera más RAM | Recursos parametrizados y registrados por fase. Cuota VM, cuota contenedor y RAM realmente observada son medidas distintas |
| Presión histórica de PostgreSQL a 512 MiB | Se conservaron identidades, picos y contadores; protocolos posteriores distinguen estado histórico e incrementos. Cero OOM observado no acredita margen de memoria |
| Diferencias UTC/monotónico y diagnóstico de relojes incompleto | Se conservaron los failed y se añadió protocolo operativo con ambos relojes. No se atribuyó una causa sin evidencia. [Relojes](clock-diagnostics.md), [T5 operativo](events-operational.md) |
| SSH/llavero Docker y plugins Buildx distintos | Preparación pública aisló credenciales innecesarias y verificó ejecutable/imagen; no deshabilitó TLS ni copió llaveros. Son incidentes macOS, no requisitos Linux |
| API con código distinto al checkout | Se alineó únicamente API con imagen verificada, conservando PostgreSQL/UI. Un git pull no actualiza contenedores por sí solo |
| Temporales sintéticos sin espacio suficiente | Tmpfs sintético ampliado después de reproducir el fallo; no alteró los datos/umbrales del test real |
| Inspección Docker fallaba si State.Health faltaba | Consulta robusta y recuperación controlada. Se conservó la reserva y sólo se continuó tras comprobar que el test no había arrancado |
| UI retenía metadatos antiguos en estado del widget | Se persistió ID del stream y se releyeron metadatos actuales. Regresión sintética, despliegue y revisión visual separados. [Auditoría UI](../evidence/ui-deployment-review-mac24.json) |
| El repo pasó a público y el publicador lo rechazaba | Publicación de reportes agregados admite ambas visibilidades; se publicó el ZIP existente sin repetir la carga |
| Cuotas de contenedores excedían RAM efectiva de Colima | Admisión por fase y pausas acotadas autorizadas. No extender esas autorizaciones históricas al nuevo servidor |

## Qué está probado y qué no

T4–T7 acreditan paquetes, paridad muestreada, publicación/rollback, eventos, dos ciclos programados y recuperación lógica. T7 restauró en otra ruta/proyecto de la **misma Mac**, con nuevas credenciales, seis consultas equivalentes y seis sin red externa. No demuestra traslado físico a Linux/AMD64 ni a otra cuenta de SO; el usuario retiró esa última prueba del alcance.

T8-A: 12,000 respuestas correctas a 20 solicitudes/s, p95 14.099 ms en localhost. T8-B quedó **failed** porque la construcción/solapamiento duraron menos de lo exigido; no se modificaron umbrales para aprobarla. H-cycle posterior pasó un protocolo distinto: construcción 6.821 s, 132 llegadas dentro de 6.580 s conservadores, p95 8.909 ms; global p95 14.151 ms. Esto acredita **un ciclo breve**, no concurrencia sostenida, capacidad máxima ni T8 global. [Auditoría H-cycle](../evidence/h-cycle-review-mac24.json).

T8-C evaluó H/P una vez sobre 68,984 compradores. T8-D incluye documentación, demo y tag. La aceptación sintética cerró 60/60 en una VM aislada; los cambios posteriores tienen evidencia propia. No volver a ejecutar campañas para generar capturas o presentar el proyecto.

## Nueva capa del servidor

Se añade Caddy con HTTPS y autenticación para el equipo; el algoritmo y sus resultados no cambian. Es una aplicación académica para operadores de confianza, sin autorización por comprador, SSO/MFA ni auditoría individual de eventos por identidad web. El acceso web autenticado no convierte la aplicación en un SaaS multiusuario.

La restauración real Linux, el dominio/DNS, el certificado público y la navegación desde otra red requieren ejecución en el destino. La preparación deja herramientas y controles; **no declara esos resultados como obtenidos**. La reproducción de servicio necesita un respaldo privado; el repositorio público sólo contiene código y agregados. El lock controla dependencias, pero no promete disponibilidad perpetua de registros ni imágenes bit a bit idénticas.

## Trabajo futuro, fuera de esta entrega

Sólo con una nueva hipótesis/protocolo: mejorar descubrimiento sin perjudicar recompra/arranque en frío; incorporar historia de secuencia a modelos aprendidos; comparar comunidades o HITS si se amplía el temario; medir concurrencia sostenida y carga HTTPS en hardware destino; separar dependencias de inferencia para reducir imágenes; añadir roles/MFA y trazabilidad individual si cambia la audiencia. No usar el test consumido para ajustar esas decisiones.
