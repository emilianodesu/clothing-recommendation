"""Bounded aggregate metrics; no URLs, customers, streams or secrets as labels."""
import threading


class Metrics:
    bounds=(.005,.01,.025,.05,.1,.2,.5,1.,5.,float('inf'))
    def __init__(self):
        self.lock=threading.Lock();self.counts={};self.buckets=[0]*len(self.bounds);self.total=0.;self.fallback=0
    def observe(self,status,seconds):
        with self.lock:
            key=str(status//100)+'xx';self.counts[key]=self.counts.get(key,0)+1;self.total+=seconds
            for i,bound in enumerate(self.bounds):self.buckets[i]+=seconds<=bound
    def used_fallback(self):
        with self.lock:self.fallback+=1
    def render(self):
        with self.lock:
            lines=['# TYPE hmrec_http_requests_total counter']
            lines += [f'hmrec_http_requests_total{{status="{key}"}} {value}' for key,value in sorted(self.counts.items())]
            lines += ['# TYPE hmrec_http_seconds histogram']
            lines += ['hmrec_http_seconds_bucket{le="'+('+Inf' if bound==float('inf') else str(bound))+'"} '+str(value) for bound,value in zip(self.bounds,self.buckets)]
            lines += [f'hmrec_http_seconds_sum {self.total}',f'hmrec_http_seconds_count {sum(self.counts.values())}', '# TYPE hmrec_fallback_total counter',f'hmrec_fallback_total {self.fallback}']
            return '\n'.join(lines)+'\n'
