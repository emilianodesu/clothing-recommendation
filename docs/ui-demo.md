# Interfaz para demostrar recomendaciones

La interfaz se centra en las prendas y en el efecto de una compra simulada. Las versiones, hashes, cortes temporales, ensayos y recibos técnicos permanecen en la API, los reportes y la documentación; no se muestran en la pantalla de la demo.

## Recorrido para la presentación

1. Abrir **Para ti**. **Nuevo visitante** empieza sin compras de esta sesión; los [perfiles descriptivos](demo-profiles.md) permiten explorar historiales anónimos reales con hábitos reconocibles, como tonos claros, oscuros, estampados o variedad de prendas.
2. Abrir **Explorar catálogo**. Elegir una prenda y pulsar **Simular compra**. La confirmación indica que se guardó; todavía falta actualizar la selección.
3. Pulsar **Actualizar recomendaciones**. La aplicación vuelve a **Para ti**, compara las dos listas e indica las prendas nuevas o las que subieron. Si nada cambia, lo explica sin inventar una mejora.
4. Mostrar **¿Cómo funciona?** para explicar la mezcla de compras recientes y popularidad. Los filtros de prenda/color afectan a las recomendaciones; el catálogo permite explorar todos los artículos por páginas.

No se hacen pedidos ni cobros. Cada clic nuevo en Simular compra representa una interacción distinta. Si se pierde la confirmación, **Reintentar compra** conserva el mismo identificador y contenido para evitar duplicados. La selección de perfil y la actualización se bloquean mientras hay una compra pendiente. Cerrar un intento rechazado no deshace una compra ya guardada.

Los nombres de perfil describen compras de referencia, no nombres reales ni cuentas personales. Moda masculina y femenina son categorías del catálogo, no el género del comprador. El nuevo visitante dura la sesión del navegador. La simulación conserva su fecha de referencia y registra compras dentro de la ventana ya admitida; no avanza el reloj ni cambia H para producir un resultado más vistoso. Una compra puede dejar la lista igual, especialmente si la prenda ya encabezaba las recomendaciones.

## Fotografías y nombres

Las fotografías proceden del ZIP Kaggle montado en la API. Se verificaron visualmente fotografías reales en `https://hm.alanmagno.com` el 25 de octubre, antes de publicar este rediseño; tener el ZIP por sí solo no se tomó como prueba de renderizado. No se exportaron imágenes ni identificadores de compradores del sitio.

Las tarjetas usan el tipo de prenda y color del catálogo, con traducción de categorías comunes. No inventan nombres comerciales ausentes del paquete. La nueva consulta `POST /articles/details` admite hasta 48 identificadores y sólo lee el catálogo en memoria; no modifica ranking, estado o base de datos. Durante una actualización gradual, una API anterior puede omitir estos detalles y la UI sigue mostrando fotos y acciones con un nombre genérico.

Si falta una foto, se muestra el sustituto existente; si falla su petición, aparece **Foto no disponible** y el resto de la demo sigue funcionando. Eso no equivale a garantizar que todos los artículos del dataset tienen fotografía.

## Actualización del servidor

El usuario confirmó que el servidor se actualiza automáticamente desde el repositorio. Esta revisión modifica **API y UI** y no necesita migraciones, restauración, entrenamiento, campañas ni evaluación. El mecanismo del servidor debe reconstruir/seleccionar la imagen de aplicación para ambos servicios, conservar PostgreSQL, paquetes, ZIP de fotografías, credenciales, proxy y volúmenes, y actualizar su lock local de imágenes según su procedimiento. El actualizador propio del servidor no está incluido en este repositorio; un `git pull` aislado no cambia una imagen Docker ya ejecutándose.

Se reconoce la nueva interfaz por **Encuentra tu próxima prenda**, **Para ti** y **Explorar catálogo**. La publicación Git no acredita por sí sola que el sitio ya cambió: comprobar estas señales en el navegador, fotografías y salud tras la actualización. Mantener autenticación y HTTPS. No publicar credenciales ni capturas de la UI anterior con compradores.

## Verificación de este cambio

- Pruebas Streamlit con HTTP sintético: perfiles, imágenes, metadatos vigentes, compra/reintento, conflicto, actualización, comparación y fallos recuperables.
- Ruta FastAPI real con snapshot sintético: límite de 48, validación, orden, omisión de desconocidos y ausencia de escrituras.
- Caché de imágenes y suite host; no se repiten pruebas académicas con datos H&M.
- Navegador local con transporte sintético: catálogo, confirmación y regreso automático a Para ti con comparación. No prueba una escritura en la base de producción.

Las capturas de entrega anteriores siguen siendo evidencia histórica; no representan el diseño actual. Véase [evidencia de UI](../evidence/ui-demo-local.json).
