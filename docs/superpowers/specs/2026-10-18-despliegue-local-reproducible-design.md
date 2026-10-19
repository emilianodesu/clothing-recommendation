# Diseño de ejecución local y reproducción del proyecto H&M

Actualizado el 18 de octubre de 2026. Complementa la propuesta académica y sustituye sus decisiones anteriores de despliegue en nube. Este documento define requisitos para la implementación; los comandos indicados más adelante todavía no existen.

**Estado actualizado después de preparar Docker:** Compose y Colima están comprobados con 4 CPU, 8 GiB y disco virtual de 100 GiB; se añadió el montaje del proyecto conservando los anteriores. Ya existen los scripts de preparación, diagnóstico y prueba aislada descritos en [docs/setup.md](../../setup.md). El [plan de implementación](../plans/2026-10-18-plataforma-local-hm.md) concreta tareas, entregables y calendario; los comandos del producto continúan pendientes.

## Decisión y estado del proyecto

Construiremos una plataforma de recomendaciones con PyTorch y servicios locales mediante Docker Compose. Usaremos el equipo disponible, sin contratar nube, GPU, bases de datos administradas ni servicios de pago. El sistema deberá poder instalarse bajo otro usuario, en otra ruta y en otra computadora compatible, sin Codex ni credenciales del autor.

Los datos y la dirección técnica permiten comenzar. La aplicación, sus contenedores, su instalador y las pruebas de portabilidad del producto están pendientes. La preparación de Docker sí está implementada y verificada. Por tanto, todavía no hay un recomendador listo para desplegar ni evidencia de rendimiento del modelo.

| Elemento | Evidencia al revisar el espacio de trabajo | Estado |
| --- | --- | --- |
| Dataset H&M | ZIP completo y dos descargas con la misma huella; validación integral guardada | Disponible |
| Validación de datos | Notebook ejecutado y 16 controles satisfactorios | Disponible |
| Motor personalizado | Sólo existe una prueba funcional de popularidad | Por implementar |
| Código y control de versiones | Esta carpeta no es aún un repositorio Git | Por implementar |
| Docker | Cliente, motor Colima, Compose, Buildx y prueba funcional comprobados | Preparado en macOS ARM64 |
| Pipeline y servicios | Sólo hay Dockerfile y Compose de prueba; faltan contenedores de aplicación, API, modelo y DAG | Por implementar |
| Reproducibilidad | El entorno temporal depende del Python de Codex | Por corregir en la implementación |
| Entrenamiento completo y latencia | No se han medido | Por validar |

## Datos disponibles y acceso desde otra cuenta

La evidencia se encuentra en `validation_hm/resultado_validacion.json`, `validation_hm/manifiesto_kaggle.json`, `validation_hm/README.md` y `notebooks/validacion_hm_temporal.ipynb`. La última validación terminó el 1 de octubre de 2026 a las 04:28 UTC, equivalente al 30 de septiembre por la noche en Ciudad de México. En esta revisión se comprobó que el ZIP sigue presente con el tamaño esperado; no se volvió a ejecutar la validación completa.

| Propiedad | Resultado validado |
| --- | --- |
| ZIP | 30,810,293,747 bytes, aproximadamente 28.69 GiB |
| Archivos | 105,104, correspondientes a 4 CSV y 105,100 JPEG |
| Transacciones | 31,788,324 |
| Clientes | 1,371,980 |
| Artículos | 105,542 |
| Periodo | 2018-09-20 a 2020-09-22 |
| Integridad | Inventario remoto contrastado, CRC de todos los archivos y decodificación de todas las imágenes |

SHA-256 del ZIP validado:

```text
e4695ac063fd621a29ba83523aa97be4041a91c45fba331162784f55771fbe9c
```

Los 442 artículos sin imagen son una característica del conjunto original. La interfaz tendrá una imagen de sustitución. Los valores ausentes en atributos se tratarán explícitamente; la validación de integridad no implica que todas las columnas estén completas.

