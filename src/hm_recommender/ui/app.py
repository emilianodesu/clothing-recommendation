"""Interactive fashion recommendation interface for H&M customers and operators.

Provides personalized recommendations powered by v2 two-stage retrieval & ranking
(and backward-compatible v1 history releases), live shopper actions (view, favorite,
purchase), cold-start onboarding, balanced vs discovery modes, and searchable catalog.
"""

from datetime import date, datetime, time, timedelta, timezone
import os
from uuid import uuid4

import httpx
import streamlit as st

from hm_recommender.ui.i18n import label, section_label
from hm_recommender.ui.styles import inject_custom_css

SECTION_OPTIONS = [None, 'Ladieswear', 'Menswear', 'Divided', 'Sport', 'Baby/Children']
AGE_BIN_OPTIONS = [None, '<25', '25-34', '35-44', '45-54', '55+']

REASON_BADGES = {
    'recent_purchase': ('TU HISTORIAL', 'atelier-badge-recent'),
    'same_product_variant': ('OTRO COLOR DE TU PRENDA', 'atelier-badge-variant'),
    'frequently_bought_together': ('COMBÍNALO CON TUS PRENDAS', 'atelier-badge-cooc'),
    'latent_style_match': ('ESTILO AFÍN', 'atelier-badge-style'),
    'content_similar': ('PRENDA SIMILAR', 'atelier-badge-style'),
    'popular_for_segment': ('TENDENCIA PARA TI', 'atelier-badge-popular'),
    'popular_7d': ('TENDENCIA 7 DÍAS', 'atelier-badge-popular'),
}


def create_api_client(base_url: str | None = None) -> httpx.Client:
    """Create a configured HTTP client for interacting with the recommender API."""
    url = base_url or os.environ.get('HMREC_API_URL', 'http://api:8000')
    return httpx.Client(
        base_url=url,
        trust_env=False,
        timeout=120,
        follow_redirects=False,
    )


@st.cache_data(ttl=60, show_spinner=False, max_entries=128)
def _fetch_catalog_cached(
    _client: httpx.Client,
    base_url: str,
    release_id: str,
    q: str,
    index_group_name: str,
    offset: int,
    limit: int,
) -> dict:
    """Fetch a catalog page with optional keyword search and section filter."""
    params: dict[str, object] = {'offset': offset, 'limit': limit}
    if q:
        params['q'] = q
    if index_group_name:
        params['index_group_name'] = index_group_name
    response = _client.get('/catalog', params=params)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=300, show_spinner=False, max_entries=512)
def _fetch_image_cached(
    _client: httpx.Client,
    base_url: str,
    release_id: str,
    article_id: str,
) -> tuple[bytes, str]:
    """Fetch and cache an article image, raising on HTTP/non-image failures."""
    response = _client.get(f'/articles/{article_id}/image', timeout=8)
    response.raise_for_status()
    mime = response.headers.get('content-type', '')
    if not mime.startswith('image/'):
        raise ValueError('Non-image response')
    return response.content, mime


def render_photo(client: httpx.Client, article_id: str, name: str, release_id: str = '') -> None:
    """Fetch and render an article photo or fallback caption."""
    try:
        body, mime = _fetch_image_cached(client, str(client.base_url), release_id, article_id)
        if mime.startswith('image/svg'):
            st.image(body.decode('utf-8', errors='replace'), width='stretch', alt=name)
        else:
            st.image(body, width='stretch', alt=name)
    except (httpx.HTTPError, ValueError, OSError):
        st.caption('Foto no disponible')


def _set_catalog_page(page: int) -> None:
    st.session_state['catalog_page'] = page


