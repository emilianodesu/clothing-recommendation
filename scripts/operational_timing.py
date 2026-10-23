"""Prospective operation timing; preserve failed observations, never private inputs."""
import copy
import math
import re
from clock_guard import Timeline, AwakeGuard
from events_clock import clock_metadata, require

POLICY = dict(discrepancy_seconds=2, budget_seconds=7200, max_marks=2048)
LABELS = {'git','docker_context','docker_info','source','source_final','code_digest','compose','inspect','cgroup',
          'demo_package','seed','restart','pause','health_live','health_ready','http_catalog','http_customers',
          'http_recommend','http_profile','http_events','http_refresh','http_prepare','http_activate','ui_health'}
LABELS |= {'sample_'+phase+'_'+service for phase in ('before_seed','after_seed','after_replay','after_restart')
           for service in ('api','ui','postgres')}


def assessment(marks, capture_error=None):
    """Pure derivation shared with the reader; discontinuities are valid failure data."""
    if not marks:return dict(coherent=False,reason=capture_error or 'no_samples',utc_seconds=None,monotonic_seconds=None)
    wall=marks[-1]['utc_seconds']-marks[0]['utc_seconds']
    mono=marks[-1]['monotonic_seconds']-marks[0]['monotonic_seconds']
    reason=capture_error
    for a,b in zip(marks,marks[1:]):
        w=b['utc_seconds']-a['utc_seconds'];m=b['monotonic_seconds']-a['monotonic_seconds']
        if not reason and (w<0 or m<0):reason='clock_regression'
        if not reason and abs(w-m)>2:reason='clock_discontinuity'
    if not reason and abs(wall-mono)>2:reason='clock_discontinuity'
    if not reason and max(wall,mono)>7200:reason='budget_exhausted'
    if not reason and not all(m['awake'] for m in marks):reason='guard_lost'
    return dict(coherent=reason is None,reason=reason,utc_seconds=wall,monotonic_seconds=mono)


class Monitor:
    def __init__(self,guard=None,sample=None):
        self.guard=guard if guard is not None else AwakeGuard()
        self.timeline=Timeline(sample=sample)
        self.marks=[];self.operations=[];self.capture_error=None
        self.started=False;self.released=False;self.finished=False
        self.host=clock_metadata()

    def start(self):
        self.guard.__enter__();self.started=True
        self.mark('start')

    def mark(self,label):
        require(label in {'start','finish','poll'} | {'begin_'+s for s in LABELS} | {'end_'+s for s in LABELS})
        if len(self.marks)>=POLICY['max_marks']:
            self.capture_error='trace_limit';raise ValueError('Trace limit')
        awake=True
        try:self.guard.check()
        except Exception:awake=False
        before=len(self.timeline.samples)
        try:self.timeline.mark(label)
        except ValueError:
            if len(self.timeline.samples)==before:self.capture_error='invalid_clock'
            raise
        finally:
            if len(self.timeline.samples)>before:
                self.marks.append(dict(self.timeline.samples[-1],awake=awake))
        require(awake)
        return self.timeline.check()['remaining_seconds']

    def perform(self,label,limit,action):
        require(label in LABELS and type(limit) in (int,float) and math.isfinite(limit) and limit>0)
        remaining=self.mark('begin_'+label)
        start=len(self.marks)-1
        operation=dict(label=label,begin=start,end=None,timeout_seconds=min(limit,remaining),status='running',error_type=None,retry=False)
        self.operations.append(operation)
        def left():
            total=self.mark('poll');a=self.marks[start];b=self.marks[-1]
            elapsed=max(b['utc_seconds']-a['utc_seconds'],b['monotonic_seconds']-a['monotonic_seconds'])
            budget=min(total,operation['timeout_seconds']-elapsed)
            if budget<=0:raise TimeoutError('Operation deadline')
            return budget
        try:
            value=action(left);left();self.mark('end_'+label)
            operation.update(end=len(self.marks)-1,status='passed')
            return value
        except BaseException as error:
            operation.update(status='failed',error_type=type(error).__name__)
            try:self.mark('end_'+label)
            except Exception:pass
            if self.marks and self.marks[-1]['label']=='end_'+label:operation['end']=len(self.marks)-1
            raise

    def retry_ready(self):
        require(self.operations and assessment(self.marks,self.capture_error)['coherent'])
        op=self.operations[-1]
        require(op['label']=='health_ready' and op['status']=='failed')
        op['retry']=True

    def finish(self):
        try:
            if self.started:self.mark('finish');self.finished=True
        except Exception:pass
        finally:
            try:self.guard.__exit__(None,None,None);self.released=True
            except Exception:self.released=False

    def evidence(self):
        derived=assessment(self.marks,self.capture_error)
        complete=(self.started and self.released and self.finished and derived['coherent']
                  and all(o['status']=='passed' or o['retry'] for o in self.operations)
                  and all(any(p['label']=='health_ready' and p['status']=='passed' for p in self.operations[i+1:])
                          for i,o in enumerate(self.operations) if o['retry']))
        return copy.deepcopy(dict(schema_version=1,policy=POLICY,host=self.host,marks=self.marks,
            operations=self.operations,guard=dict(started=self.started,released=self.released),
            finished=self.finished,capture_error=self.capture_error,assessment=derived,complete=complete))


