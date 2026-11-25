# Recomendador Híbrido `v2` y Experiencia de Comprador

**Estado:** implementación `v2` (`two_stage_lgbm_v2`) evaluada íntegramente en `data/processed/hm-v1` bajo el protocolo `quality-v2`, con paquete portable verificado `v2-d7861a4189e2e3e478dd16d521982fc2f4635fb60a56ce1b6d88629dcbbb4154` (`data_as_of = 2020-09-23`) y latencia online en CPU `< 40 ms` (`p95`). Los artefactos históricos de `v1` (**H**, **P**, **RG**, `quality-v1`, tag `entrega-academica-2026-10-24` y reportes previos) permanecen intactos.

---

## 1. Resumen Ejecutivo y Compuertas `quality-v2`

El motor `v2` evoluciona el baseline `v1` (`recent_then_popular`, **H**) hacia un pipeline de dos etapas diseñado para mejorar tanto la recompra como el descubrimiento personalizado, sin regresiones por segmento ni dependencias de `scikit-learn` en inferencia online:

| Compuerta (`quality-v2`) | Criterio | Valor medido | Estado |
| --- | --- | ---: | --- |
| `beat_h_map` | `MAP@12(v2) > MAP@12(H)` en `V1`–`V3` | `0.031591 > 0.022225` (`+42.14%`) | PASS |
| `beat_rg_map` | `MAP@12(v2) > MAP@12(RG)` en `V1`–`V3` | `0.031591 > 0.025444` (`+24.16%`) | PASS |
| `reached_target_map` | `MAP@12(v2) >= 0.031` en `V1`–`V3` | `0.031591 >= 0.031` | PASS |
| `beat_h_discovery` | `Discovery MAP@12(v2) > H` | `0.011221 > 0.004589` (`+144.48%`) | PASS |
| `beat_p_discovery` | `Discovery MAP@12(v2) > P` | `0.011221 > 0.006437` (`+74.30%`) | PASS |
| `zero_segment_regressions` | Pérdida relativa `<= 2%` (`>= 1,000` compradores) | `16/16` segmentos PASS (`0` regresiones) | PASS |
| `test_beat_h_map` | `MAP@12(v2) > MAP@12(H)` en `2020-09-16` | `0.034612 > 0.026285` (`+31.68%`) | PASS |

- **Configuración de evaluación (`configs/quality/quality-v2.json`)**: `config_sha256 = 3419756267314a42f8b359d5908628faf045c905f71500d98a57f84ddf85a01e`.
- **Dataset (`data/processed/hm-v1`)**: `dataset_id = hm-e4695ac063fd621a`, `dataset_manifest_sha256 = e04454c8ee54078fb495c4a0765d102b57d4438c3cd2839849b5603d303311a7` (`104,547` artículos activos en catálogo del paquete `v2-*`, `1,371,980` clientes, `6,975,679` filas de historial compacto).

---

## 2. Arquitectura de Dos Etapas y Experiencia en Vivo

```mermaid
flowchart LR
    U[Comprador en Streamlit] -->|view / click / purchase + onboarding| API[FastAPI + TwoStageState]
    API -->|POST /events + refresh| PG[(PostgreSQL interaction_events)]
    API -->|Stage 1: 5 generadores| C[Unión ~291 candidatos/usuario]
    C -->|61 features Float32 < cutoff| LGBM[LightGBM LambdaRank 63 árboles]
    LGBM -->|balanced / discovery + 7 razones| U
    ZIP[Archivo ZIP de imágenes] -->|ZipFile.getinfo O(1) + LRU| API
```

### 2.1 Etapa 1 — Recuperación Multi-Generador (`src/hm_recommender/v2/latent.py`, `retrieval.py`)

Implementada exclusivamente con `numpy` y `scipy.sparse` (sin `scikit-learn`), combinando cinco generadores complementarios sin truncado prematuro a 200 candidatos (`~291.49` candidatos únicos por usuario en promedio):

1. `history_decay`: compras de los últimos 180 días con decaimiento exponencial multiescala (`tau = 14d` + `tau = 60d`), tope 80.
2. `product_code_variants`: otros colores/variantes del mismo `product_code` comprado o marcado como favorito, ordenados por popularidad de 14 días, tope 60.
3. `cooccurrence`: co-compra en canasta semanal / ventana de 90 días con ponderación BM25 y decaimiento temporal, tope 80.
4. `latent_and_content`: SVD truncado (`k = 64` vía `scipy.sparse.linalg.svds`) con `fold_in_user` en `O(k)` para incorporar señales en vivo de sesión + similitud TF-IDF sobre metadatos y descripción del artículo, tope 60.
5. `segment_popularity`: popularidad de 7 días segmentada por `(age_bin, index_group_name)` y popularidad global de 7 días, tope 80.

