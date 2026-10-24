# Referencias y procedencia

Los resultados numéricos del proyecto proceden de sus propias [auditorías](delivery-status.md), no de tutoriales. Las versiones ejecutadas están fijadas en `uv.lock`, Dockerfiles, Compose y reportes; una página `latest` sirve para documentación, no para actualizar dependencias automáticamente.

## Bibliografía de la asignatura

Referencias suministradas en el temario; mantener la edición exigida por el profesor en la entrega formal:

- Aggarwal, C. (2015). *Data Mining*. Springer International Publishing.
- Hastie, T., Tibshirani, R. y Friedman, J. (2008). *The Elements of Statistical Learning: Data Mining, Inference, and Prediction*. Springer.
- Han, J., Kamber, M. y Pei, J. (2012). *Data Mining*. Morgan Kaufmann/Elsevier.
- Leskovec, J., Rajaraman, A. y Ullman, J. (2016). *Mining of Massive Datasets*. Cambridge University Press.
- Stahlbock, R., Abou-Nasr, M. y Weiss, G. (2018). *Data Mining*. C.S.R.E.A.
- Witten, I., Frank, E., Hall, M. y Pal, C. (s.f.). *Data Mining*. Elsevier.

Recomendación, vínculos, evaluación y minería sustentan la [matriz del curso](course-coverage.md). Netflix es contraste conceptual de ratings explícitos; este proyecto evalúa compras implícitas y rankings temporales, no reproduce ese desafío.

## Datos y modelos

- [H&M Personalized Fashion Recommendations — Kaggle](https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/data): fuente autorizada del dataset. La descarga requiere acceso y términos propios; no se redistribuye el ZIP desde este repositorio.
- [Kaggle CLI](https://github.com/Kaggle/kaggle-cli): referencia oficial de autenticación/descarga; el proyecto valida su archivo mediante tamaño, estructura y huella.
- [PyTorch: reproducibilidad](https://docs.pytorch.org/docs/2.14/notes/randomness.html): contexto de semillas, plataforma y límites. Los experimentos de dos torres se conservan en `model/` y sus reportes T3.
- [TensorFlow Recommenders](https://www.tensorflow.org/recommenders): biblioteca de modelos de recomendación basada en Keras, considerada durante la exploración. **No es una dependencia del proyecto.** Usar PyTorch para un experimento no garantiza mejor calidad; la selección H se tomó por las métricas y restricciones documentadas.
- [Documentación LightGBM](https://lightgbm.readthedocs.io/): referencia del framework de reranking Q3; configuraciones y resultados concretos están en [ranker-quality](ranker-quality.md).

## Infraestructura

- [uv: locking y sincronización](https://docs.astral.sh/uv/concepts/projects/sync/): `--locked` falla si el lock no concuerda, evitando actualizarlo durante el build.
- [Docker Engine: instalación por sistema](https://docs.docker.com/engine/install/) y [firewall/puertos](https://docs.docker.com/engine/network/packet-filtering-firewalls/): preparación del destino según el SO detectado.
- [Buildx: driver docker-container](https://docs.docker.com/build/builders/drivers/docker-container/): constructor con cuotas propias de RAM/CPU.
- [Docker: múltiples plataformas](https://docs.docker.com/build/building/multi-platform/): distinguir imagen multiarch, construcción nativa y validación física.
- [Caddy: HTTPS automático](https://caddyserver.com/docs/automatic-https): dominio, DNS, puertos y almacenamiento persistente para TLS.
- [Caddy: basic_auth](https://caddyserver.com/docs/caddyfile/directives/basic_auth) y [CLI hash-password](https://caddyserver.com/docs/command-line#caddy-hash-password): autenticación del equipo con hashes y contraseña recibida por stdin.
- [Caddy: reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy): proxy HTTP/WebSocket a Streamlit. [Release 2.11.7](https://github.com/caddyserver/caddy/releases/tag/v2.11.7), imagen fijada por digest en `deploy/compose.gateway.yaml`.
- [MLflow Tracking Server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/), [Airflow LocalExecutor](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/local.html) y [Colima](https://github.com/abiosoft/colima): infraestructura experimental/local y pipeline histórico; no se publican sus interfaces en el nuevo servidor.

## Tutoriales y documentación de aprendizaje

El usuario propuso [freeCodeCamp en YouTube](https://www.youtube.com/@freecodecamp) como estilo de proyecto autocontenido. El repositorio conservado no contiene una lista verificable de vídeos concretos/duraciones de aquella búsqueda; no se inventan referencias ni se atribuye esta implementación a un curso específico. Para aprender el flujo de recomendación, TFRS conserva tutoriales oficiales; para operar **esta implementación**, las fuentes de verdad son [handoff](handoff.md), [despliegue](server-deployment.md), código, contratos y auditorías.

Los diseños originales con referencias están en [plataforma](superpowers/specs/2026-10-18-despliegue-local-reproducible-design.md) y [calidad](superpowers/specs/2026-10-20-calidad-temporal-grafos-ranking-design.md). Preservan el razonamiento histórico y no sustituyen el procedimiento vigente del servidor.