Para descargar H&M, cada participante deberá obtener acceso con su propia cuenta de Kaggle, aceptar las condiciones requeridas y configurar su propio token fuera del repositorio. El token se facilitará únicamente al proceso de descarga. Habrá también una entrada por ZIP local obtenido legítimamente, con verificación del manifiesto y la huella. No se copiarán las credenciales del autor. [R1]

El repositorio contendrá un generador determinista de datos sintéticos con el mismo esquema. Permitirá instalar, entrenar un modelo pequeño y probar el sistema sin una cuenta de Kaggle. Esta demostración no sustituirá el entrenamiento ni la evaluación académica con H&M completo. Los datos reales, perfiles y paquetes derivados se mantendrán fuera del repositorio público y se transferirán sólo cuando su uso y acceso estén autorizados.

## Arquitectura local

Se conserva un único modelo principal de dos torres en PyTorch. El tema 4.1–4.5 del curso es la cobertura comprometida. La extensión de grafos y comunidades permanece opcional, después de completar la operación y evaluación del sistema principal. No se necesitan TensorFlow, TensorFlow Recommenders, Hopsworks ni TorchRec para la base.

| Componente | Implementación local acordada |
| --- | --- |
| Preparación | Polars y Parquet por particiones; lectura por lotes |
| Modelo | PyTorch en CPU, dos torres y vectores iniciales de 64 dimensiones |
| Orquestación | Airflow con LocalExecutor, un trabajo pesado simultáneo |
| Experimentos | MLflow autohospedado; PostgreSQL para metadatos y volumen local para artefactos |
| Recuperación | Qdrant local, producto punto y colección específica por versión |
| Datos operativos | PostgreSQL; bases y usuarios separados para aplicación, Airflow y MLflow |
| Servicio | FastAPI, modelo precargado y fallback de popularidad |
| Interfaz | Streamlit, doce recomendaciones y registro de eventos |
| Empaquetado | Docker Compose; imágenes construidas localmente o importadas de un paquete |

El manifiesto de cada versión enlazará pesos, vocabularios, transformaciones, catálogo, perfiles iniciales, vectores y colección. La API resolverá cada solicitud con una sola versión compatible. Publicar una versión será una operación verificable y reversible; un entrenamiento fallido no alterará la versión activa.

Se usarán volúmenes persistentes para PostgreSQL, Qdrant y artefactos de MLflow. El servidor MLflow entregará los artefactos por HTTP, evitando que el cliente necesite la ruta absoluta del disco del servidor. El directorio de datos será configurable; los contenedores verán rutas internas estables. [R2]

Airflow ejecutará el paquete de trabajos dentro de su propia imagen de ejecución, con las dependencias CPU necesarias instaladas y verificadas. Se fijará el entorno de Airflow por separado del servicio web y se comprobará su compatibilidad con el código de entrenamiento. LocalExecutor ejecutará procesos locales con concurrencia limitada; no se requiere Celery, Redis ni acceso al socket de Docker. Una CLI invocará los mismos trabajos para depuración y recuperación manual. [R3]

## Modos de ejecución y recursos

Los perfiles Compose y sus comandos de entrada tendrán nombres estables:

| Modo | Finalidad y servicios |
| --- | --- |
| `demo` | Generar datos sintéticos, entrenar una versión pequeña y arrancar PostgreSQL, Qdrant, API e interfaz; el entrenamiento termina antes de servir |
| `serve` | Importar o usar una versión ya entrenada y ejecutar PostgreSQL, Qdrant, API e interfaz |
| `pipeline` | Preparar datos, entrenar y publicar con PostgreSQL, MLflow y Airflow; iniciar Qdrant cuando corresponda publicar |

La configuración permitirá combinar los servicios necesarios para demostrar la actualización de perfiles. No se ejecutarán dos entrenamientos simultáneos. El modo de referencia para medir latencia mantendrá pausado el entrenamiento pesado; una prueba adicional mostrará la degradación si comparten recursos. Si el equipo no soporta el conjunto completo, se ejecutarán las fases secuencialmente y se documentará esa limitación.