def main() -> None:
    """Main Streamlit application entry point."""
    st.set_page_config(
        page_title='Recomendaciones de vestuario',
        page_icon='◻',
        layout='wide',
        initial_sidebar_state='collapsed',
    )
    inject_custom_css()

    st.markdown(
        """
        <div class="minimal-header">
            <div>
                <h1 class="minimal-title">Recomendaciones de vestuario</h1>
                <p class="minimal-subtitle">Selección personalizada según historial, afinidad de estilo y señales en vivo.</p>
            </div>
            <a href="/logout" target="_self" class="logout-btn">Cerrar sesión</a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    client = create_api_client()

    def call_api(method: str, path: str, **kwargs) -> dict:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    try:
        st.session_state.setdefault('pending', None)
        st.session_state.setdefault('visitor', 'demo-' + uuid4().hex)

        if st.session_state.get('notice'):
            level, message = st.session_state.pop('notice')
            getattr(st, level)(message)

        if st.session_state.pop('switch_to_for_you', False):
            st.session_state['demo_tab'] = 'Para ti'

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
                    disabled=bool(st.session_state['pending']),
                    format_func=lambda sid: f'Demostración {stream_ids.index(sid)+1}',
                )

        stream = st.session_state['stream']
        chosen = current[stream]
        release = chosen['release_id']
        cutoff = date.fromisoformat(chosen['data_as_of'])

        # Keep the chosen examples stable while interactions change their results.
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

        pending = bool(st.session_state['pending'])

        def record_session_action(kind: str, article_id: str | None) -> None:
            if not article_id:
                return
            cid = st.session_state.get('profile', profiles[0])
            store = st.session_state.setdefault('session_activity', {})
            bucket = store.setdefault((stream, cid), {'view': [], 'click': [], 'purchase': []})
            bucket[kind].append(article_id)

        def submit_purchase(payload: dict) -> None:
            st.session_state['pending'] = payload
            try:
                result = call_api('POST', '/events', json=payload)
                simulated = st.session_state.setdefault('simulated_history', [])
                simulated.append(payload.get('article_id'))
                record_session_action('purchase', payload.get('article_id'))
                st.session_state.update(
                    pending=None,
                    pending_terminal=False,
                    receipt=result,
                    switch_to_for_you=True,
                    auto_refresh_trigger=True,
                    notice=(
                        'success',
                        'Compra simulada guardada. Mostrando tu selección actualizada.',
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

        def submit_action(kind: str, article_id: str, *, switch_to_for_you: bool = False) -> None:
            event_time = datetime.combine(
                cutoff - timedelta(days=1),
                time(12),
                tzinfo=timezone.utc,
            ).isoformat()
            payload = dict(
                event_id=str(uuid4()),
                stream_id=stream,
                release_id=release,
                customer_id=st.session_state['profile'],
                article_id=article_id,
                kind=kind,
                origin=chosen['mode'],
                event_time=event_time,
            )
            if kind == 'purchase':
                submit_purchase(payload)
                return
            try:
                call_api('POST', '/events', json=payload)
                record_session_action(kind, article_id)
                if kind == 'view':
                    st.session_state['detail_article_id'] = article_id
                    st.session_state['notice'] = (
                        'info',
                        f'Mostrando detalle de la prenda ART. {article_id}.',
                    )
                elif kind == 'click':
                    st.session_state['notice'] = (
                        'success',
                        f'Prenda ART. {article_id} agregada a favoritos. Actualizando recomendaciones.',
                    )
                if switch_to_for_you:
                    st.session_state['switch_to_for_you'] = True
                st.session_state['auto_refresh_trigger'] = True
                st.rerun()
            except httpx.HTTPError:
                st.session_state['notice'] = (
                    'warning',
                    'No pudimos registrar tu interacción en este momento. Intenta de nuevo.',
                )
                st.rerun()

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

        active_profile_ctx = (stream, customer)
        if st.session_state.get('active_profile_context') != active_profile_ctx:
            st.session_state['active_profile_context'] = active_profile_ctx
            st.session_state['detail_article_id'] = None

        preferences: dict[str, str] = {}
        if customer == profiles[0]:
            onb_left, onb_right = st.columns(2)
            selected_group = onb_left.selectbox(
                'Sección de interés',
                SECTION_OPTIONS,
                key='onboard_group',
                disabled=pending,
                format_func=lambda v: section_label(v, 'Sin preferencia'),
            )
            selected_age = onb_right.selectbox(
                'Rango de edad',
                AGE_BIN_OPTIONS,
                key='age_bin',
                disabled=pending,
                format_func=lambda v: 'Sin especificar' if v is None else (
                    str(v) if str(v).endswith('años') else f'{v} años'
                ),
            )
            if selected_age:
                preferences['age_bin'] = selected_age
            if selected_group:
                preferences['index_group_name'] = selected_group
            if preferences:
                pref_parts = []
                if 'index_group_name' in preferences:
                    pref_parts.append(section_label(preferences['index_group_name']))
                if 'age_bin' in preferences:
                    pref_parts.append(f"{preferences['age_bin']} años")
                st.caption('Preferencias de bienvenida: ' + ' · '.join(pref_parts))

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
                'El nombre describe su historial de partida. Tus interacciones simuladas actualizan la selección.'
            )

        # Single /catalog fetch per active query/filter/page state before rendering tabs.
        q_val = str(st.session_state.get('catalog_q', '') or '').strip()
        ig_val = st.session_state.get('index_group_name') or ''
        if (q_val, ig_val) != st.session_state.get('prev_catalog_query'):
            st.session_state['catalog_page'] = 1
            st.session_state['prev_catalog_query'] = (q_val, ig_val)

        current_page = max(1, int(st.session_state.get('catalog_page', 1)))
        offset = (current_page - 1) * 12
        catalog = _fetch_catalog_cached(client, str(client.base_url), release, q_val, ig_val, offset, 12)
        total_pages = max(1, (catalog['total'] + 11) // 12)
        if current_page > total_pages:
            current_page = 1
            st.session_state['catalog_page'] = 1
            catalog = _fetch_catalog_cached(client, str(client.base_url), release, q_val, ig_val, 0, 12)

        filters_by_release = st.session_state.setdefault('catalog_filters_by_release', {})
        if catalog.get('filters'):
            filters_by_release[release] = catalog['filters']
        catalog_filters = catalog.get('filters') or filters_by_release.get(release, {})

        meta_cache = st.session_state.setdefault('catalog_meta_cache', {})
        for cat_item in catalog.get('items', []):
            meta_cache[cat_item['article_id']] = cat_item

        if refresh:
            st.session_state['demo_tab'] = 'Para ti'

        for_you, explore = st.tabs(['Para ti', 'Explorar catálogo'], key='demo_tab', on_change='rerun')

        with for_you:
            mode_col, filter_col = st.columns([2, 1], vertical_alignment='bottom')
            rec_mode = mode_col.radio(
                'Modo de recomendación',
                ('balanced', 'discovery'),
                key='mode',
                horizontal=True,
                disabled=pending,
                format_func=lambda m: 'Equilibrado' if m == 'balanced' else 'Solo descubrimiento',
            )

            filters: dict[str, str] = {}
            with filter_col, st.popover('Filtrar recomendaciones'):
                columns = st.columns(2)
                for column, field, field_name, filter_key in zip(
                    columns,
                    ('product_type_name', 'colour_group_name'),
                    ('Prenda', 'Color'),
                    ('rec_filter_type', 'rec_filter_colour'),
                ):
                    options = [None, *catalog_filters.get(field, [])]
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

            context = (
                stream,
                customer,
                tuple(sorted(filters.items())),
                rec_mode,
                tuple(sorted(preferences.items())),
            )
            if st.session_state.get('selection_context') != context:
                st.session_state['comparison'] = None
            st.session_state['selection_context'] = context

            auto_refresh = st.session_state.pop('auto_refresh_trigger', False)
            if refresh or auto_refresh:
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
                    if not st.session_state.get('notice'):
                        st.session_state['notice'] = (
                            'success',
                            'Recomendaciones actualizadas con tu última selección.',
                        )
                    st.rerun()
                except httpx.HTTPError:
                    st.error(
                        'No pudimos actualizar la selección. Tus compras guardadas se conservan; '
                        'vuelve a pulsar «Actualizar recomendaciones».'
                    )

            rec_payload: dict[str, object] = {
                'customer_id': customer,
                'k': 12,
                'filters': filters,
                'mode': rec_mode,
            }
            if preferences:
                rec_payload['preferences'] = preferences

            result = call_api('POST', f'/streams/{stream}/recommend', json=rec_payload)
            ranked = [item['article_id'] for item in result['items']]
            comparison = st.session_state.get('comparison')
            previous = comparison['items'] if comparison and comparison['context'] == context else None
            st.session_state['last_selection'] = {'context': context, 'items': ranked}

            st.markdown(
                '<h2 style="font-size: 22px; font-weight: 500; margin: 1.2rem 0 0.2rem 0;">Recomendado para ti</h2>',
                unsafe_allow_html=True,
            )
            if result['fallback']:
                st.caption(
                    'Sin compras recientes para esta selección: mostramos tendencias personalizadas o populares.'
                )
            elif rec_mode == 'discovery':
                st.caption(
                    'Modo Solo descubrimiento: excluye prendas ya compradas y evita variantes repetidas.'
                )
            else:
                st.caption(
                    'Selección híbrida basada en tu historial, prendas afines y señales recientes de la sesión.'
                )

            if previous is not None:
                new_items = sum(item not in previous for item in ranked)
                moved_items = sum(
                    item in previous and previous.index(item) != idx for idx, item in enumerate(ranked)
                )
                st.info(
                    f'{new_items} prendas nuevas y {moved_items} cambiaron de posición respecto a la selección anterior.'
                    if previous != ranked
                    else 'La selección se mantiene. Una interacción no siempre cambia las prendas o su orden.'
                )

            details = dict(meta_cache)
            if ranked:
                try:
                    extra = call_api('POST', '/articles/details', json={'article_ids': ranked})
                    if extra.get('release_id', release) == release:
                        for det_item in extra['items']:
                            aid = det_item['article_id']
                            merged = {**det_item, **meta_cache.get(aid, {})}
                            details[aid] = merged
                            meta_cache[aid] = merged
                except httpx.HTTPError:
                    pass  # Keep UI usable while API and UI roll out independently.

            detail_aid = st.session_state.get('detail_article_id')
            if detail_aid:
                detail_info = details.get(detail_aid) or meta_cache.get(detail_aid, {})
                with st.container(border=True):
                    d_left, d_right = st.columns([1, 3], vertical_alignment='top')
                    with d_left:
                        render_photo(client, detail_aid, label(detail_info.get('product_type_name')), release)
                    with d_right:
                        title_txt = detail_info.get('prod_name') or label(detail_info.get('product_type_name'))
                        st.markdown(f'### Detalle de la prenda: {title_txt}')
                        st.caption(f'ART. {detail_aid}')
                        st.write(
                            f"**Sección:** {section_label(detail_info.get('index_group_name'), 'Catálogo general')} · "
                            f"**Tipo:** {label(detail_info.get('product_type_name'))} · "
                            f"**Color:** {label(detail_info.get('colour_group_name'), 'Sin especificar')}"
                        )
                        st.write(
                            f"**Estampado:** {label(detail_info.get('graphical_appearance_name'), 'Sin especificar')}"
                        )
                        st.write(
                            f"**Descripción:** {detail_info.get('detail_desc') or 'Prenda seleccionada del catálogo.'}"
                        )
                        if st.button('Cerrar detalle', key='close_detail'):
                            st.session_state['detail_article_id'] = None
                            st.rerun()

            if not ranked:
                if rec_mode == 'discovery':
                    st.info(
                        'No encontramos nuevas prendas en modo Solo descubrimiento con estos filtros. '
                        'Prueba otra combinación o cambia a Equilibrado.'
                    )
                else:
                    st.info('No encontramos prendas con estos filtros. Prueba otra combinación.')

            columns = st.columns(4)
            for idx, item in enumerate(result['items']):
                article = item['article_id']
                info = details.get(article, {})
                with columns[idx % 4], st.container(border=True):
                    item_name = label(info.get('product_type_name'))
                    render_photo(client, article, item_name, release)
                    st.markdown(f'**{idx + 1}. {item_name}**')
                    if info.get('colour_group_name'):
                        st.caption(label(info['colour_group_name']))
                    st.caption(f'ART. {article}')
                    badge_label, badge_css = REASON_BADGES.get(
                        item['reason'],
                        ('TENDENCIA 7 DÍAS', 'atelier-badge-popular'),
                    )
                    st.markdown(
                        f'<span class="atelier-badge {badge_css}">{badge_label}</span>',
                        unsafe_allow_html=True,
                    )
                    if previous is not None:
                        if article not in previous:
                            st.markdown(
                                '<span class="atelier-badge atelier-badge-new">NUEVA SELECCIÓN</span>',
                                unsafe_allow_html=True,
                            )
                        elif previous.index(article) > idx:
                            st.markdown(
                                f'<span class="atelier-badge atelier-badge-rank">SUBIÓ #{previous.index(article) + 1}</span>',
                                unsafe_allow_html=True,
                            )
                    b_det, b_fav, b_buy = st.columns(3)
                    if b_det.button('Ver detalle', key=f'rec_view_{idx}_{article}', disabled=pending, width='stretch'):
                        submit_action('view', article)
                    if b_fav.button('♡ Favorito', key=f'rec_click_{idx}_{article}', disabled=pending, width='stretch'):
                        submit_action('click', article)
                    if b_buy.button('Comprar', key=f'rec_purchase_{idx}_{article}', disabled=pending, width='stretch'):
                        submit_action('purchase', article)

            activity_store = st.session_state.setdefault('session_activity', {})
            activity = activity_store.get((stream, customer), {'view': [], 'click': [], 'purchase': []})
            with st.container(border=True):
                st.markdown('### Tu actividad en esta sesión')
                st.write(
                    f"**Vistos:** {len(activity['view'])} · "
                    f"**Favoritos:** {len(activity['click'])} · "
                    f"**Comprados:** {len(activity['purchase'])}"
                )
                recent_entries: list[str] = []
                for kind_key, kind_label in (('purchase', 'Compra'), ('click', 'Favorito'), ('view', 'Vista')):
                    for aid in reversed(activity[kind_key][-3:]):
                        meta_item = details.get(aid) or meta_cache.get(aid, {})
                        recent_entries.append(
                            f"{kind_label}: {label(meta_item.get('product_type_name'))} (ART. {aid})"
                        )
                if recent_entries:
                    st.caption(' · '.join(recent_entries[:6]))
                else:
                    st.caption(
                        'Aún no registras interacciones en esta sesión. '
                        'Usa Ver detalle, ♡ Favorito o Comprar en cualquier prenda.'
                    )

        with explore:
            st.markdown(
                '<h2 style="font-size: 22px; font-weight: 500; margin: 1.2rem 0 0.2rem 0;">Elige una prenda para probar</h2>',
                unsafe_allow_html=True,
            )
            st.caption(
                'Explora el catálogo por palabra clave o sección e interactúa con las prendas para actualizar tus recomendaciones.'
            )

            q_col, sec_col = st.columns([2, 1], vertical_alignment='bottom')
            q_col.text_input(
                'Buscar en el catálogo',
                key='catalog_q',
                placeholder='Ej. Dress, Blazer, Blue...',
            )
            sec_col.selectbox(
                'Sección del catálogo',
                SECTION_OPTIONS,
                key='index_group_name',
                format_func=lambda v: section_label(v, 'Todas'),
            )

            simulated = st.session_state.get('simulated_history', [])
            if simulated:
                c1, c2 = st.columns([3, 1], vertical_alignment='center')
                c1.info(f'Has simulado {len(simulated)} compra(s) en esta sesión.')
                if c2.button('Ver recomendaciones →', key='btn_goto_for_you', type='primary', width='stretch'):
                    st.session_state['switch_to_for_you'] = True
                    st.session_state['auto_refresh_trigger'] = True
                    st.rerun()

            col_prev, col_status, col_next = st.columns([1, 2, 1], vertical_alignment='center')
            col_prev.button(
                '← Anterior',
                key='btn_prev_page',
                disabled=current_page <= 1,
                width='stretch',
                on_click=_set_catalog_page,
                args=(max(1, current_page - 1),),
            )
            col_status.markdown(
                f"<div style='text-align: center; font-size: 11.5px; font-weight: 600; letter-spacing: 0.12em; color: var(--atelier-muted); padding: 8px 0;'>"
                f"PÁGINA {current_page} DE {total_pages}</div>",
                unsafe_allow_html=True,
            )
            col_next.button(
                'Siguiente →',
                key='btn_next_page',
                disabled=current_page >= total_pages,
                width='stretch',
                on_click=_set_catalog_page,
                args=(min(total_pages, current_page + 1),),
            )

            if not catalog['items']:
                if q_val or ig_val:
                    st.info(
                        'Sin resultados en el catálogo para esa búsqueda o sección. '
                        'Prueba otra palabra clave o selecciona Todas.'
                    )
                else:
                    st.info('El catálogo estará disponible en unos momentos.')

            columns = st.columns(4)
            for idx, item in enumerate(catalog['items']):
                with columns[idx % 4], st.container(border=True):
                    article = item['article_id']
                    cat_name = label(item.get('product_type_name'))
                    render_photo(client, article, cat_name, release)
                    st.markdown(f'**{cat_name}**')
                    caption_parts = []
                    if item.get('colour_group_name'):
                        caption_parts.append(label(item['colour_group_name']))
                    if item.get('index_group_name'):
                        caption_parts.append(section_label(item['index_group_name']))
                    if caption_parts:
                        st.caption(' · '.join(caption_parts))
                    st.markdown(
                        f'<span class="atelier-badge atelier-badge-popular">ART. {article}</span>',
                        unsafe_allow_html=True,
                    )
                    c_det, c_fav, c_buy = st.columns(3)
                    if c_det.button(
                        'Ver detalle',
                        key='catalog_view_' + article,
                        disabled=pending,
                        width='stretch',
                    ):
                        submit_action('view', article, switch_to_for_you=True)
                    if c_fav.button(
                        '♡ Favorito',
                        key='catalog_click_' + article,
                        disabled=pending,
                        width='stretch',
                    ):
                        submit_action('click', article)
                    if c_buy.button(
                        'Comprar',
                        key='catalog_purchase_' + article,
                        disabled=pending,
                        width='stretch',
                    ):
                        submit_action('purchase', article, switch_to_for_you=True)

        with st.expander('¿Cómo funciona?'):
            st.write(
                'El motor v2 combina cinco generadores de candidatos (historial reciente con decaimiento temporal, '
                'variantes de color, coocurrencia, similitud latente/contenido y tendencias por segmento) y un '
                'ranking LightGBM entrenado por semanas. Las interacciones en vivo (vista, favorito y compra) '
                'actualizan el vector de sesión y las recomendaciones.'
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
