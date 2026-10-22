"""Synthetic dataset provenance and demo HTTP, without Kaggle or training."""
from datetime import date
import os
from pathlib import Path
from uuid import uuid4
import pytest
from test_recommendations import serving

pytestmark=pytest.mark.skipif(os.environ.get('HMREC_SERVING_TESTS')!='1',reason='requires PostgreSQL')


def test_generated_demo_seeds_only_past_rows_and_accepts_synthetic_origin(serving):
    from hm_recommender.model.demo import prepare_demo_dataset
    from hm_recommender.releases.package import build_history_release
    from hm_recommender.events.aggregate import initialize_stream
    _,registry,_,_,connection,root=serving
    with connection() as conn:
        conn.execute((Path('/workspace/migrations') if Path('/workspace/migrations').exists() else Path('/opt/hmrec/migrations')).joinpath('003_events.sql').read_text())
    ds=prepare_demo_dataset(root/'generated')
    release=build_history_release(ds,date(2020,9,9),root/'generated-release')
    registry.prepare_release(release._root);registry.activate_release(release.release_id)
    stream=str(uuid4())
    result=initialize_stream(ds._root,release._root,stream,mode='synthetic',connection=connection)
    assert result['mode']=='synthetic'
    with connection() as conn:
        assert conn.execute('SELECT max(day),sum(frequency) FROM stream_daily_seed WHERE stream_id=%s',(stream,)).fetchone()==(date(2020,9,2),256)
    response=serving[0].post(f'/streams/{stream}/recommend',json={'customer_id':'demo-000'})
    assert response.status_code==200 and response.json()['items'][0]['article_id']=='0000000000'
    assert response.json()['features_timestamp']=='2020-09-09'
    from hm_recommender.events.replay import replay
    def request(path,body=None,expected=200):
        response=serving[0].request('GET' if body is None else 'POST',path,json=body,
            headers={'Authorization':'Bearer local-test-token'})
        assert response.status_code==expected
        return response.json()
    result=replay(request,stream,release.release_id,'synthetic')
    assert result['purchase_changes_profile'] and result['base_unchanged']
    assert result['duplicate_status']==200 and result['conflict_status']==409