La computadora actual es ARM64, con 16 GiB de RAM y 10 CPU lógicas. Después de preparar Docker, Colima tiene asignadas 4 CPU y 8 GiB de RAM; el motor informa aproximadamente 7.74 GiB utilizables. La última evidencia registró 204.45 GiB libres en el volumen del proyecto y 82.09 GiB dentro de Docker. Son mediciones de esa preparación, no espacio reservado.

La configuración inicial aplicada a Colima es 4 CPU y 8 GiB de RAM; se ajustará después de medir, dejando memoria para el sistema operativo. Esto es una hipótesis de trabajo, no una garantía de que todo el pipeline quepa. La guía oficial de Airflow advierte de sus necesidades de memoria y del alcance limitado de su ejemplo Compose; adaptaremos los servicios necesarios en vez de adoptar ese ejemplo íntegro. [R3, R4, R5]

Se presupuestarán inicialmente entre 100 y 150 GiB libres para el trabajo completo, incluyendo originales, Parquet, imágenes Docker, modelos y margen de respaldo. La estimación deberá reemplazarse con mediciones por etapa. Se comprobará tanto el espacio del host como el del disco virtual y los volúmenes Docker: el espacio libre del host no garantiza capacidad dentro de Colima. Las imágenes JPEG se conservarán en el ZIP; extraer todo el archivo no será un paso obligatorio. La interfaz resolverá sólo las imágenes solicitadas y limitará su caché.

La primera semana medirá lectura, creación de variables y entrenamiento con una muestra temporal representativa y al menos dos tamaños de entrada. Registrará memoria máxima, ejemplos por segundo, tamaño de artefactos y una proyección del entrenamiento completo. A más tardar al cerrar la segunda semana se deberá demostrar una época completa sobre las particiones de entrenamiento; mientras falte, el calendario seguirá siendo provisional. Se ajustarán lotes, trabajadores, optimizador y anchura de red dentro del modelo acordado. Reducir permanentemente el histórico para entregar requeriría revisar el alcance, no se ocultará bajo la demostración sintética.

## Plataformas y límites de la reproducción

La ruta de referencia será Linux en contenedores CPU. Los objetivos de soporte inicial son Linux x86_64 con Docker Engine y macOS Apple Silicon con Colima y Docker. Se verificarán imágenes y dependencias para `linux/amd64` y `linux/arm64`; la disponibilidad de ambas variantes todavía no está comprobada. No se presentará una ejecución emulada como prueba de rendimiento nativo. Windows con WSL2 y aceleración CUDA o MPS serán opcionales, sin condicionar la entrega. [R5, R6]

No harán falta una cuenta de nube, dominio, registro privado, licencia de GPU o cuenta de Hopsworks. Se necesitará Internet para obtener inicialmente software, imágenes y datos. Una vez almacenados esos recursos, entrenamiento y servicio deberán poder funcionar sin conexión externa. La distribución opcional de imágenes mediante `docker save/load`, con manifiesto de arquitectura y huellas, permitirá trasladar el sistema sin publicar en un registro.

Reproducibilidad significa conservar datos, código, dependencias, configuración y procedimientos, además de comparar resultados con tolerancias explícitas. No se prometerán pesos idénticos bit a bit entre procesadores, versiones o aceleradores. Se registrarán semillas, dispositivos y opciones deterministas; PyTorch señala límites a la reproducibilidad exacta entre entornos. [R7]

## Contrato del repositorio que se implementará

El repositorio será independiente de esta carpeta, del nombre de usuario y del runtime de Codex. Usará rutas relativas o variables documentadas. El código ejecutable será un paquete; los notebooks serán exploratorios. La automatización no dependerá de ejecutarlos manualmente ni de su estado previo.

