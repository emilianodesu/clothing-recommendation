# Perfiles descriptivos para la demostración

La selección **¿Para quién buscamos?** ofrece un nuevo visitante y hasta nueve historiales anónimos elegidos por sus compras. El usuario prefirió ejemplos reales representativos a personajes con compras inventadas. Las etiquetas describen prendas del catálogo; no son nombres, géneros ni atributos personales de los compradores.

| Perfil | Qué permite observar |
| --- | --- |
| Moda masculina · oscuros | Compras de la categoría Menswear con colores oscuros |
| Moda femenina · claros | Compras de Ladieswear con colores claros |
| Negro, gris y tonos oscuros | Predominio de oscuros sin restringir la sección del catálogo |
| Blancos y tonos claros | Predominio de claros sin restringir la sección |
| Estampados y rayas | Tipos de estampado declarados por el catálogo |
| Vestidos y faldas | Concentración en estos dos tipos de prenda |
| Capas y abrigo | Suéteres, cárdigans, chaquetas, abrigos y prendas afines |
| Un color, varias prendas | Un color predominante en al menos tres tipos de prenda |
| Variedad sin un favorito | Varios tipos y colores, sin una mayoría dominante |

No se garantiza que todos existan en cualquier instalación o fecha. Si los datos no respaldan una etiqueta, se omite. Un comprador sólo ocupa un ejemplo. Los números de coincidencia y tres compras de referencia acompañan al nombre; sus identificadores no se muestran en pantalla.

## Cómo se seleccionan

`GET /streams/{stream_id}/demo-profiles` consulta los perfiles publicados del stream y verifica que pertenecen al paquete activo. Usa una transacción **REPEATABLE READ, READ ONLY**, timeout SQL de 20 segundos y cursor de servidor con lotes de 512 filas. No genera compras, modifica perfiles, publica estado ni escribe en la base.

La consulta toma hasta 25,000 historiales con al menos cuatro artículos, ordenados por identificador. Sólo admite compradores presentes en el paquete original: los nuevos visitantes de la demo no pasan a ser ejemplos. Analiza las primeras doce prendas distintas de cada historial, en el orden que ya utiliza H. La muestra se limita por coste y no representa estadísticamente a todos los compradores.

Las categorías habituales exigen al menos 75% de coincidencia. Un color/varias prendas requiere cinco prendas como mínimo, al menos tres tipos dentro del color dominante, al menos 80% de prendas de vestir y 80% del mismo color. Variedad exige seis prendas como mínimo, cuatro tipos, tres colores, datos de color completos, al menos 80% de prendas de vestir y ninguna mayoría de tipo o color superior al 50%. Los empates se resuelven por proporción, tamaño hasta ocho prendas e identificador; se conservan alternativas para no repetir comprador entre categorías. Estos criterios curan ejemplos de presentación, **no seleccionan ni evalúan el recomendador**.

Tipo, grupo y color proceden del catálogo servido. Para las categorías masculina/femenina y estampados se lee opcionalmente `articles.csv` del ZIP configurado en `HMREC_IMAGE_ARCHIVE`: máximo 64 MiB y 200,000 filas, sin extraer archivos ni leer CSV de compradores/transacciones. Sólo se enriquecen artículos del catálogo activo. Si falta el ZIP o esos campos, se mantienen los estilos basados en tipo/color y se omiten las categorías que no pueden justificarse.

La API conserva un catálogo enriquecido y hasta cuatro respuestas pequeñas, vinculadas a paquete, stream, revisión e identidad del archivo. La primera consulta prepara los ejemplos; las siguientes reutilizan el resultado. La UI conserva el conjunto de referencia durante la sesión mientras se mantengan stream, paquete y fecha. Esto evita que simular una compra cambie de comprador seleccionado o borre la comparación. Los nombres y la descripción corresponden al **historial de partida**; las nuevas compras sí pueden cambiar sus recomendaciones. Una nueva sesión puede elegir otros representantes si cambió el estado publicado.

## Presentación sugerida

1. Elegir **Blancos y tonos claros** y leer su breve descripción y compras de referencia.
2. Cambiar a **Negro, gris y tonos oscuros** para contrastar las primeras prendas recomendadas.
3. Mostrar **Un color, varias prendas** o **Variedad sin un favorito**: un hábito de compra puede describirse por varias características, no sólo por una categoría de ropa.
4. Conservar uno de ellos, simular una compra y pulsar **Actualizar recomendaciones**. Comparar el resultado observado; no prometer un cambio si la prenda ya encabezaba la lista.

Los perfiles no aplican un filtro oculto. H sigue ordenando compras recientes y completando con popularidad, por lo que las prendas populares pueden contrastar con el nombre del perfil. No es un modelo que infiera género, estilo latente o similitud visual. La descripción de un perfil tampoco prueba calidad ni representatividad de toda la población.

## Actualizar una instalación existente

Este cambio requiere desplegar **API y UI** desde el mismo commit. No requiere migraciones, reentrenar, reconstruir paquetes, restaurar backups, reiniciar campañas ni recrear PostgreSQL. Conservar datos, volúmenes, autenticación, gateway y el ZIP de imágenes. Una API anterior no ofrece la nueva ruta; la UI conserva Nuevo visitante y explica que los ejemplos aún no están disponibles.

Después de que el actualizador del servidor publique ambas imágenes, comprobar el selector, las fotos, las compras de referencia y que el perfil seleccionado se conserva al actualizar. La publicación en Git no acredita despliegue efectivo; la verificación del navegador público es independiente.

## Evidencia local

La comprobación del 25 de octubre encontró los nueve estilos en 25,000 historiales H&M entre 2020-08-12 y 2020-09-08. Se verificaron las huellas de las particiones locales leídas. Sólo se publican conteos agregados; compradores, artículos seleccionados y resultados individuales permanecen en `.runtime/`.

Se comprobó el selector, el enriquecimiento opcional, el comportamiento de la UI y una consulta de sólo lectura sobre PostgreSQL sintético existente. No se ejecutó otra evaluación académica ni se modificaron datos. Los tiempos de esta comprobación local no son una medición del servidor ni una prueba de carga. Véase [evidencia](../evidence/demo-profiles-local.json).
