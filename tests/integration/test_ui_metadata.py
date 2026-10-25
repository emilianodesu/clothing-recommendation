"""Streamlit user journeys with synthetic HTTP only; no real events or evaluation."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

SOURCE = Path(os.environ.get('TEST_UI_SOURCE', str(Path(__file__).resolve().parents[2]/'src/hm_recommender/ui/app.py')))
IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="30"><rect width="30" height="30" fill="gray"/></svg>'


def demo(handler_override=None):
    state = {'revision': 3, 'refresh': [], 'events': [], 'published': False, 'fail_event': False, 'fail_refresh': False}
    articles = [dict(article_id=f'{i:010}', product_type_name='T-shirt', colour_group_name='Blue', product_group_name='Garment Upper body') for i in range(1, 4)]
    def handler(request):
        if handler_override:
            response = handler_override(request, state)
            if response is not None: return response
        path = request.url.path
        if path == '/health/ready': value = {'status': 'ready'}
        elif path == '/streams':
            value = {'items': [dict(stream_id=n, release_id='release-'+str(state['revision']), mode='synthetic', data_as_of='2020-09-09', revision=state['revision'], published_at='publication-private', received_before='cursor-private') for n in ('a', 'b')]}
        elif path == '/customers': value = {'items': ['private-buyer-hash']}
        elif path.endswith('/demo-profiles'):
            value = {'release_id': 'release-'+str(state['revision']), 'items': [dict(
                customer_id='private-buyer-hash', key='light', label='Tonos claros',
                description='Predominan los tonos claros en sus compras de referencia.',
                history_count=5, matched_count=4,
                examples=[dict(article_id='0000000001',product_type_name='T-shirt',colour_group_name='White')])]}
        elif path == '/catalog': value = {'items': articles, 'total': 3, 'filters': {k: [] for k in ('product_type_name', 'product_group_name', 'colour_group_name')}}
        elif path == '/articles/details': value = {'items': articles}
        elif path.endswith('/image'): return httpx.Response(200, text=IMAGE, headers={'content-type': 'image/svg+xml'})
        elif path.endswith('/recommend'):
            order = [2, 1] if state['published'] else [1, 2]
            value = {'fallback': not state['published'], 'items': [dict(article_id=f'{n:010}', rank=i+1, reason='recent_purchase' if state['published'] and i == 0 else 'popular_7d') for i, n in enumerate(order)]}
        elif path.endswith('/refresh'):
            state['refresh'].append((path, json.loads(request.content)))
            if state['fail_refresh']: return httpx.Response(503)
            state['revision'] += 1; state['published'] = True; value = {}
        elif path == '/events':
            state['events'].append(json.loads(request.content))
            if state['fail_event']: return httpx.Response(503)
            value = {'status': 'created'}
        else: raise AssertionError(path)
        return httpx.Response(200, json=value)
    original = httpx.Client
    def client(**kwargs): return original(**kwargs, transport=httpx.MockTransport(handler))
    return state, patch('httpx.Client', side_effect=client)


def visible_text(app):
    return '\n'.join(str(item.value) for kind in ('title', 'subheader', 'caption', 'markdown', 'info', 'success', 'warning', 'error') for item in getattr(app, kind))


def test_demo_has_photos_friendly_profiles_and_no_operational_metadata():
    state, transport = demo()
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        assert not app.exception
        assert app.selectbox(key='profile').options == ['Nuevo visitante', 'Tonos claros']
        assert len(app.get('image')) == 5
        assert 'Camiseta' in visible_text(app)
        for private in ('release-3', 'publication-private', 'cursor-private', 'private-buyer-hash', '0000000001'):
            assert private not in visible_text(app)
        assert not app.text_input and not app.date_input and not app.dataframe
        assert state['events'] == []


def test_refresh_uses_current_metadata_and_keeps_selected_demo():
    state, transport = demo()
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        app.selectbox(key='stream').select_index(1).run()
        app.button(key='refresh').click().run()
        assert not app.exception
        assert state['refresh'] == [('/admin/streams/b/refresh', {'data_as_of': '2020-09-09'})]
        app.button(key='catalog_purchase_0000000002').click().run()
        event = state['events'][-1]
        assert event['release_id'] == 'release-4' and event['stream_id'] == 'b'
        assert event['kind'] == 'purchase' and event['event_time'].startswith('2020-09-08T12:00:00')
        assert 'release-4' not in visible_text(app)


def test_lost_receipt_retries_same_purchase_and_refresh_shows_actual_change():
    state, transport = demo()
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        visitor = app.session_state['visitor']
        state['fail_event'] = True
        app.button(key='catalog_purchase_0000000002').click().run()
        assert app.error and app.session_state['pending']
        assert app.selectbox(key='profile').disabled
        assert app.button(key='refresh').disabled
        state['fail_event'] = False
        app.button(key='retry').click().run()
        assert state['events'][0] == state['events'][1]
        assert state['events'][0]['customer_id'] == visitor
        assert app.session_state['pending'] is None
        app.button(key='refresh').click().run()
        assert not app.exception
        assert 'cambiaron de posición' in visible_text(app)
        assert 'Subió del puesto 2' in visible_text(app)
        assert app.session_state['demo_tab'] == 'Para ti'
        app.selectbox(key='profile').select_index(1).run()
        assert 'cambiaron de posición' not in visible_text(app)


def test_unavailable_image_and_refresh_do_not_hide_the_catalog_or_claim_success():
    def unavailable(request, state):
        if request.url.path.endswith('/image'): raise httpx.ReadTimeout('image unavailable', request=request)
    state, transport = demo(unavailable)
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        assert not app.exception and app.button(key='catalog_purchase_0000000002')
        assert 'Foto no disponible' in visible_text(app)
        state['fail_refresh'] = True
        app.button(key='refresh').click().run()
        assert app.error and not app.exception
        assert 'Recomendaciones actualizadas' not in visible_text(app)
        assert app.button(key='catalog_purchase_0000000002')


def test_failed_second_refresh_clears_prior_comparison_and_keeps_purchase_retryable():
    state, transport = demo()
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        app.button(key='refresh').click().run()
        assert 'cambiaron de posición' in visible_text(app)
        state['fail_refresh'] = True
        app.button(key='refresh').click().run()
        assert app.error and 'cambiaron de posición' not in visible_text(app)
        assert app.button(key='catalog_purchase_0000000002').disabled is False


def test_terminal_purchase_conflict_can_be_closed_without_resending():
    def conflict(request, state):
        if request.url.path == '/events': return httpx.Response(409)
    state, transport = demo(conflict)
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        app.button(key='catalog_purchase_0000000002').click().run()
        assert app.error and app.session_state['pending']
        assert app.button(key='retry').disabled
        app.button(key='discard_pending').click().run()
        assert app.session_state['pending'] is None
        assert app.button(key='catalog_purchase_0000000002').disabled is False


def test_older_api_without_article_details_keeps_images_and_purchases_available():
    def old_api(request, state):
        if request.url.path == '/articles/details': return httpx.Response(404)
    state, transport = demo(old_api)
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        assert not app.exception and len(app.get('image')) == 5
        assert app.button(key='catalog_purchase_0000000002')


def test_unchanged_result_is_reported_honestly():
    state, transport = demo()
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        app.button(key='refresh').click().run()
        app.button(key='refresh').click().run()
        assert not app.exception
        assert 'La selección se mantiene.' in visible_text(app)
        app.selectbox(key='profile').select_index(1).run()
        app.selectbox(key='profile').select_index(0).run()
        assert 'La selección se mantiene.' not in visible_text(app)


def test_descriptive_profile_selects_its_real_buyer_and_keeps_it_after_refresh():
    state, transport = demo()
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        app.selectbox(key='profile').select_index(1).run()
        assert 'Predominan los tonos claros' in visible_text(app)
        assert 'Camiseta' in visible_text(app) and 'Blanco' in visible_text(app)
        app.button(key='catalog_purchase_0000000002').click().run()
        assert state['events'][-1]['customer_id'] == 'private-buyer-hash'
        app.button(key='refresh').click().run()
        assert app.selectbox(key='profile').value == 'private-buyer-hash'
        assert 'private-buyer-hash' not in visible_text(app)


def test_profile_service_failure_keeps_new_visitor_and_purchase_working():
    def unavailable(request, state):
        if request.url.path.endswith('/demo-profiles'): return httpx.Response(503)
    state, transport = demo(unavailable)
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        assert not app.exception
        assert app.selectbox(key='profile').options == ['Nuevo visitante']
        app.button(key='catalog_purchase_0000000002').click().run()
        assert state['events'][-1]['customer_id'] == app.session_state['visitor']


def test_refresh_keeps_reference_profile_when_a_new_revision_would_choose_another_buyer():
    def changing_candidates(request, state):
        if request.url.path == '/streams':
            return httpx.Response(200, json={'items': [dict(stream_id='a', release_id='fixed-release',
                mode='synthetic', data_as_of='2020-09-09', revision=state['revision'])]})
        if request.url.path.endswith('/demo-profiles'):
            changed = state['revision'] > 3
            return httpx.Response(200, json={'release_id': 'fixed-release', 'items': [dict(
                customer_id='replacement' if changed else 'reference', key='dark', label='Tonos oscuros',
                description='Compras de referencia oscuras.', history_count=4, matched_count=4, examples=[])]})
    state, transport = demo(changing_candidates)
    with transport:
        app = AppTest.from_file(str(SOURCE), default_timeout=20).run()
        app.selectbox(key='profile').select_index(1).run()
        app.button(key='refresh').click().run()
        assert not app.exception and state['revision'] == 4
        assert app.selectbox(key='profile').value == 'reference'
        app.button(key='catalog_purchase_0000000002').click().run()
        assert state['events'][-1]['customer_id'] == 'reference'
