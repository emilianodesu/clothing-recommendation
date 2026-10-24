# Traspaso a servidor con HTTPS para el equipo

Solicitud: dejar el repositorio completo y comprensible para que el compañero y su agente desplieguen en su propio servidor. El usuario delega descubrir SO, arquitectura, RAM y disco al agente receptor. Decide acceso desde Internet con dominio, HTTPS y autenticación para el equipo. El repositorio permanece público; los datos y credenciales siguen privados.

## Diseño

Reutilizar PostgreSQL, API H y Streamlit de Compose y restauración lógica `hm-app-backup-v1`. H conserva selección, reglas y cortes; no requiere entrenamiento ni Qdrant. No ejecutar runners históricos mac24, campañas Q ni test final. Restaurar en una ruta nueva con contraseñas generadas, verificar seis consultas y readiness antes de iniciar UI. El respaldo se obtiene de la instalación fuente y se transfiere por SSH/SFTP privado con SHA-256; no por Releases públicas. El dataset crudo es opcional para imágenes y no está en el respaldo. Sin él hay imágenes de sustitución.

Elecciones de implementación del agente: servidor Linux ARM64/AMD64, Docker Engine y Compose; Caddy con versión/digest fijados, HTTPS automático y autenticación HTTP Basic sobre TLS, un usuario por integrante. No requiere una cuenta externa. Todos son operadores de confianza, con el mismo permiso sobre la demo; no es autorización por comprador, SSO ni MFA. Proteger también WebSocket y recursos estáticos. El proxy sólo dirige a Streamlit y no reenvía Authorization. Sólo 80/443 públicos; API/UI existentes conservan bindings localhost y bases de datos sin puertos publicados. No exponer Airflow ni MLflow. DNS, firewall y puertos los configura el agente receptor después del diagnóstico; no instalar paquetes ni modificar firewall a ciegas.

Entrada única `scripts/server.py`: diagnóstico en lectura, preparar fuentes/configuración, construir imágenes, restaurar respaldo usando implementación existente, iniciar UI, configurar usuarios/proxy, verificar salud/auth/HTTPS y emitir informe privado del despliegue. Separar utilidades de infraestructura y gateway cuando mejoren legibilidad. Rechazar recursos insuficientes y servicios sin límite de memoria; construcción inicial en motor dedicado sin cargas activas. No parar otros proyectos ni limpiar volúmenes. Runtime mínimo presupuestado 4GiB API + .5GiB PG + .5GiB UI + .25GiB proxy; trabajos backup/restore 5GiB por separado; margen de al menos 1GiB. Construcción requiere 8GiB disponibles y 4CPU como admisión conservadora, sin afirmar un pico medido. Presupuestar disco de imágenes y respaldo, 40GiB libres iniciales; con ZIP imágenes añadir 31GiB. Hardware físico AMD64/Linux aún no validado.

Mantener la aplicación en ruta de restauración inventariada; checkout fuente separado para construir/actualizar. No actualizar automáticamente imágenes por tag. Guardar IDs, commit, arquitectura, huellas, readiness y resultados de control sin clientes ni contraseñas. Respaldos privados y rollback explícito, conservando versiones previas. No borrar intentos ni reescribir las auditorías académicas.

## Documentación

README y AGENTS cortos con estado vigente y enlace a guía del agente receptor. Preservar historia anterior en archivo documental, conservando enlaces. Guía por pasos con diagnóstico, instalación oficial según SO, construcción, obtención/transferencia de respaldo, restauración, imágenes opcionales, DNS/TLS/login, verificación externa y operación/backup/rollback. Índice de hallazgos, modelos/experimentos, resultados, cobertura del temario, arquitectura, planes y evidencias. T8-B failed histórico permanece; h-cycle acredita sólo un ciclo breve; no afirmar concurrencia sostenida ni despliegue real Linux ya realizado.

## Verificación y límites

Tests host para admisión, configuración maliciosa/incompleta, contraseñas fuera de argv/logs, rutas con espacios y ausencia de mutaciones ante fallo. Validar Compose/gateway y, si hay capacidad local, un proxy aislado con upstream sintético, sin tocar servicios existentes. Revisión independiente de seguridad y ejecutabilidad. DNS/ACME público, navegador y restauración real en el servidor son aceptación del receptor; no inventar sus resultados.

## Decisiones cerradas tras revisión

Revisor de diseño no encontró preguntas adicionales para el usuario. Build usa 8GiB de presupuesto + 1GiB de margen (9GiB Docker), constructor fijo 6GiB/3CPU. Backup suma cuotas activas + 5GiB + 1GiB (11.25GiB con servicio mínimo). Restauración fija IDs mediante override privado estricto y rechaza rutas host/VPN que se solapen con 10.253.0.0/16. Se verifica disco Docker aparte del destino. Cada intento de build conserva carpeta única. No se detiene infraestructura existente para admitir cargas.
