"""Bounded open-loop loopback load. Never exports requests or responses."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
import math
import threading
import time


def quantiles(values):
    values=sorted(values)
    return {name:values[max(0,math.ceil(q*len(values))-1)] if values else None
            for name,q in [('p50',.5),('p95',.95),('p99',.99)]}


def summarize(samples,scheduled,seconds):
    completed=len(samples);success=sum(s['ok'] for s in samples)
    if completed>scheduled or seconds<=0:raise ValueError('Invalid accounting')
    return dict(scheduled=scheduled,completed=completed,successful=success,
        omitted=scheduled-completed,errors=completed-success,error_fraction=(scheduled-success)/scheduled if scheduled else 1.,
        success_rps=success/seconds,completed_rps=completed/seconds,
        latency_seconds=quantiles([s['latency'] for s in samples]),
        service_seconds=quantiles([s['service'] for s in samples]))


def phase(cases,*,rate,seconds,workers,request,max_lag):
    if not cases or rate<=0 or seconds<=0 or workers<1:raise ValueError('Invalid load')
    scheduled=round(rate*seconds);samples=[];lock=threading.Lock();slots=threading.BoundedSemaphore(workers)
    by_group={c['group']:0 for c in cases};started=time.monotonic();wall=time.time()
    def work(case,due):
        begin=time.monotonic();ok=False
        try:ok=request(case) is True
        except Exception:pass
        finally:
            end=time.monotonic()
            with lock:samples.append(dict(group=case['group'],ok=ok,latency=end-due,service=end-begin))
            slots.release()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index in range(scheduled):
            case=cases[index%len(cases)];by_group[case['group']]+=1
            due=started+index/rate;remaining=due-time.monotonic()
            if remaining>0:time.sleep(remaining)
            if time.monotonic()-due>max_lag or not slots.acquire(blocking=False):continue
            pool.submit(work,case,due)
        remaining=started+seconds-time.monotonic()
        if remaining>0:time.sleep(remaining)
    elapsed=time.monotonic()-started
    result=summarize(samples,scheduled,elapsed)
    result.update(offered_rps=rate,window_seconds=seconds,elapsed_seconds=elapsed,
        clock_discrepancy_seconds=abs((time.time()-wall)-elapsed),
        groups={g:summarize([s for s in samples if s['group']==g],n,elapsed) for g,n in by_group.items()})
    return result


def http_request(port,case,timeout=2.):
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('Invalid local port')
    connection=HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        connection.request('POST','/recommend',body=json.dumps(case['request']),headers={'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read(1024*1024+1)
        if response.status!=200 or len(raw)>1024*1024:return False
        body=json.loads(raw)
        if not isinstance(body,dict) or not isinstance(body.pop('request_id',None),str):return False
        return body==case['response']
    finally:connection.close()
