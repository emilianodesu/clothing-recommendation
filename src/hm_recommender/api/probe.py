"""Deterministic sampled HTTP parity against a local package; no labels or private rows exported."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time
import httpx
from hm_recommender.api.recommend import load_state, FILTERS
from hm_recommender.api.schemas import RecommendationRequest
from hm_recommender.api.runtime import source_digest
from hm_recommender.api.host_contract import host_requests, responses_digest


def probe(directory, expected_id, url, expected_code, host_reference=False):
    state=load_state(directory)
    if state.release_id != expected_id: raise ValueError('Unexpected package')
    # Bound the sample; include both recent and older known buyers when available.
    recent=sorted(state.histories)[:128]
    old=sorted(state.customers-state.histories.keys())[:128]
    users=recent+old+['__hmrec_unknown_probe__']
    if users[-1] in state.customers: raise ValueError('Probe ID collision')
    filtered=[{}]
    for field in FILTERS:
        values=sorted(state.filter_index[field])
        if values: filtered.append({field:values[0]})
    filtered.append({'colour_group_name':'__hmrec_empty_filter__'})
    digest=hashlib.sha256();elapsed=[];checked=0
    with httpx.Client(base_url=url,trust_env=False,timeout=30) as client:
        live=client.get('/health/live');live.raise_for_status()
        if live.json()['source_tree_sha256']!=expected_code or source_digest()!=expected_code:
            raise ValueError('Serving/probe code differs from requested commit')
        ready=client.get('/health/ready');ready.raise_for_status()
        if ready.json()['release_id']!=expected_id:raise ValueError('Wrong active version')
        for user in users:
            for filters in (filtered if user in (users[0],users[-1]) else [{}]):
                request=RecommendationRequest(customer_id=user,k=12,filters=filters)
                expected=state.recommend(request).model_dump(mode='json');expected.pop('request_id')
                started=time.perf_counter()
                response=client.post('/recommend',json=request.model_dump())
                elapsed.append(time.perf_counter()-started)
                response.raise_for_status();body=response.json()
                if not isinstance(body.pop('request_id',None),str) or body!=expected:
                    raise ValueError('HTTP parity mismatch')
                digest.update(json.dumps(body,sort_keys=True,separators=(',',':')).encode())
                checked+=1
        metrics=client.get('/metrics');metrics.raise_for_status()
        if 'hmrec_recommendations_total' not in metrics.text:raise ValueError('Missing metrics')
    result={'release_id':expected_id,'source_tree_sha256':expected_code,
        'sampled_buyers':len(users),'checked_requests':checked,'filtered_cases':len(filtered)-1,
        'http_parity':True,'response_sha256':digest.hexdigest(),'catalog_size':len(state.catalog),
        'past_buyers':len(state.customers),'request_seconds_mean':statistics.mean(elapsed),
        'request_seconds_max':max(elapsed),'load_benchmark':False}
    if host_reference:
        bodies=[]
        for query in host_requests():
            body=state.recommend(RecommendationRequest(**query)).model_dump(mode='json')
            body.pop('request_id');bodies.append(body)
        result['host_reference_sha256']=responses_digest(bodies)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--release-id',required=True);parser.add_argument('--expected-code',required=True)
    parser.add_argument('--url',default='http://api:8000')
    parser.add_argument('--host-reference',action='store_true');args=parser.parse_args()
    try: print(json.dumps(probe(args.directory,args.release_id,args.url,args.expected_code,args.host_reference)))
    except Exception as error:
        print(json.dumps({'status':'failed','phase':'http-probe','error_type':type(error).__name__}))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
