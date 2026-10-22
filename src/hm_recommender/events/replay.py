"""Bounded operational rehearsal. Exports aggregates, never request rows."""
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import urllib.error
import urllib.request
from uuid import uuid4


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def http_request(base, token):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(path,body=None,expected=200):
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        try:response=opener.open(req,timeout=600)
        except urllib.error.HTTPError as error:response=error
        with response:
            if response.status!=expected:raise ValueError('Unexpected HTTP status: '+str(response.status))
            raw=response.read(2*1024**2+1)
            if len(raw)>2*1024**2:raise ValueError('Oversized response')
            return json.loads(raw)
    return request


def normalized(body):
    return {k:v for k,v in body.items() if k not in ('request_id','stream_id','revision','received_before','published_at','mode')}


def digest(body):
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def profile_digest(request,stream):
    body=request(f'/streams/{stream}/recommend',{'customer_id':'t5-'+stream})
    body.pop('request_id')
    return digest(body)


def replay(request,stream,release_id,mode):
    """One fresh stream, four new events, three publications; no source future rows."""
    def check(ok):
        if not ok:raise ValueError('Event rehearsal invariant failed')
    users=request('/customers')['items'][:3]+['t5-'+stream]
    query={'customer_id':users[-1]}
    original=normalized(request('/recommend',query));cut=date.fromisoformat(original['features_timestamp'])
    check(original['release_id']==release_id)
    for user in users:
        q={'customer_id':user}
        check(normalized(request('/recommend',q))==normalized(request(f'/streams/{stream}/recommend',q)))
    items=request('/catalog?limit=2')['items'];check(len(items)==2)
    a,b=[row['article_id'] for row in items]
    def event(article,kind,days):
        return dict(event_id=str(uuid4()),stream_id=stream,release_id=release_id,customer_id=users[-1],
            article_id=article,kind=kind,origin=mode,event_time=datetime.combine(cut-timedelta(days=days),time(12),tzinfo=timezone.utc).isoformat())
    payload=event(a,'purchase',1)
    request('/events',payload,201);request('/events',payload,200)
    request('/events',dict(payload,article_id=b),409)
    request('/events',event(b,'view',1),201);request('/events',event(b,'click',1),201)
    before=request(f'/streams/{stream}/recommend',query);check(before['fallback'])
    def refresh():return request(f'/admin/streams/{stream}/refresh',{'data_as_of':str(cut)})
    first=refresh();body=request(f'/streams/{stream}/recommend',query)
    check(not body['fallback'] and body['items'][0]['article_id']==a and body['items'][0]['reason']=='recent_purchase')
    check(sum(i['reason']=='recent_purchase' for i in body['items'])==1)
    check(first['processed_events']==3 and first['revision']==2)
    request('/events',event(b,'purchase',2),201)
    second=refresh();body=request(f'/streams/{stream}/recommend',query)
    check([i['article_id'] for i in body['items'][:2]]==[a,b])
    check(second['processed_events']==4 and second['revision']==3)
    check(refresh()==second)  # Same publication survives an idempotent refresh.
    check(normalized(request('/recommend',query))==original)
    return dict(initial_parity_requests=len(users),new_events=4,duplicate_status=200,conflict_status=409,
        purchase_changes_profile=True,views_clicks_not_purchases=True,late_event_order=True,
        refresh_idempotent=True,base_unchanged=True,profile_sha256=profile_digest(request,stream),
        revision=second['revision'],data_as_of=second['data_as_of'],processed_events=second['processed_events'])
