"""Real Streamlit reruns with a synthetic HTTP transport; no database/network."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from streamlit.testing.v1 import AppTest


class MetadataTests(unittest.TestCase):
    def test_refresh_reads_current_metadata_without_changing_selected_stream(self):
        state={'revision':3,'refresh':[]}
        def handler(request):
            path=request.url.path
            streams=[dict(stream_id=n,release_id='release-'+str(state['revision']),mode='synthetic',
                data_as_of='2020-09-09',revision=state['revision'],
                published_at='publication-'+str(state['revision']),received_before='cursor-'+str(state['revision'])) for n in ('a','b')]
            if path=='/health/ready':value={'status':'ready'}
            elif path=='/streams':value={'items':streams}
            elif path=='/customers':value={'items':['synthetic-buyer']}
            elif path=='/catalog':value={'items':[],'total':0,'filters':{k:[] for k in ('product_type_name','product_group_name','colour_group_name')}}
            elif path.endswith('/recommend'):value={'fallback':True,'items':[]}
            elif path.endswith('/refresh'):
                state['refresh'].append(path);state['revision']+=1;value={}
            else:raise AssertionError(path)
            return httpx.Response(200,json=value)
        client_type=httpx.Client
        def client(**kwargs):return client_type(**kwargs,transport=httpx.MockTransport(handler))
        source=Path(os.environ.get('TEST_UI_SOURCE','/opt/hmrec/src/hm_recommender/ui/app.py'))
        with patch('httpx.Client',side_effect=client):
            app=AppTest.from_file(str(source),default_timeout=15).run()
            assert not app.exception
            # Choose the second stream and preserve its identity across metadata changes.
            app.selectbox(key='stream').select_index(1).run()
            app.button(key='refresh').click().run()
            assert not app.exception
            captions='\n'.join(c.value for c in app.caption)
            self.assertIn('Revisión 4',captions)
            self.assertIn('publication-4',captions)
            self.assertIn('cursor-4',captions)
            self.assertIn('Versión release-4',captions)
            self.assertEqual(state['refresh'],['/admin/streams/b/refresh'])
            app.button(key='refresh').click().run()
            self.assertEqual(state['refresh'],['/admin/streams/b/refresh']*2)
            self.assertIn('Revisión 5','\n'.join(c.value for c in app.caption))

if __name__=='__main__':unittest.main()
