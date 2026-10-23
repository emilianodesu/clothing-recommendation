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
        operation=dict(label=label,begin=start,end=None,timeout_seconds=min(limit,remaining),status='running',error_type=None)
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
                  and all(o['status']=='passed' for o in self.operations))
        return copy.deepcopy(dict(schema_version=1,policy=POLICY,host=self.host,marks=self.marks,
            operations=self.operations,guard=dict(started=self.started,released=self.released),
            finished=self.finished,capture_error=self.capture_error,assessment=derived,complete=complete))