```text
README.md                         Guía desde una computadora limpia
.env.example                      Variables documentadas sin secretos
.gitignore / .dockerignore         Exclusión de datos, tokens y artefactos privados
.python-version                   Versión del intérprete verificada
pyproject.toml / uv.lock           Dependencias resueltas de aplicación y trabajos
compose.yaml                      Servicios, perfiles, redes y volúmenes
docker/                           Imágenes de servicio, trabajos y Airflow
configs/                          Ejecuciones demo y H&M completas
src/                              Datos, variables, modelo, publicación y API
dags/                             Orquestación que llama al paquete
scripts/                          Comandos repetibles de instalación y operación
tests/                            Pruebas críticas e integración con datos sintéticos
docs/setup.md                     macOS y Linux; diagnóstico de errores
docs/data.md                      Acceso, integridad, esquema y cortes temporales
docs/operations.md                Arranque, respaldo, restauración y rollback
docs/architecture.md              Diseño y decisiones registradas
evidence/                         Resultados sanitizados de reproducción y carga
```

Las versiones exactas se elegirán en la prueba de compatibilidad de la primera semana y se fijarán en archivos de bloqueo, constraints de Airflow e imágenes por digest. No se dejarán dependencias flotantes como `latest`. Se usará `uv sync --locked` donde corresponda para detectar cambios no reflejados en el lockfile. La versión del runtime, arquitectura y digest efectivamente utilizado se registrarán en cada ejecución. Un lockfile no demuestra por sí solo compatibilidad multiplataforma. [R6, R8]

Los scripts siguientes son entregables pendientes, no comandos disponibles hoy:

| Comando previsto en `scripts/` | Contrato mínimo |
| --- | --- |
| `doctor` | Revisar motor Docker, Compose, arquitectura, memoria, disco, puertos y permisos; devolver errores accionables sin cambiar el equipo |
| `bootstrap` | Preparar directorios, configuración y credenciales locales; construir imágenes y ejecutar migraciones sin sobrescribir configuración existente |
| `download-data` | Autenticar con el token del usuario, descargar a un temporal, reintentar y publicar el ZIP sólo tras validar; admitir ZIP local |
| `validate-data` | Verificar hash, inventario, conteos, esquema y referencias; distinguir comprobación rápida e integral |
| `demo` | Crear datos sintéticos deterministas, entrenar, publicar y arrancar una demostración funcional |
| `pipeline` | Ejecutar etapas declaradas o iniciar su programación; admitir reanudación y cortes de datos explícitos |
| `up` / `down` | Arrancar el perfil elegido y detenerlo conservando datos; comprobar disponibilidad real de dependencias |
| `verify` | Ejecutar pruebas críticas y prueba funcional HTTP; exigir un modelo activo compatible |
| `benchmark` | Ejecutar carga controlada y exportar métricas, consumo y configuración del experimento |
| `export-release` / `import-release` | Transferir una versión verificable del modelo, catálogo, perfiles y vectores; reconstruir el índice en destino |
| `backup` / `restore` | Exportar y restaurar bases, manifiestos y artefactos; validar restauración sobre volúmenes vacíos |

Podrán exponerse también mediante Make, pero se documentará la invocación directa. Los nombres concretos de archivos ejecutables y la implementación interna quedan a criterio del constructor, manteniendo estos contratos.

Las herramientas de sistema tendrán instrucciones separadas por plataforma, con versiones probadas y comprobaciones previas. Ningún script instalará silenciosamente software global, reiniciará otros servicios o eliminará volúmenes. Se distinguirá parada normal de borrado explícito. Bootstrap y migraciones deberán poder repetirse sin duplicar registros ni regenerar contraseñas activas. Se contemplarán permisos de volúmenes en Linux y rutas con espacios.

Cada instalación generará sus propios secretos. Los archivos de credenciales tendrán acceso restringido y quedarán fuera de Git, imágenes y logs. El motor sólo necesitará publicar API e interfaz en `127.0.0.1` por defecto; las bases tendrán conectividad interna. Las interfaces administrativas se habilitarán localmente cuando se usen. Cambiar a una red compartida exigirá documentar autenticación y exposición, fuera de la configuración inicial.

