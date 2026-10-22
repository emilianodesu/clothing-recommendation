"""Check the published loopback API from the actual host; never export request rows."""
import json
from pathlib import Path
import statistics
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hm_recommender.api.host_contract import host_requests, responses_digest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def probe_host(port, release_id, expected_code, expected_digest, bindings):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid local API port')
    if bindings.get('8000/tcp') != [{'HostIp':'127.0.0.1','HostPort':str(port)}]:
        raise ValueError('API must publish the configured port only on loopback')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    base='http://127.0.0.1:'+str(port)

    def request(path, body=None):
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=5) as response:
            raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('Oversized host response')
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('Invalid host response')
            return data

    live=request('/health/live')
    if live.get('status')!='alive' or live.get('source_tree_sha256')!=expected_code:
        raise ValueError('Wrong host API code')
    ready=request('/health/ready')
    if ready.get('status')!='ready' or ready.get('release_id')!=release_id:
        raise ValueError('Wrong host API version')
    bodies=[];elapsed=[]
    for query in host_requests():
        started=time.monotonic();body=request('/recommend',query);elapsed.append(time.monotonic()-started)
        if not isinstance(body.pop('request_id',None),str) or body.get('release_id')!=release_id:
            raise ValueError('Invalid host recommendation identity')
        bodies.append(body)
    actual=responses_digest(bodies)
    if actual!=expected_digest:raise ValueError('Host recommendations differ from package reference')
    return dict(release_id=release_id,source_tree_sha256=expected_code,host='127.0.0.1',port=port,
        binding_verified=True,health_live=True,health_ready=True,checked_requests=len(bodies),
        http_parity=True,response_sha256=actual,request_seconds_mean=statistics.mean(elapsed),
        request_seconds_max=max(elapsed),load_benchmark=False)
