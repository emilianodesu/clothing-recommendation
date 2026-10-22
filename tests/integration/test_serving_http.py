import os
import socket
import threading
import time
import httpx
import pytest
import uvicorn
from test_recommendations import serving, activate

pytestmark=pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS')!='1',reason='requires isolated PostgreSQL')


def test_real_http_serves_version_and_filters(serving):
    from hm_recommender.api.app import create_app
    _,registry,a,_,_,tmp=serving; activate(serving)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(registry,admin_token='test',input_root=tmp/'input'),
        lifespan='off',access_log=False,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline: time.sleep(.01)
        assert server.started
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as client:
            r=client.post('/recommend',json={'customer_id':'buyer','k':3,'filters':{'colour_group_name':'red'}})
            assert r.status_code==200
            assert r.json()['release_id']==a.release_id
            assert [i['article_id'] for i in r.json()['items']]==['000250','000251','000252']
        from hm_recommender.api.probe import probe
        from hm_recommender.api.runtime import source_digest
        report=probe(a._root,a.release_id,f'http://127.0.0.1:{port}',source_digest())
        assert report['http_parity'] is True and report['checked_requests']>report['sampled_buyers']
        assert report['past_buyers']==3 and report['catalog_size']==255
        assert 'buyer' not in __import__('json').dumps(report).replace('buyers','')
        host_report=probe(a._root,a.release_id,f'http://127.0.0.1:{port}',source_digest(),host_reference=True)
        # Independent known fixture: popularity starts with 254, then 0..10; empty filter has no items.
        import hashlib,json
        base=dict(release_id=a.release_id,strategy='recent_then_popular',features_timestamp='2020-09-09',
            fallback=True,items=[dict(article_id=f'{n:06}',rank=i+1,reason='popular_7d') for i,n in enumerate([254,*range(11)])])
        want=hashlib.sha256(''.join(json.dumps(x,sort_keys=True,separators=(',',':')) for x in (base,{**base,'items':[]})).encode()).hexdigest()
        assert host_report['host_reference_sha256']==want
        with pytest.raises(ValueError,match='code differs'):
            probe(a._root,a.release_id,f'http://127.0.0.1:{port}','0'*64)
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close()


def test_history_serving_without_vector_or_training_services():
    import subprocess,sys
    subprocess.run([sys.executable,'-c',
        "import sys; from hm_recommender.api.app import create_app; "
        "assert not ({'torch','qdrant_client','mlflow'} & set(sys.modules)); "
        "assert create_app(admin_token='test') is not None"],check=True)