Git local será suficiente para versionar. Elegir un proveedor remoto será opcional; no bloqueará instalación ni entrega. Se podrá transferir el código como un paquete Git o archivo de la versión. No se incluirá el entorno temporal `.venv-validation` como mecanismo de instalación.

## Paquetes de modelo y recuperación

Cada versión portable incluirá pesos PyTorch, configuración, vocabularios, esquema de variables, catálogo, perfiles iniciales, vectores de artículos en un formato intercambiable y popularidad de respaldo. El manifiesto registrará identificador de versión, commit, hash de datos, corte temporal, dimensión, métrica, dependencias, semillas, dispositivo, resultados y huellas de todos los archivos.

Importar una versión no requerirá volver a entrenar ni acceder al servidor MLflow original. Se verificará integridad y compatibilidad, se reconstruirá Qdrant desde los vectores y se efectuarán consultas de prueba antes de activar. Esto evita depender de que un volumen binario de una base sea portable entre versiones y arquitecturas. El respaldo operativo exportará PostgreSQL de forma lógica y copiará artefactos con sus manifiestos; se probará sobre volúmenes nuevos sin destruir los existentes.

La reproducción de una versión para servir y la reproducción de su entrenamiento son pruebas diferentes. Para la primera se compararán respuestas de consultas fijas, puntuaciones con tolerancia numérica y desempate por identificador. Para la segunda se repetirán datos, configuración y procedimiento y se reportará variación de métricas. Los umbrales numéricos se fijarán con la primera ejecución de referencia antes de validar otra máquina.

## Tiempo histórico y operación de una computadora personal

Los datos de H&M terminan en 2020. Las variables históricas se calcularán respecto a un `data_as_of` explícito; no se usará la fecha actual de la computadora para recencia o popularidad del histórico. Se reservarán las últimas dos semanas para validación y prueba, conforme a la propuesta. Una simulación trasladará los eventos a un reloj declarado o avanzará ese reloj, conservando el orden y sin mezclarse con la evaluación histórica.

Se distinguirán `event_time` y `received_at`. La frescura operativa se medirá desde recepción hasta publicación del perfil. Los agregados se actualizarán cada cinco minutos mientras esté activo el pipeline; el reentrenamiento completo será manual inicialmente y después semanal si su duración lo permite. Dormir o apagar la computadora suspende el servicio. Al volver se procesarán intervalos pendientes con límites y sin disparar muchos entrenamientos atrasados. La interfaz mostrará el corte y la última actualización efectiva.

## Pruebas de aceptación

| Prueba | Evidencia requerida |
| --- | --- |
| Instalación limpia | Código en otra ruta y usuario, configuración propia y volúmenes vacíos; demo funcional siguiendo sólo README |
| Repetición del setup | Segunda ejecución sin pérdida de datos, eventos duplicados ni cambios inesperados de secretos |
| Independencia de cuentas | Demo sin Kaggle; flujo real con token propio o ZIP autorizado; ausencia de llamadas a servicios de pago |
| Entrenamiento masivo | Conteos de histórico procesado, filas de entrenamiento tras cortes, configuración, tiempo y memoria; validación y prueba aisladas |
| Calidad | MAP@12 y NDCG@12, Recall@200, cobertura y segmentos; referencia de popularidad con el mismo corte |
| Publicación | Rechazo de esquemas o dimensiones incompatibles; activación consistente y rollback sin mezclar versiones |
| Reinicio y restauración | Persistencia tras reinicio; recuperación en volúmenes nuevos mediante respaldo verificado |
| Otra máquina | Instalación y consultas con la misma versión en un segundo equipo; hardware y sistema registrados |
| Funcionamiento sin Internet | Servicios, inferencia y trabajos con recursos ya descargados; sin solicitudes externas obligatorias |
| Carga local | Dos minutos de calentamiento y diez minutos a 20 solicitudes/s; p50, p95, p99, errores, tasa efectiva y recursos |

