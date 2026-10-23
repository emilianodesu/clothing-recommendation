# T8-B: comprobar capacidad antes de diseñar la carga concurrente

T8-A está cerrado. No repetir baseline, alineación API, T7 ni entrenamiento. Test permanece reservado.

## Decisión de implementación

El objetivo pendiente es observar degradación al preparar un paquete H mientras se sirve el H vigente, sin activar el nuevo paquete. Antes de lanzar un trabajo necesitamos conocer el conjunto real de contenedores: original, servicios auxiliares y restauración T7. El compose original incluye una cuota de 5 GiB para Airflow scheduler; por tanto no basta con sumar API y PostgreSQL. La RAM libre de macOS tampoco equivale a capacidad disponible dentro de Colima.

El colector `scripts/mac24_t8_capacity.py` sólo consulta Docker, valida el ZIP auditado T8-A y escribe/publica un reporte agregado. No crea contenedores, modifica servicios, reconstruye imágenes ni inicia cargas. Lee campos seleccionados de inspect; nunca exporta variables de entorno, montajes, credenciales o nombres de proyecto. Los proyectos se representan mediante SHA-256 y scope original/other. Compara nuevamente el inventario y los límites al finalizar para detectar cambios durante la captura.

Política `capacity-v1`, fijada antes de la consulta:

- VM existente de 14 GiB/4 CPU; se utiliza la memoria efectiva que informa Docker.
- Candidato de construcción H aislado: 4 GiB/1 CPU/2 hilos. Es una propuesta de presupuesto, no un consumo medido ni una garantía de que el trabajo terminará. El pico de construcción T4-A fue 2.248 GiB; no se usa como máximo garantizado. No se cambia el perfil histórico mac24 de entrenamiento de 10 GiB/3 CPU.
- Margen de 1 GiB para VM/runtime, añadido a la suma de todos los límites de contenedores activos y los 4 GiB del candidato.
- Un límite de memoria cero significa sin límite, no consumo cero. Servicios desconocidos o sin límite requieren inspección antes de decidir.
- `fits_memory_budget`: la suma cabe bajo esa política; sólo permite diseñar el ensayo, no lo ejecuta ni acredita rendimiento.
- `sequential_policy_required`: no admitir automáticamente el candidato con la configuración actual. Preparar una política secuencial y explicar su efecto en frescura, o diseñar posteriormente una transición controlada de servicios. No detener restauraciones/auxiliares ni ampliar recursos automáticamente.
- `inspect_unknown_limits`: no hay un presupuesto cerrado que permita tomar la decisión.

Las cuotas de CPU conocidas y contenedores sin cuota se reportan por separado. No son CPU consumida ni reservas exclusivas. Esta comprobación sólo decide admisión conservadora de memoria; no garantiza aislamiento CPU ni mide latencia. Sumar límites es deliberadamente más conservador que observar memoria actual: que no quepan todos los máximos no demuestra un fallo empírico bajo concurrencia. La decisión no cierra T8-B ni E7.

## Comando mac24

Con SHA completo entregado y clon limpio, desde la raíz:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
export DOCKER_CONTEXT=colima-hm-project
python3 scripts/mac24_t8_capacity.py --expected-commit SHA_COMPLETO
```

Guarda `capacity-mac24-*` en artifacts/handoff y publica el ZIP en GitHub privado. `passed` significa inventario válido, incluso si no se admite concurrencia. Devolver URL y decisión. Si la publicación falla, conservar el ZIP y usar sólo `scripts/github-handoff.py upload RUTA.zip`; no iniciar trabajos. Ante error sin reporte, devolver salida y conservar logs.

Siguiente acción principal: descargar, validar y revisar el inventario; concretar el ensayo o el procedimiento secuencial con los recursos observados. Ningún resultado de este comando autoriza entrenamiento, test o cambios en H.
