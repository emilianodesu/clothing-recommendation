# H&M Fashion Recommender Platform
> Plataforma de recomendación personalizada de moda en tiempo real sobre datos masivos de compras, arquitectura contenerizada de microservicios y despliegue continuo GitOps.

[![Producción](https://img.shields.io/badge/Producción-En_Línea-success?style=flat-square)](https://hm.alanmagno.com/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?style=flat-square)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.66-FF4B4B?style=flat-square)](https://streamlit.io/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17.11-336791?style=flat-square)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square)](https://www.docker.com/)

---

## 🌐 Demostración en Producción

El sistema se encuentra desplegado y accesible públicamente con credenciales de operador en:  
👉 **[https://hm.alanmagno.com/](https://hm.alanmagno.com/)**

### Aspectos Destacados:
* **Motor H (Recent History 28d + Popularity 7d):** Motor de inferencia heurístico ultrarrápido y determinístico especializado en recompra con fallback inteligente por popularidad semanal.
* **Simulación en Tiempo Real:** Interfaz web interactiva para simular compras y observar la adaptación reactiva e instantánea del catálogo recomendado.
* **Perfiles Descriptivos Reales:** Modelos de compradores obtenidos de historiales reales del dataset oficial de H&M Kaggle (gamas cromáticas, prendas estampadas y patrones de recompra).
* **Arquitectura de Grado Producción:** Caddy Gateway perimetral con TLS automático, API REST asíncrona, PostgreSQL relacional para eventos y telemetría Prometheus.

---

## 🏛️ Arquitectura del Sistema

```mermaid
flowchart TD
    subgraph Internet_Layer ["Capa Perimetral (Pública)"]
        Client["Usuario / Operador Web"]
        DNS["Cloudflare DNS (hm.alanmagno.com)"]
    end

    subgraph Host_Server ["Servidor Host Linux (Producción)"]
        subgraph Gateway_Container ["Caddy Gateway"]
            Caddy["Caddy 2.11\n• Terminación TLS/HTTPS\n• HTTP Basic Auth Guard\n• Redirección 80 a 443\n• Reverse Proxy"]
        end

        subgraph Presentation_Container ["Frontend (Docker: hmrec-app)"]
            UI["Streamlit UI (Port 8501)\n• Layout Reactivo\n• Catálogo Paginado\n• Perfiles Reales de Demo\n• Simulación de Compra"]
            UICache["Disk Image Cache\n(/tmp/hmrec-images)"]
        end

        subgraph Backend_Container ["API Serving (Docker: hmrec-app)"]
            FastAPI["FastAPI Engine (Port 8000)\n• /recommend\n• /catalog\n• /articles/details\n• /articles/{id}/image\n• /events\n• /metrics\n• /health/*"]
            Metrics["Telemetría & Prometheus"]
        end

        subgraph Engine_Layer ["Motor In-Memory"]
            H_Engine["Algoritmo H\n• Historial Reciente (28d)\n• Popularidad Semanal (7d)\n• Fallback Determinístico"]
            ReleaseReg["ReleaseRegistry\n(Snapshot In-Memory Atómico)"]
        end

        subgraph Storage_Layer ["Persistencia (Docker: postgres:17)"]
            Postgres[("PostgreSQL 17.11\n• Event Streams\n• Serving Advisory Leases\n• Release Pointers")]
        end

        subgraph Deploy_Pipeline ["Pipeline GitOps (server-checks)"]
            DeployScript["hm-deploy.sh (Cron)\n1. Git Fetch origin/main\n2. Sandbox: pytest tests/unit\n3. BuildKit Build (jobs & app)\n4. Database Migrate\n5. Hot Container Restart\n6. local_check & check_public"]
        end
    end

    Client -->|"HTTPS :443"| DNS
    DNS --> Caddy
    Caddy -->|"Reverse Proxy :8501"| UI
    UI -->|"HTTP Interno :8000"| FastAPI
    FastAPI -->|"In-Memory Lookup"| ReleaseReg
    ReleaseReg --> H_Engine
    FastAPI -->|"Advisory Locks & Events"| Postgres
    UI -.->|"Caché Local de Imágenes"| UICache
    FastAPI -.->|"Telemetría"| Metrics
    DeployScript -.->|"Reconstrucción & Verificación"| Presentation_Container
    DeployScript -.->|"Reconstrucción & Verificación"| Backend_Container
```

---

## 🛠️ Requisitos Previos

* Docker Engine 24+ & Docker Compose v2
* Python 3.12 con [Astral `uv`](https://github.com/astral-sh/uv) (gestor de paquetes de alto rendimiento)

---

## 🚀 Inicio Rápido Local (Quickstart)

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/emilianodesu/clothing-recommendation.git
   cd clothing-recommendation
   ```

2. **Instalar dependencias del entorno:**
   ```bash
   uv sync --extra ui
   ```

3. **Iniciar los servicios con Docker Compose:**
   ```bash
   docker compose --profile serve up -d
   ```
   * Interfaz de Usuario (Streamlit): `http://localhost:8501`
   * Documentación de la API (Swagger/OpenAPI): `http://localhost:8000/docs`

---

## 🧪 Ejecución de Pruebas

* **Pruebas unitarias de motor y API (ejecutadas en sandbox):**
  ```bash
  uv run pytest tests/unit -v
  ```

* **Pruebas de integración de base de datos y UI:**
  ```bash
  uv run pytest tests/integration -v
  ```

---

## 📁 Estructura del Proyecto

```
clothing-recommendation/
├── compose.yaml              # Definición de servicios (postgres, api, ui, etc.)
├── pyproject.toml            # Definición del paquete hm-recommender y dependencias
├── docker/                   # Dockerfiles optimizados multi-stage con BuildKit
├── src/
│   └── hm_recommender/
│       ├── api/              # FastAPI application, routing, esquemas Pydantic y eventos
│       ├── ui/               # Streamlit application, estilos CSS e internacionalización
│       ├── model/            # Modelos de recomendación (heurística H y two-tower)
│       ├── ranking/          # Algoritmos determinísticos de desempate y deduplicación
│       ├── releases/         # Gestión de releases y leases de servicio
│       └── events/           # Almacenamiento y agregación de eventos de usuario
├── tests/
│   ├── unit/                 # Pruebas unitarias ultrarrápidas aisladas
│   ├── integration/          # Pruebas de integración HTTP y persistencia
│   └── fixtures/             # Datos sintéticos para pruebas
└── docs/                     # Documentación técnica, de operaciones y archivo histórico
```

---

## 🚢 Operación y Despliegue en Servidor

El despliegue en servidor se gestiona mediante el pipeline automatizado `server-checks/hm-deploy.sh`, que vigila `origin/main`, ejecuta pruebas unitarias en contenedor cerrado sin red, reconstruye imágenes con BuildKit y realiza validaciones locales y de certificados HTTPS antes de conmutar tráfico.

Para consultar los procedimientos detallados de diagnóstico, certificados y respaldos, consulte la [Guía de Operaciones del Servidor](docs/server-operations.md).