La meta inicial de servicio será p95 menor de 200 ms y errores menores del 1%; la frescura de perfiles buscará menos de diez minutos mientras el pipeline esté activo. Se medirá desde localhost o la red local, indicando dónde corre el generador de carga y su consumo. Son objetivos por probar, no resultados actuales.

Una instalación aislada en una VM local ayuda a comprobar dependencias ocultas, pero no demuestra funcionamiento en otro equipo físico ni otra arquitectura. No se ha confirmado disponibilidad de un segundo equipo: esa evidencia se obtendrá con un integrante del equipo o una máquina prestada, sin compra. Mientras falte, el soporte correspondiente se declarará no verificado.

## Plan de trabajo y pendientes reales

| Semana relativa | Resultado esperado |
| --- | --- |
| 1 | Git, README, configuración de aplicación, locks iniciales, datos sintéticos y medición temprana de factibilidad CPU; Docker ya está preparado |
| 2 | Ingesta verificable, Parquet completo, perfiles temporales, pruebas de fuga y una época de entrenamiento completa medida |
| 3 | Modelo PyTorch entrenado, validación frente a popularidad y registro de experimentos |
| 4 | API, Qdrant, Streamlit y paquete portable con publicación y rollback; demo sintética completa y servicio de una versión H&M |
| 5 | Airflow, eventos idempotentes y actualización periódica de perfiles |
| 6 | Instalación limpia, transferencia de versión y respaldo/restauración; segundo equipo si está disponible |
| 7 | Pruebas de carga, memoria y frescura; ajustes; grafos sólo si el núcleo está completo |
| 8 | Evaluación final, evidencia de reproducción, documentación y demostración |

Se conserva la estimación inicial de 12 a 15 horas por semana, sujeta al tiempo disponible y tamaño del equipo. La medición de entrenamiento de la primera semana puede exigir reestimar; no se promete cumplir ocho semanas sin esa comprobación.

Los pendientes que requieren trabajo técnico son probar y fijar dependencias para ambas arquitecturas, medir la capacidad de entrenamiento, implementar los componentes y producir evidencia. Compose y la configuración inicial de Docker ya están comprobados. No hace falta elegir proveedor de nube ni contratar recursos. El acceso propio a Kaggle sólo es necesario para reproducir los datos reales desde su origen; la computadora de origen ya tiene los datos validados. La disponibilidad del segundo equipo y sus permisos de instalación sigue sin confirmarse. Las decisiones de librerías auxiliares, tamaños de lotes y detalles de scripts pueden resolverse durante la implementación y registrarse, sin solicitar nuevas decisiones de producto.

El plan permite comenzar, pero sólo se considerará completa la reproducibilidad cuando otra instalación ejecute los procedimientos y produzca la evidencia anterior. Un README y un archivo Compose sin esa prueba no bastan.

## Referencias técnicas

Consultadas para esta revisión el 18 de octubre de 2026. Las versiones señaladas por la documentación no se consideran versiones ya probadas del proyecto.

1. [Kaggle CLI y autenticación](https://github.com/Kaggle/kaggle-cli) y [dataset H&M](https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/data).
2. [MLflow Tracking Server y servicio de artefactos](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/).
3. [Airflow LocalExecutor y límites de concurrencia](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/local.html).
4. [Airflow con Docker Compose y requisitos del ejemplo](https://airflow.apache.org/docs/apache-airflow/stable/howto/docker-compose/index.html).
5. [Colima y configuración de recursos](https://github.com/abiosoft/colima).
6. [Docker e imágenes para múltiples plataformas](https://docs.docker.com/build/building/multi-platform/).
7. [PyTorch y reproducibilidad](https://docs.pytorch.org/docs/2.14/notes/randomness.html).
8. [uv y sincronización con archivos de bloqueo](https://docs.astral.sh/uv/concepts/projects/sync/).
