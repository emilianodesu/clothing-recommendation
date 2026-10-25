"""Exercise Streamlit against real HTTP and PostgreSQL, including a lost receipt."""
import os
from pathlib import Path
import socket
import threading
import time
import pytest
import uvicorn
from test_events import events
from test_recommendations import serving

pytestmark=pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS')!='1',reason='requires PostgreSQL')


def test_ui_purchase_retry_and_refresh_use_real_service(events,monkeypatch):
    from hm_recommender.api.app import create_app
    from hm_recommender.events.store import EventStore
    source=Path('/workspace/src' if Path('/workspace/src').exists() else '/opt/hmrec/src')/'hm_recommender/ui/app.py'
    assert source.exists(), 'T5 must provide the runnable UI'
    from streamlit.testing.v1 import AppTest
    serving,stream=events
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(serving[1],admin_token=os.environ['HM_APP_PASSWORD']),lifespan='off',access_log=False,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    monkeypatch.setenv('HMREC_API_URL',f'http://127.0.0.1:{port}')
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.01)
        assert server.started
        app=AppTest.from_file(str(source),default_timeout=30).run()
        assert not app.exception
        customer = app.session_state['visitor']
        original=EventStore.insert
        def lose_reply(self,payload):
            original(self,payload);raise OSError('lost reply')
        monkeypatch.setattr(EventStore,'insert',lose_reply)
        app.button(key='catalog_purchase_000000').click().run()
        assert app.error and app.session_state['pending']['customer_id']==customer
        saved=app.session_state['pending']['event_id']
        monkeypatch.setattr(EventStore,'insert',original)
        app.button(key='retry').click().run()
        assert app.session_state['pending'] is None
        with serving[4]() as conn:
            rows=conn.execute('SELECT event_id FROM interaction_events').fetchall()
            assert [str(r[0]) for r in rows]==[saved]
        app.button(key='refresh').click().run()
        assert not app.exception
        response=serving[0].post(f'/streams/{stream}/recommend',json={'customer_id':customer}).json()
        assert response['fallback'] is False and response['items'][0]['article_id']=='000000'
        assert app.caption  # Human-readable purchase reasons stay visible.
        # A permanent 409 must not trap every future interaction behind retry.
        from hm_recommender.events.store import Conflict
        def conflict(self,payload):raise Conflict('version changed')
        monkeypatch.setattr(EventStore,'insert',conflict)
        app.button(key='catalog_purchase_000000').click().run()
        assert app.error and app.session_state['pending'] is not None
        assert app.button(key='retry').disabled
        app.button(key='discard_pending').click().run()
        assert app.session_state['pending'] is None and not app.button(key='catalog_purchase_000000').disabled
        monkeypatch.setattr(EventStore,'insert',original)
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close()