### 2.2 Etapa 2 — Features Temporales y LightGBM LambdaRank (`features.py`, `ranker.py`)

- **61 features `Float32` estrictamente `< cutoff`** (`feature_version = 2`): estadísticas de usuario (con conversión defensiva `age` desde `pl.String`), popularidad y precio de artículo, afinidad precio/edad/canal/sección/color/tipo de producto, historial de `product_code` y puntajes/rangos de los cinco generadores de Etapa 1.
- **Ranker (`lightgbm==4.7.0`, `lambdarank`, `ndcg@12`)**: entrenado en 5 semanas deslizantes (4 semanas de entrenamiento + 1 semana de validación con *early stopping* y refit final; `63` árboles, `num_leaves = 63`, `val_ndcg_at_12 = 0.057410`).
- **Enrutamiento *cold-start***: usuarios sin historial de compras (`user_count180 == 0`) y sin eventos en vivo se enrutan directamente a `segment_popularity` (personalizada por `age_bin` e `index_group_name` si el visitante completa el onboarding) o `popular_7d`.

### 2.3 Serving Online y Experiencia Real de Comprador (`api/`, `ui/`)

- **Señales en vivo**: `POST /streams/{stream_id}/recommend` pondera eventos recientes de sesión (`purchase = 1.0`, `click` / favorito `= 0.55`, `view = 0.25`) mediante `fold_in_user` y actualización de candidatos tras `POST /admin/streams/{stream_id}/refresh`.
- **Modos `balanced` vs `discovery`**: en `balanced` se permiten recompras y variantes; en `discovery` (*Solo descubrimiento*) se excluyen artículos ya comprados y se limita a 1 variante por `product_code`.
- **Explicabilidad (7 razones reales)**: `recent_purchase`, `same_product_variant`, `frequently_bought_together`, `latent_style_match`, `content_similar`, `popular_for_segment` y `popular_7d`.
- **UI Streamlit y caché de imágenes**: onboarding rápido de visitante nuevo (`onboard_group`, `age_bin`), acciones por prenda (`Ver detalle`, `♡ Favorito`, `Comprar`), búsqueda `q` y filtro `index_group_name` en `/catalog` con `@st.cache_data` (una sola llamada `/catalog` por estado), IDs completos de 10 dígitos, panel de actividad en sesión y `ImageCache` con búsqueda `O(1)` vía `ZipFile.getinfo()`.

Véanse los detalles de la base previa en [arquitectura v1](architecture.md), [servicio](serving.md) y [eventos](events.md).

---

## 3. Resultados Offline `quality-v2` (`V1`, `V2`, `V3` y Promedio)

Los resultados siguientes provienen directamente de `artifacts/quality-v2/quality-v2-report.json` (`generated_at_utc = 2026-11-25T04:54:55Z`).

### 3.1 Promedio Ponderado Igual (`V1`–`V3`)

| Método | MAP@12 | NDCG@12 | Recall@200 | Recall@300 | Discovery MAP@12 | Repeat MAP@12 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **V2 (`two_stage_lgbm_v2`)** | **0.031591** | **0.047518** | **0.224704** | **0.242470** | **0.011221** | **0.356586** |
| **RG** (referencia Q3 `v1`) | 0.025444 | 0.038512 | 0.186691 | — | 0.007556 | 0.301986 |
| **H** (`recent_then_popular`) | 0.022225 | 0.033009 | 0.195269 | 0.195269 | 0.004589 | 0.294963 |
| **P** (`popular_7d`) | 0.006722 | 0.013326 | 0.174696 | 0.174696 | 0.006437 | 0.010884 |

- **Ganancia de `v2` en `MAP@12`**: `+42.14%` (`+0.009366`) frente a **H**, `+24.16%` (`+0.006147`) frente a **RG**, y `+369.99%` (`+0.024869`) frente a **P**.
- **Ganancia de `v2` en `Discovery MAP@12`**: `+144.48%` (`+0.006631`) frente a **H**, `+74.30%` (`+0.004783`) frente a **P**, y `+48.50%` (`+0.003665`) frente a **RG**.
- **Etapa 1 (`V1`–`V3`)**: promedio de `291.49` candidatos/usuario con `stage1_union_recall = 0.223396`.

### 3.2 Desglose por Fold (`V1`, `V2`, `V3`)

