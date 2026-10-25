"""A purchase-and-refresh demo. Operational identities stay behind the interface."""
from datetime import date, datetime, time, timedelta, timezone
import os
from uuid import uuid4

import httpx
import streamlit as st


NAMES = {
    'T-shirt': 'Camiseta', 'Top': 'Top', 'Vest top': 'Top de tirantes',
    'Sweater': 'Suéter', 'Cardigan': 'Cárdigan', 'Jacket': 'Chaqueta',
    'Blazer': 'Blazer', 'Coat': 'Abrigo', 'Dress': 'Vestido', 'Skirt': 'Falda',
    'Trousers': 'Pantalón', 'Shorts': 'Shorts', 'Leggings/Tights': 'Leggings',
    'Shirt': 'Camisa', 'Blouse': 'Blusa', 'Hoodie': 'Sudadera', 'Garment Set': 'Conjunto',
    'Bra': 'Sujetador', 'Underwear bottom': 'Ropa interior', 'Socks': 'Calcetines',
    'Bikini top': 'Top de bikini', 'Swimwear bottom': 'Bikini', 'Swimsuit': 'Traje de baño',
    'Sneakers': 'Tenis', 'Boots': 'Botas', 'Sandals': 'Sandalias', 'Bag': 'Bolso',
    'Hat/beanie': 'Gorro', 'Scarf': 'Bufanda', 'Belt': 'Cinturón', 'Earring': 'Aretes',
    'Necklace': 'Collar', 'Ring': 'Anillo', 'Hair/alice band': 'Diadema',
    'Unknown': 'Prenda', 'Blue': 'Azul', 'Dark Blue': 'Azul oscuro',
    'Light Blue': 'Azul claro', 'Black': 'Negro', 'White': 'Blanco',
    'Off White': 'Blanco hueso', 'Grey': 'Gris', 'Greyish Beige': 'Gris beige',
    'Dark Grey': 'Gris oscuro', 'Light Grey': 'Gris claro', 'Beige': 'Beige',
    'Light Beige': 'Beige claro', 'Dark Beige': 'Beige oscuro', 'Green': 'Verde',
    'Dark Green': 'Verde oscuro', 'Light Green': 'Verde claro', 'Pink': 'Rosa',
    'Light Pink': 'Rosa claro', 'Dark Pink': 'Rosa oscuro', 'Red': 'Rojo',
    'Dark Red': 'Rojo oscuro', 'Yellow': 'Amarillo', 'Light Yellow': 'Amarillo claro',
    'Orange': 'Naranja', 'Purple': 'Morado', 'Brown': 'Marrón',
    'Light Orange': 'Naranja claro', 'Dark Orange': 'Naranja oscuro',
    'Light Turquoise': 'Turquesa claro', 'Turquoise': 'Turquesa',
    'Silver': 'Plateado', 'Gold': 'Dorado', 'Other': 'Otro',
}


def label(value, default='Prenda'):
    return NAMES.get(value, value) if value else default