def validate_temporal(t):
    """Accept honest incomplete traces, but never recomputed success from missing work."""
    require(set(t)=={'schema_version','policy','host','marks','operations','guard','finished','capture_error','assessment','complete'})
    require(type(t['schema_version']) is int and t['schema_version']==1 and t['policy']==POLICY)
    require(t['capture_error'] in (None,'invalid_clock','trace_limit'))
    require(set(t['guard'])=={'started','released'} and all(type(v) is bool for v in t['guard'].values()))
    require(type(t['finished']) is bool and type(t['complete']) is bool)
    host=t['host'];require(set(host)=={'system','system_version','machine','python','clocks'})
    for k in ('system','system_version','machine','python'):
        require(isinstance(host[k],str) and re.fullmatch('[A-Za-z0-9_.+ -]{1,100}',host[k]))
    require(set(host['clocks'])=={'time','monotonic','perf_counter'})
    for c in host['clocks'].values():
        require(set(c)=={'implementation','monotonic','adjustable','resolution'})
        require(isinstance(c['implementation'],str) and re.fullmatch('[A-Za-z0-9_ .(),+-]{1,120}',c['implementation']))
        require(type(c['monotonic']) is bool and type(c['adjustable']) is bool)
        require(type(c['resolution']) in (int,float) and math.isfinite(c['resolution']) and 0<c['resolution']<=1)
    marks=t['marks'];ops=t['operations']
    require(isinstance(marks,list) and len(marks)<=POLICY['max_marks'] and isinstance(ops,list) and len(ops)<=len(marks))
    labels={'start','finish','poll'}|{'begin_'+s for s in LABELS}|{'end_'+s for s in LABELS}
    for m in marks:
        require(set(m)=={'label','utc_seconds','monotonic_seconds','awake'} and m['label'] in labels and type(m['awake']) is bool)
        require(all(type(m[k]) in (int,float) and math.isfinite(m[k]) and m[k]>=0 for k in ('utc_seconds','monotonic_seconds')))
    import json
    require(json.dumps(t['assessment'],sort_keys=True)==json.dumps(assessment(marks,t['capture_error']),sort_keys=True))
    begins=[];ends=[]
    for i,o in enumerate(ops):
        require(set(o)=={'label','begin','end','timeout_seconds','status','error_type','retry'} and o['label'] in LABELS)
        require(type(o['begin']) is int and 0<=o['begin']<len(marks) and o['begin'] not in begins)
        a=marks[o['begin']];require(a['label']=='begin_'+o['label']);begins.append(o['begin'])
        require(type(o['timeout_seconds']) in (int,float) and math.isfinite(o['timeout_seconds']) and 0<=o['timeout_seconds']<=7200)
        require(o['timeout_seconds']<=7200-max(a['utc_seconds']-marks[0]['utc_seconds'],a['monotonic_seconds']-marks[0]['monotonic_seconds'])+1e-6)
        require(o['status'] in ('passed','failed') and type(o['retry']) is bool)
        if o['retry']:
            require(o['label']=='health_ready' and o['status']=='failed')
        if o['end'] is not None:
            require(type(o['end']) is int and o['begin']<o['end']<len(marks) and o['end'] not in ends)
            b=marks[o['end']];require(b['label']=='end_'+o['label']);ends.append(o['end'])
        if o['status']=='passed':
            require(o['error_type'] is None and o['end'] is not None and not o['retry'])
            require(max(b['utc_seconds']-a['utc_seconds'],b['monotonic_seconds']-a['monotonic_seconds'])<=o['timeout_seconds']+1e-6)
        else:require(isinstance(o['error_type'],str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',o['error_type']))
    require(begins==sorted(begins))
    require(set(ends)=={i for i,m in enumerate(marks) if m['label'].startswith('end_')})
    unpaired={i for i,m in enumerate(marks) if m['label'].startswith('begin_')}-set(begins)
    # A failed pre-operation clock check appends its bad begin mark before raising.
    require(not unpaired or (len(unpaired)==1 and not t['assessment']['coherent'] and max(unpaired)>max(begins,default=-1)))
    expected=(t['guard']['started'] and t['guard']['released'] and t['finished'] and t['assessment']['coherent']
              and all(o['status']=='passed' or o['retry'] for o in ops)
              and all(any(p['label']=='health_ready' and p['status']=='passed' for p in ops[i+1:]) for i,o in enumerate(ops) if o['retry']))
    require(t['complete']==expected)
    if t['finished']:require(bool(marks) and marks[-1]['label']=='finish')
    if marks:require(t['guard']['started'] and marks[0]['label']=='start')
