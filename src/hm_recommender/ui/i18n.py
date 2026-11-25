"""Translations and localization labels for fashion items and colors."""

NAMES = {
    'T-shirt': 'Camiseta',
    'Top': 'Top',
    'Vest top': 'Top de tirantes',
    'Sweater': 'Suéter',
    'Cardigan': 'Cárdigan',
    'Jacket': 'Chaqueta',
    'Blazer': 'Blazer',
    'Coat': 'Abrigo',
    'Dress': 'Vestido',
    'Skirt': 'Falda',
    'Trousers': 'Pantalón',
    'Shorts': 'Shorts',
    'Leggings/Tights': 'Leggings',
    'Shirt': 'Camisa',
    'Blouse': 'Blusa',
    'Hoodie': 'Sudadera',
    'Garment Set': 'Conjunto',
    'Bra': 'Sujetador',
    'Underwear bottom': 'Ropa interior',
    'Socks': 'Calcetines',
    'Bikini top': 'Top de bikini',
    'Swimwear bottom': 'Bikini',
    'Swimsuit': 'Traje de baño',
    'Sneakers': 'Tenis',
    'Boots': 'Botas',
    'Sandals': 'Sandalias',
    'Bag': 'Bolso',
    'Hat/beanie': 'Gorro',
    'Scarf': 'Bufanda',
    'Belt': 'Cinturón',
    'Earring': 'Aretes',
    'Necklace': 'Collar',
    'Ring': 'Anillo',
    'Hair/alice band': 'Diadema',
    'Unknown': 'Prenda',
    'Blue': 'Azul',
    'Dark Blue': 'Azul oscuro',
    'Light Blue': 'Azul claro',
    'Black': 'Negro',
    'White': 'Blanco',
    'Off White': 'Blanco hueso',
    'Grey': 'Gris',
    'Greyish Beige': 'Gris beige',
    'Dark Grey': 'Gris oscuro',
    'Light Grey': 'Gris claro',
    'Beige': 'Beige',
    'Light Beige': 'Beige claro',
    'Dark Beige': 'Beige oscuro',
    'Green': 'Verde',
    'Dark Green': 'Verde oscuro',
    'Light Green': 'Verde claro',
    'Pink': 'Rosa',
    'Light Pink': 'Rosa claro',
    'Dark Pink': 'Rosa oscuro',
    'Red': 'Rojo',
    'Dark Red': 'Rojo oscuro',
    'Yellow': 'Amarillo',
    'Light Yellow': 'Amarillo claro',
    'Orange': 'Naranja',
    'Purple': 'Morado',
    'Brown': 'Marrón',
    'Light Orange': 'Naranja claro',
    'Dark Orange': 'Naranja oscuro',
    'Light Turquoise': 'Turquesa claro',
    'Turquoise': 'Turquesa',
    'Silver': 'Plateado',
    'Gold': 'Dorado',
    'Other': 'Otro',
    'Solid': 'Liso',
    'Stripe': 'Rayas',
    'All over pattern': 'Estampado integral',
    'Melange': 'Jaspeado',
    'Denim': 'Mezclilla',
    'Check': 'Cuadros',
    'Dot': 'Lunares',
    'Front print': 'Estampado frontal',
}

INDEX_GROUPS = {
    'Ladieswear': 'Mujer (Ladieswear)',
    'Menswear': 'Hombre (Menswear)',
    'Divided': 'Divided',
    'Sport': 'Deportivo (Sport)',
    'Baby/Children': 'Bebé y niños (Baby/Children)',
}


def label(value: str | None, default: str = 'Prenda') -> str:
    """Return the localized label for an article type or colour, falling back to default."""
    if not value:
        return default
    return NAMES.get(value, value)


def section_label(value: str | None, default: str = 'Todas') -> str:
    """Return the localized label for a catalog section (`index_group_name`)."""
    if not value:
        return default
    return INDEX_GROUPS.get(value, value)
