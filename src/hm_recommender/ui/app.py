"""Interactive fashion recommendation interface for H&M customers and operators.

Provides personalized recommendations based on recent purchase history (28d)
and weekly popularity (7d), with live simulation of purchases and catalog exploration.
"""

from datetime import date, datetime, time, timedelta, timezone
import os
from uuid import uuid4

import httpx
import streamlit as st

from hm_recommender.ui.i18n import NAMES, label
from hm_recommender.ui.styles import inject_custom_css


def create_api_client(base_url: str | None = None) -> httpx.Client:
    """Create a configured HTTP client for interacting with the recommender API."""
    url = base_url or os.environ.get('HMREC_API_URL', 'http://api:8000')
    return httpx.Client(
        base_url=url,
        trust_env=False,
        timeout=120,
        follow_redirects=False,
    )


def render_photo(client: httpx.Client, article_id: str, name: str) -> None:
    """Fetch and render an article photo or fallback caption."""
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


def main() -> None:
    """Main Streamlit application entry point."""
    st.set_page_config(
        page_title='H&M // Intelligent Styling Nexus',
        page_icon='⚡',
        layout='wide',
        initial_sidebar_state='collapsed',
    )
    inject_custom_css()

    st.markdown(
        """
        <div class="hud-banner">
            <div class="hud-telemetry">
                <span class="hud-status-badge"><span class="hud-pulse"></span> CORE ONLINE</span>
                <span class="hud-chip">MODEL: HEURISTIC (H-28d/7d)</span>
                <span class="hud-chip">INFERENCE: REALTIME DETERMINISTIC</span>
                <span class="hud-chip">SECURITY: TLS / EDGE-SECURE</span>
            </div>
            <h1 class="hud-title-text">H&amp;M // INTELLIGENT STYLING NEXUS</h1>
            <p class="hud-subtitle-text">Motor heurístico de recomendación predictiva y simulación adaptativa en tiempo real.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    client = create_api_client()

    def call_api(method: str, path: str, **kwargs) -> dict:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def submit_purchase(payload: dict) -> None:
        st.session_state['pending'] = payload
        try:
            result = call_api('POST', '/events', json=payload)
            st.session_state.update(
                pending=None,
                pending_terminal=False,
                receipt=result,
                notice=(
                    'success',
                    'Compra simulada guardada. Pulsa «Actualizar recomendaciones» para ver el resultado.',
                ),
            )
        except httpx.HTTPError as error:
            terminal = (
                isinstance(error, httpx.HTTPStatusError)
                and 400 <= error.response.status_code < 500
                and error.response.status_code not in (408, 429)
            )
            st.session_state['pending_terminal'] = terminal
            message = (
                'No se pudo aceptar esta compra. Cierra el intento y vuelve a elegir una prenda.'
                if terminal
                else 'No pudimos confirmar la compra. Reinténtala con el botón de abajo; no se duplicará.'
            )
            st.session_state['notice'] = ('error', message)
        st.rerun()

    try:
        st.session_state.setdefault('pending', None)
        st.session_state.setdefault('visitor', 'demo-' + uuid4().hex)

        if st.session_state.get('notice'):
            level, message = st.session_state.pop('notice')
            getattr(st, level)(message)

        pending = bool(st.session_state['pending'])
        if pending:
            st.warning('Tienes una compra pendiente de confirmación.')
            terminal = st.session_state.get('pending_terminal', False)
            if st.button('Reintentar compra', key='retry', disabled=terminal):
                submit_purchase(st.session_state['pending'])
            if terminal:
                st.caption('Cerrar el intento no deshace una compra que ya se haya guardado.')
                if st.button('Cerrar este intento', key='discard_pending'):
                    st.session_state.update(pending=None, pending_terminal=False)
                    st.rerun()

        call_api('GET', '/health/ready')
        streams = call_api('GET', '/streams')['items']
        current = {s['stream_id']: s for s in streams if s['mode'] in ('simulation', 'synthetic')}
        if not current:
            st.info('La demostración se está preparando. Vuelve en unos momentos.')
            return

        # Stable identity/order across publications; never keep a stale metadata dict.
        saved = st.session_state.get('stream')
        if isinstance(saved, dict):
            saved = saved.get('stream_id')
        st.session_state['stream'] = saved if saved in current else next(iter(current))
        stream_ids = sorted(current)

        if len(current) > 1:
            with st.sidebar:
                st.subheader('Demostración')
                st.selectbox(
                    'Demostración',
                    stream_ids,
                    key='stream',
                    disabled=pending,
                    format_func=lambda sid: f'Demostración {stream_ids.index(sid)+1}',
                )

        stream = st.session_state['stream']
        chosen = current[stream]
        release = chosen['release_id']
        cutoff = date.fromisoformat(chosen['data_as_of'])

        # Keep the chosen examples stable while purchases change their results.
        profile_context = (stream, release, str(cutoff))
        saved_profiles = st.session_state.get('descriptive_profiles', {})
        if saved_profiles.get('context') != profile_context:
            saved_profiles = {'context': profile_context, 'items': []}
            try:
                with st.spinner('Buscando perfiles con estilos reconocibles…'):
                    described = call_api('GET', f'/streams/{stream}/demo-profiles')
                if described.get('release_id') == release:
                    saved_profiles['items'] = described['items']
                    st.session_state['descriptive_profiles'] = saved_profiles
            except httpx.HTTPError:
                st.caption(
                    'Los perfiles de ejemplo no están disponibles por el momento. Puedes probar como nuevo visitante.'
                )

        descriptions = {p['customer_id']: p for p in saved_profiles['items']}
        profiles = [st.session_state['visitor'], *descriptions]
        if st.session_state.get('profile') not in profiles:
            st.session_state['profile'] = profiles[0]

        left, right = st.columns([2, 1], vertical_alignment='bottom')
        customer = left.selectbox(
            '¿Para quién buscamos?',
            profiles,
            key='profile',
            disabled=pending,
            format_func=lambda cid: 'Nuevo visitante' if cid == profiles[0] else descriptions[cid]['label'],
        )
        refresh = right.button(
            'Actualizar recomendaciones',
            key='refresh',
            type='primary',
            disabled=pending,
            width='stretch',
        )
        st.caption('Las compras son simuladas. No se realizan pedidos ni cobros.')

        selected_description = descriptions.get(customer)
        if selected_description:
            st.write(selected_description['description'])
            examples = [
                ' '.join((label(a.get('product_type_name')), label(a.get('colour_group_name'), ''))).strip()
                for a in selected_description['examples']
            ]
            if examples:
                st.caption('Compras de referencia: ' + ' · '.join(examples))
            st.caption(
                'El nombre describe su historial de partida. Tus compras simuladas pueden cambiar la selección.'
            )

        catalog = call_api('GET', '/catalog', params={'offset': 0, 'limit': 12})
        if refresh:
            st.session_state['demo_tab'] = 'Para ti'

        for_you, explore = st.tabs(['Para ti', 'Explorar catálogo'], key='demo_tab', on_change='rerun')

        with for_you:
            filters = {}
            with st.popover('Filtrar recomendaciones'):
                columns = st.columns(2)
                for column, field, field_name in zip(
                    columns,
                    ('product_type_name', 'colour_group_name'),
                    ('Prenda', 'Color'),
                ):
                    options = [None, *catalog['filters'].get(field, [])]
                    filter_key = 'filter_' + field
                    if st.session_state.get(filter_key) not in options:
                        st.session_state[filter_key] = None
                    val = column.selectbox(
                        field_name,
                        options,
                        key=filter_key,
                        format_func=lambda v: label(v, 'Todas'),
                    )
                    if val is not None:
                        filters[field] = val

            context = (stream, customer, tuple(sorted(filters.items())))
            if st.session_state.get('selection_context') != context:
                st.session_state['comparison'] = None
            st.session_state['selection_context'] = context

            if refresh:
                st.session_state['comparison'] = None
                before = st.session_state.get('last_selection')
                try:
                    with st.spinner('Actualizando tu selección…'):
                        call_api(
                            'POST',
                            f'/admin/streams/{stream}/refresh',
                            json={'data_as_of': str(cutoff)},
                            headers={'Authorization': 'Bearer ' + os.environ.get('HM_APP_PASSWORD', '')},
                        )
                    st.session_state['comparison'] = before if before and before['context'] == context else None
                    st.session_state['notice'] = ('success', 'Recomendaciones actualizadas.')
                    st.rerun()
                except httpx.HTTPError:
                    st.error(
                        'No pudimos actualizar la selección. Tus compras guardadas se conservan; '
                        'vuelve a pulsar «Actualizar recomendaciones».'
                    )

            result = call_api(
                'POST',
                f'/streams/{stream}/recommend',
                json={'customer_id': customer, 'k': 12, 'filters': filters},
            )
            ranked = [item['article_id'] for item in result['items']]
            comparison = st.session_state.get('comparison')
            previous = comparison['items'] if comparison and comparison['context'] == context else None
            st.session_state['last_selection'] = {'context': context, 'items': ranked}

            st.subheader('Recomendado para ti')
            st.caption(
                'Sin compras recientes para esta selección: empezamos por las prendas populares.'
                if result['fallback']
                else 'Una selección basada en las compras recientes de este perfil, acompañada de prendas populares.'
            )

            if previous is not None:
                new_items = sum(item not in previous for item in ranked)
                moved_items = sum(
                    item in previous and previous.index(item) != idx for idx, item in enumerate(ranked)
                )
                st.info(
                    f'{new_items} prendas nuevas y {moved_items} cambiaron de posición respecto a la selección anterior.'
                    if previous != ranked
                    else 'La selección se mantiene. Una compra no siempre cambia las prendas o su orden.'
                )

            details = {item['article_id']: item for item in catalog['items']}
            if ranked:
                try:
                    extra = call_api('POST', '/articles/details', json={'article_ids': ranked})
                    if extra.get('release_id', release) == release:
                        details.update({item['article_id']: item for item in extra['items']})
                except httpx.HTTPError:
                    pass  # Keep UI usable while API and UI roll out independently.

            if not ranked:
                st.info('No encontramos prendas con estos filtros. Prueba otra combinación.')

            columns = st.columns(4)
            for idx, item in enumerate(result['items']):
                article = item['article_id']
                info = details.get(article, {})
                with columns[idx % 4], st.container(border=True):
                    item_name = label(info.get('product_type_name'))
                    render_photo(client, article, item_name)
                    st.markdown(f'**{idx + 1}. {item_name}**')
                    if info.get('colour_group_name'):
                        st.caption(label(info['colour_group_name']))
                    reason_badge = (
                        '<span class="cyber-badge cyber-badge-bought">⚡ RECIENTE · HISTORIAL</span>'
                        if item['reason'] == 'recent_purchase'
                        else '<span class="cyber-badge cyber-badge-popular">🔥 TRENDING · 7 DÍAS</span>'
                    )
                    st.markdown(reason_badge, unsafe_allow_html=True)
                    if previous is not None:
                        if article not in previous:
                            st.markdown(
                                '<span class="cyber-badge cyber-badge-new">✨ NUEVA SELECCIÓN</span>',
                                unsafe_allow_html=True,
                            )
                        elif previous.index(article) > idx:
                            st.markdown(
                                f'<span class="cyber-badge cyber-badge-rank">▲ SUBIÓ #{previous.index(article) + 1}</span>',
                                unsafe_allow_html=True,
                            )

        with explore:
            st.subheader('Elige una prenda para probar')
            st.caption(
                'Simula una compra y después pulsa «Actualizar recomendaciones». Puedes probar con varias prendas.'
            )
            total_pages = max(1, (catalog['total'] + 11) // 12)
            if st.session_state.get('catalog_page', 1) > total_pages:
                st.session_state['catalog_page'] = 1

            page = st.number_input(
                'Página',
                min_value=1,
                max_value=total_pages,
                value=1,
                step=1,
                key='catalog_page',
            )
            if page > 1:
                catalog = call_api('GET', '/catalog', params={'offset': (page - 1) * 12, 'limit': 12})

            if not catalog['items']:
                st.info('El catálogo estará disponible en unos momentos.')

            columns = st.columns(4)
            for idx, item in enumerate(catalog['items']):
                with columns[idx % 4], st.container(border=True):
                    article = item['article_id']
                    cat_name = label(item.get('product_type_name'))
                    render_photo(client, article, cat_name)
                    st.markdown(f'**{cat_name}**')
                    if item.get('colour_group_name'):
                        st.caption(label(item['colour_group_name']))
                    st.markdown(
                        f'<span class="cyber-badge cyber-badge-popular">ID: {article[:8]}…</span>',
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        'Simular compra ⚡',
                        key='catalog_purchase_' + article,
                        disabled=pending,
                        width='stretch',
                    ):
                        event_time = datetime.combine(
                            cutoff - timedelta(days=1),
                            time(12),
                            tzinfo=timezone.utc,
                        ).isoformat()
                        submit_purchase(
                            dict(
                                event_id=str(uuid4()),
                                stream_id=stream,
                                release_id=release,
                                customer_id=customer,
                                article_id=article,
                                kind='purchase',
                                origin=chosen['mode'],
                                event_time=event_time,
                            )
                        )

        with st.expander('¿Cómo funciona?'):
            st.write(
                'El sistema pone primero las prendas compradas recientemente y completa la selección con las más '
                'populares. Si el perfil no tiene compras recientes, empieza por las populares. '
                'Al simular una compra y actualizar, puedes observar si la prenda aparece o cambia de posición.'
            )
            st.caption(
                'Proyecto universitario de Datos Masivos. Los perfiles representan ejemplos anónimos; '
                'no son cuentas personales.'
            )
            st.caption(
                'Los estilos se eligen por compras observadas. Moda masculina y femenina son categorías del '
                'catálogo, no el género de quien compra. Estos ejemplos ilustrativos no representan a todos los '
                'compradores.'
            )

    except httpx.HTTPError:
        st.error(
            'No pudimos cargar la demostración. Recarga la página en unos momentos; tus compras confirmadas se '
            'conservan.'
        )
    finally:
        client.close()


if __name__ == '__main__':
    main()