| Fold (ventana) | Compradores | Método | MAP@12 | NDCG@12 | Recall@200 | Recall@300 | Discovery MAP@12 | Repeat MAP@12 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **V1** (`2020-08-26` → `2020-09-02`) | 80,253 | **V2** | **0.028442** | **0.044045** | **0.222283** | **0.239240** | **0.011032** | **0.328959** |
| **V1** | 80,253 | H | 0.019364 | 0.029367 | 0.193452 | 0.193452 | 0.004569 | 0.267115 |
| **V1** | 80,253 | P | 0.006828 | 0.013367 | 0.176185 | 0.176185 | 0.006544 | 0.010687 |
| **V2** (`2020-09-02` → `2020-09-09`) | 75,822 | **V2** | **0.033068** | **0.049373** | **0.229279** | **0.247793** | **0.011669** | **0.366577** |
| **V2** | 75,822 | H | 0.022721 | 0.034167 | 0.201570 | 0.201570 | 0.004636 | 0.294125 |
| **V2** | 75,822 | P | 0.006732 | 0.014007 | 0.180324 | 0.180324 | 0.006528 | 0.010514 |
| **V3** (`2020-09-09` → `2020-09-16`) | 72,019 | **V2** | **0.033263** | **0.049135** | **0.222549** | **0.240377** | **0.010961** | **0.374222** |
| **V3** | 72,019 | H | 0.024589 | 0.035493 | 0.190786 | 0.190786 | 0.004563 | 0.323648 |
| **V3** | 72,019 | P | 0.006604 | 0.012605 | 0.167579 | 0.167579 | 0.006240 | 0.011452 |

### 3.3 Bootstrap Pareado por Cliente (2,000 Réplicas, IC 95%)

Muestreo por conglomerado de cliente (`seed = 42`, `190,944` compradores únicos en `V1`–`V3`, `0` réplicas descartadas por fold vacío):

| Contraste | Métrica | Delta absoluto | IC 95% |
| --- | --- | ---: | --- |
| `v2 - H` | `MAP@12` | `+0.00936608` | `[0.00903977, 0.00970899]` |
| `v2 - H` | `NDCG@12` | `+0.01450898` | `[0.01411336, 0.01491875]` |
| `v2 - H` | `Recall@200` | `+0.02943442` | `[0.02839352, 0.03049091]` |
| `v2 - P` | `MAP@12` | `+0.02486918` | `[0.02435529, 0.02538878]` |
| `v2 - P` | `NDCG@12` | `+0.03419190` | `[0.03358573, 0.03482755]` |
| `v2 - P` | `Recall@200` | `+0.05000754` | `[0.04884302, 0.05120702]` |

---

## 4. Evaluación por Segmentos (16/16 Sin Regresiones)

Todos los segmentos con `>= 1,000` compradores en `V1`, `V2`, `V3` y la ventana descriptiva `TEST` (`2020-09-16`) superan a **H** con mejoras entre `+13.22%` y `+163.99%` (`0` regresiones `> 2%`):

| Fold | Segmento | Compradores | H MAP@12 | V2 MAP@12 | Diferencia rel. vs H |
| --- | --- | ---: | ---: | ---: | ---: |
| `V1` | `recent28d` | 34,968 | 0.036309 | 0.046805 | `+28.91%` |
| `V1` | `history29_90d` | 24,612 | 0.006708 | 0.016783 | `+150.18%` |
| `V1` | `older_history` | 14,733 | 0.006118 | 0.012269 | `+100.52%` |
| `V1` | `no_history` | 5,940 | 0.004894 | 0.008760 | `+78.99%` |
| `V2` | `recent28d` | 33,437 | 0.043551 | 0.056344 | `+29.37%` |
| `V2` | `history29_90d` | 22,595 | 0.006503 | 0.017117 | `+163.20%` |
| `V2` | `older_history` | 14,076 | 0.006098 | 0.013032 | `+113.69%` |
| `V2` | `no_history` | 5,714 | 0.005912 | 0.009292 | `+57.17%` |
| `V3` | `recent28d` | 32,414 | 0.046707 | 0.057986 | `+24.15%` |
| `V3` | `history29_90d` | 20,970 | 0.005830 | 0.015390 | `+163.99%` |
| `V3` | `older_history` | 13,240 | 0.007566 | 0.011064 | `+46.23%` |
| `V3` | `no_history` | 5,395 | 0.006393 | 0.008674 | `+35.69%` |
| `TEST` (`2020-09-16`) | `recent28d` | 31,095 | 0.048032 | 0.059715 | `+24.32%` |
| `TEST` (`2020-09-16`) | `history29_90d` | 19,422 | 0.009099 | 0.016332 | `+79.49%` |
| `TEST` (`2020-09-16`) | `older_history` | 12,895 | 0.007679 | 0.012713 | `+65.55%` |
| `TEST` (`2020-09-16`) | `no_history` | 5,572 | 0.007878 | 0.008920 | `+13.22%` |

