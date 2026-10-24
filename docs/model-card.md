# Ficha del motor H

| Campo | Contrato |
| --- | --- |
| Nombre | Historial28d + popularidad7d, `recent-history-v1` |
| Selección | Q4 `quality-v1`, congelada antes de test |
| Uso | Recomendaciones comerciales locales basadas en compras; demostración académica |
| Entrenamiento | H no ajusta pesos; prepara estado anterior al corte |
| Entradas | Comprador, catálogo, compras anteriores, filtros opcionales |
| Orden | Historial por fecha/frecuencia/ID; respaldo por frecuencia/ID; deduplicar conservando primera aparición |
| Ventanas | 28 y 7 días; límite inferior inclusivo, corte superior exclusivo |
| Salida | Hasta 200 candidatos offline; API devuelve hasta 12 con ordinal, motivo y versión |
| Arranque en frío | Popularidad para ausencia de historial elegible; no inventa preferencias |
| Compras anteriores | Permitidas: recompra forma parte del objetivo |
| Servicio | API con estado H y PostgreSQL; no requiere inferencia PyTorch/Qdrant/MLflow |

La selección favorece restricciones de Recall y segmentos además de MAP. RG tiene mayor MAP medio en validación, pero no cumple los controles. No llamar a H «el mejor algoritmo posible».

[Resultados finales](final-evaluation.md): MAP@12 0.02628451, NDCG@12 0.03809567 y Recall@200 0.20463536 sobre 68,984 compradores. Mejora global frente a popularidad, concentrada en historia reciente y recompra. Empeora descubrimiento; los compradores sin historial reciente mantienen popularidad. No hay test de impacto online, calibración de probabilidades, evaluación causal ni garantía de calidad bajo filtros.

El dataset histórico no prueba vigencia comercial actual. La simulación de eventos no incorpora automáticamente compras futuras de la fuente. El estado servido conserva corte 2020-09-09; el corte del test fue 2020-09-16 y no se activó. No afirmar personalización cuando se usó fallback.

Capacidad observada: 20 solicitudes/s en localhost, p95 14.099 ms en T8-A. La concurrencia sostenida no fue acreditada. Operar trabajos pesados en mantenimiento secuencial con el protocolo revisado. API local de un operador, sin autorización por comprador para exposición pública.

Validación posterior [h-cycle-v1](mac24-h-cycle.md): un ciclo real de construcción
H de 6.821 s con la API activa, 132 llegadas en un intervalo conservador de 6.580 s
y p95 8.909 ms. Cumplió el protocolo con tres auxiliares pausados y recuperados;
no prueba concurrencia sostenida ni capacidad máxima y no cambia la selección H.

Conservar datos y eventos privados; sólo agregados por GitHub. Cambiar ventanas, reglas o candidato requiere una campaña futura predefinida y nuevos datos de evaluación: el test actual está consumido. Artefactos y comprobaciones: [selección](model-selection.md), [paquetes](releases.md), [auditoría final](../evidence/final-review-mac24.json).