def main():
    st.set_page_config(page_title='H&M · Recomendaciones', page_icon='🧥', layout='wide', initial_sidebar_state='collapsed')
    st.html('''<style>
    .block-container {max-width:1200px;padding-top:2.2rem;padding-bottom:3rem;}
    [data-testid="stImage"] img {height:245px;object-fit:contain;background:#f6f5f3;border-radius:8px;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px;}
    @media(max-width:640px) {[data-testid="stImage"] img {height:210px;}}
    </style>''')
    st.caption('H&M · DEMO DE RECOMENDACIONES')
    st.title('Encuentra tu próxima prenda')
    st.write('Elige un perfil, simula una compra y descubre cómo cambia su selección.')
    base = os.environ.get('HMREC_API_URL', 'http://api:8000')
    client = httpx.Client(base_url=base, trust_env=False, timeout=120, follow_redirects=False)

    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def submit(payload):
        st.session_state['pending'] = payload
        try:
            result = call('POST', '/events', json=payload)
            st.session_state.update(pending=None, pending_terminal=False, receipt=result,
                notice=('success', 'Compra simulada guardada. Pulsa «Actualizar recomendaciones» para ver el resultado.'))
        except httpx.HTTPError as error:
            terminal = (isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500
                        and error.response.status_code not in (408, 429))
            st.session_state['pending_terminal'] = terminal
            message = ('No se pudo aceptar esta compra. Cierra el intento y vuelve a elegir una prenda.'
                       if terminal else 'No pudimos confirmar la compra. Reinténtala con el botón de abajo; no se duplicará.')
            st.session_state['notice'] = ('error', message)
        st.rerun()

    def photo(article_id, name):
        try:
            response = client.get(f'/articles/{article_id}/image', timeout=8)
            response.raise_for_status()
            mime = response.headers.get('content-type', '')
            if mime.startswith('image/svg'):
                st.image(response.text, width='stretch', alt=name)
            elif mime.startswith('image/'):
                st.image(response.content, width='stretch', alt=name)
            else:
                st.caption('Foto no disponible')
        except (httpx.HTTPError, ValueError, OSError):
            st.caption('Foto no disponible')

    try:
        st.session_state.setdefault('pending', None)
        st.session_state.setdefault('visitor', 'demo-'+uuid4().hex)
        if st.session_state.get('notice'):
            level, message = st.session_state.pop('notice')
            getattr(st, level)(message)
        pending = bool(st.session_state['pending'])
        if pending:
            st.warning('Tienes una compra pendiente de confirmación.')
            terminal = st.session_state.get('pending_terminal', False)
            if st.button('Reintentar compra', key='retry', disabled=terminal):
                submit(st.session_state['pending'])
            if terminal:
                st.caption('Cerrar el intento no deshace una compra que ya se haya guardado.')
                if st.button('Cerrar este intento', key='discard_pending'):
                    st.session_state.update(pending=None, pending_terminal=False)
                    st.rerun()
        call('GET', '/health/ready')
        streams = call('GET', '/streams')['items']
        current = {s['stream_id']: s for s in streams if s['mode'] in ('simulation', 'synthetic')}
        if not current:
            st.info('La demostración se está preparando. Vuelve en unos momentos.')
            return
        # Stable identity/order across publications; never keep a stale metadata dict.
        saved = st.session_state.get('stream')
        if isinstance(saved, dict): saved = saved.get('stream_id')
        st.session_state['stream'] = saved if saved in current else next(iter(current))
        stream_ids = sorted(current)
        if len(current) > 1:
            with st.sidebar:
                st.subheader('Demostración')
                st.selectbox('Demostración', stream_ids, key='stream', disabled=pending,
                    format_func=lambda sid: f'Demostración {stream_ids.index(sid)+1}')
        stream = st.session_state['stream']
        chosen = current[stream]
        release = chosen['release_id']
        cutoff = date.fromisoformat(chosen['data_as_of'])
        customers = call('GET', '/customers')['items']
        profiles = [st.session_state['visitor'], *customers]
        if st.session_state.get('profile') not in profiles: st.session_state['profile'] = profiles[0]
        left, right = st.columns([2, 1], vertical_alignment='bottom')
        customer = left.selectbox('¿Para quién buscamos?', profiles, key='profile', disabled=pending,
            format_func=lambda cid: 'Nuevo visitante' if cid == profiles[0] else f'Perfil {customers.index(cid)+1}')
        refresh = right.button('Actualizar recomendaciones', key='refresh', type='primary',
                               disabled=pending, width='stretch')
        st.caption('Las compras son simuladas. No se realizan pedidos ni cobros.')
        catalog = call('GET', '/catalog', params={'offset': 0, 'limit': 12})
        if refresh: st.session_state['demo_tab'] = 'Para ti'
        for_you, explore = st.tabs(['Para ti', 'Explorar catálogo'], key='demo_tab', on_change='rerun')
        with for_you:
            filters = {}
            with st.popover('Filtrar recomendaciones'):
                columns = st.columns(2)
                for column, field, name in zip(columns, ('product_type_name', 'colour_group_name'), ('Prenda', 'Color')):
                    options = [None, *catalog['filters'].get(field, [])]
                    key = 'filter_'+field
                    if st.session_state.get(key) not in options: st.session_state[key] = None
                    value = column.selectbox(name, options, key=key, format_func=lambda v: label(v, 'Todas'))
                    if value is not None: filters[field] = value
            context = (stream, customer, tuple(sorted(filters.items())))
            if st.session_state.get('selection_context') != context:
                st.session_state['comparison'] = None
            st.session_state['selection_context'] = context
            if refresh:
                st.session_state['comparison'] = None
                before = st.session_state.get('last_selection')
                try:
                    with st.spinner('Actualizando tu selección…'):
                        call('POST', f'/admin/streams/{stream}/refresh', json={'data_as_of': str(cutoff)},
                             headers={'Authorization': 'Bearer '+os.environ.get('HM_APP_PASSWORD', '')})
                    st.session_state['comparison'] = before if before and before['context'] == context else None
                    st.session_state['notice'] = ('success', 'Recomendaciones actualizadas.')
                    st.rerun()
                except httpx.HTTPError:
                    st.error('No pudimos actualizar la selección. Tus compras guardadas se conservan; vuelve a pulsar «Actualizar recomendaciones».')
            result = call('POST', f'/streams/{stream}/recommend', json={'customer_id': customer, 'k': 12, 'filters': filters})
            ranked = [item['article_id'] for item in result['items']]
            comparison = st.session_state.get('comparison')
            previous = comparison['items'] if comparison and comparison['context'] == context else None
            st.session_state['last_selection'] = {'context': context, 'items': ranked}
            st.subheader('Recomendado para ti')
            st.caption('Sin compras recientes para esta selección: empezamos por las prendas populares.'
                       if result['fallback'] else 'Una selección basada en las compras recientes de este perfil, acompañada de prendas populares.')
            if previous is not None:
                new = sum(item not in previous for item in ranked)
                moved = sum(item in previous and previous.index(item) != i for i, item in enumerate(ranked))
                st.info(f'{new} prendas nuevas y {moved} cambiaron de posición respecto a la selección anterior.'
                        if previous != ranked else 'La selección se mantiene. Una compra no siempre cambia las prendas o su orden.')
            details = {item['article_id']: item for item in catalog['items']}
            if ranked:
                try:
                    extra = call('POST', '/articles/details', json={'article_ids': ranked})
                    if extra.get('release_id', release) == release:
                        details.update({item['article_id']: item for item in extra['items']})
                except httpx.HTTPError:
                    pass  # The UI stays usable while API and UI roll out independently.
            if not ranked: st.info('No encontramos prendas con estos filtros. Prueba otra combinación.')
            columns = st.columns(4)
            for i, item in enumerate(result['items']):
                article = item['article_id']
                info = details.get(article, {})
                with columns[i % 4], st.container(border=True):
                    name = label(info.get('product_type_name'))
                    photo(article, name)
                    st.markdown(f'**{i+1}. {name}**')
                    if info.get('colour_group_name'): st.caption(label(info['colour_group_name']))
                    st.caption('Ya la compraste' if item['reason'] == 'recent_purchase' else 'Popular esta semana')
                    if previous is not None:
                        if article not in previous: st.caption('Nueva en tu selección')
                        elif previous.index(article) > i: st.caption(f'Subió del puesto {previous.index(article)+1}')
        with explore:
            st.subheader('Elige una prenda para probar')
            st.caption('Simula una compra y después pulsa «Actualizar recomendaciones». Puedes probar con varias prendas.')
            pages = max(1, (catalog['total']+11)//12)
            if st.session_state.get('catalog_page', 1) > pages: st.session_state['catalog_page'] = 1
            page = st.number_input('Página', min_value=1, max_value=pages, value=1, step=1, key='catalog_page')
            if page > 1: catalog = call('GET', '/catalog', params={'offset': (page-1)*12, 'limit': 12})
            if not catalog['items']: st.info('El catálogo estará disponible en unos momentos.')
            columns = st.columns(4)
            for i, item in enumerate(catalog['items']):
                with columns[i % 4], st.container(border=True):
                    article = item['article_id']
                    name = label(item.get('product_type_name'))
                    photo(article, name)
                    st.markdown(f'**{name}**')
                    if item.get('colour_group_name'): st.caption(label(item['colour_group_name']))
                    if st.button('Simular compra', key='catalog_purchase_'+article, disabled=pending, width='stretch'):
                        submit(dict(event_id=str(uuid4()), stream_id=stream, release_id=release, customer_id=customer,
                            article_id=article, kind='purchase', origin=chosen['mode'],
                            event_time=datetime.combine(cutoff-timedelta(days=1), time(12), tzinfo=timezone.utc).isoformat()))
        with st.expander('¿Cómo funciona?'):
            st.write('El sistema pone primero las prendas compradas recientemente y completa la selección con las más populares. '
                     'Si el perfil no tiene compras recientes, empieza por las populares. '
                     'Al simular una compra y actualizar, puedes observar si la prenda aparece o cambia de posición.')
            st.caption('Proyecto universitario de Datos Masivos. Los perfiles representan ejemplos anónimos; no son cuentas personales.')
    except httpx.HTTPError:
        st.error('No pudimos cargar la demostración. Recarga la página en unos momentos; tus compras confirmadas se conservan.')
    finally:
        client.close()


if __name__ == '__main__':
    main()