---

## 5. Evaluación Descriptiva Única sobre `2020-09-16`

Ventana `[2020-09-16, 2020-09-23)` (`artifacts/quality-v2/test-2020-09-16-report.json`): `68,984` compradores, `103,880` artículos activos, `213,728` pares relevantes (`8,334` fuera de catálogo conservados en los denominadores, techo `0.963461`).

| Método | MAP@12 | NDCG@12 | Recall@200 | Recall@300 | Discovery MAP@12 | Repeat MAP@12 | Artículos efectivos (top-12) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **V2 (`two_stage_lgbm_v2`)** | **0.034612** | **0.051361** | **0.239321** | **0.258400** | **0.011945** | **0.376935** | **511.38** (`27.38%` top-10) |
| **H** (`recent_then_popular`) | 0.026285 | 0.038096 | 0.204635 | 0.204635 | 0.006156 | 0.325320 | 55.08 (`73.00%` top-10) |
| **P** (`popular_7d`) | 0.008748 | 0.015757 | 0.180771 | 0.180771 | 0.008722 | 0.007697 | — (`83.33%` top-10) |

- **Diferencias relativas en `2020-09-16`**: `v2` supera a **H** en `+31.68%` de `MAP@12` y `+94.04%` de `Discovery MAP@12`, y supera a **P** en `+295.67%` de `MAP@12` y `+36.96%` de `Discovery MAP@12`.
- **Diversidad y personalización**: **V2** genera `56,934` listas ordenadas únicas con cobertura `13.63%` (`14,156` artículos) y `511.38` artículos efectivos frente a `55.08` de **H** (donde el top-10 concentra `73.00%` de exposiciones). Comparar con la evaluación histórica en [evaluación final v1](final-evaluation.md).

---

## 6. Latencia Online y Consumo de Memoria en CPU (`< 40 ms`)

Mediciones verificadas en Docker sobre el paquete real `v2-d7861a4189e2e3e478dd16d521982fc2f4635fb60a56ce1b6d88629dcbbb4154` dentro de `hmrec-app:0.1.0` con límites `--memory=4g --cpus=2` (`'sklearn' not in sys.modules`):

| Escenario medido | p50 (ms) | p95 (ms) | p99 (ms) | Cumple `< 40 ms` |
| --- | ---: | ---: | ---: | --- |
| `TwoStageState.recommend` (60 compradores estratificados, directo) | `5.918` | `8.605` | `9.359` | Sí |
| `TwoStageState.recommend` (`discovery`, N=60, directo) | `5.941` | `7.629` | `7.775` | Sí |
| `TwoStageState.recommend` (*cold-start* con preferencias, N=60) | `0.558` | `0.636` | `0.690` | Sí |
| `TwoStageState.recommend_stream` (señales en vivo, N=60) | `5.332` | `6.257` | `6.341` | Sí |
| HTTP `POST /recommend` (`TestClient`, N=60) | `9.405` | `14.742` | `17.815` | Sí |
| HTTP `POST /streams/{id}/recommend` (entorno compose `OMP=2`, N=50) | `20.995` | `25.133` | `26.946` | Sí |
| HTTP `GET /catalog` en caché (`104,547` artículos, `0` lecturas Parquet) | `14.998` | `21.700` | — | Sí |

- **Uso de memoria bajo `--memory=4g`**: tiempo de carga `7.775 s`, `VmRSS` estable `1,611.33 MiB`, pico de cgroup `3,230.02 MiB` (`3,251.17 MiB` en entorno compose con caché de catálogo caliente), por debajo del límite de `4,096 MiB`.

---

## 7. Referencias y Documentación Relacionada

- [Informe final v1](final-report.md)
- [Selección de modelos v1](model-selection.md)
- [Resumen Q4 v1](quality-summary.md)
- [Evaluación final test v1](final-evaluation.md)
- [Ficha del motor v1](model-card.md)
- Artefactos locales de referencia: `artifacts/quality-v2/quality-v2-report.json`, `artifacts/quality-v2/test-2020-09-16-report.json`, `configs/quality/quality-v2.json` y `src/hm_recommender/v2/`.
