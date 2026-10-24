"""Measurements for one real H cycle; does not reinterpret historical T8-B."""
import math
from concurrency_handoff import overlap
from ui_recovery_control import require


def collect_periodically(done,collect,monotonic):
    due=monotonic()+10
    while not done.wait(max(0,due-monotonic())):
        collect()
        due+=10


def clock_bounds(before,after):
    def best(rows):
        require(bool(rows),'Missing clock calibration')
        for r in rows:
            require(all(type(r[k]) in (int,float) and math.isfinite(r[k]) for k in ('before','vm','after')),
                    'Invalid clock calibration')
            require(r['after']>=r['before'],'Reversed calibration')
        r=min(rows,key=lambda r:r['after']-r['before'])
        return r['before']-r['vm'],r['after']-r['vm']
    a,b=best(before),best(after)
    require(abs(sum(a)/2-sum(b)/2)<=.1,'VM/host clock offset drift >100 ms')
    low=min(a[0],b[0])-.1;high=max(a[1],b[1])+.1
    require(high-low<=1.,'Clock uncertainty >1 s')
    return dict(low=low,high=high,uncertainty_seconds=high-low)


def cycle_metrics(trace,cases,marks,bounds):
    seconds=marks['end']-marks['start']
    require(0<seconds<540 and abs(marks['wall_end']-marks['wall_start']-seconds)<=.1,
            'Invalid build duration or clock discontinuity')
    possible=(marks['start']+bounds['low'],marks['end']+bounds['high'])
    definite=(marks['start']+bounds['high'],marks['end']+bounds['low'])
    require(trace['started']<=possible[0]<possible[1]<=trace['started']+trace['scheduled']/trace['rate'],
            'Build not fully covered by measurement')
    return dict(internal_seconds=seconds,definite_start=definite[0],definite_end=definite[1],
        possible_start=possible[0],possible_end=possible[1],
        definite=overlap(trace,cases,*definite) if definite[1]>definite[0] else None,
        possible=overlap(trace,cases,*possible))


def meets(metrics):
    return (metrics is not None and metrics['latency_seconds']['p95'] is not None
            and metrics['latency_seconds']['p95']<.2 and metrics['error_fraction']<.01)


def decision(report):
    if report['error_type'] is not None or not all(report[k] for k in
            ('resources_ok','source_preserved','auxiliaries_restored','worker_ok')):return 'failed'
    if not meets(report['measurement']):return 'failed'
    definite=(report.get('cycle') or {}).get('definite')
    # A measured violation remains a failure even when the cohort is small.
    if definite and definite['scheduled'] and not meets(definite):return 'failed'
    if not definite or definite['scheduled']<20:return 'inconclusive'
    return 'passed'


def public_trace(trace):
    origin=trace['started']
    return dict(scheduled=trace['scheduled'],rate=trace['rate'],samples=[dict(
        group=s['group'],ok=s['ok'],due=s['due']-origin,finished=s['finished']-origin,
        latency=s['latency'],service=s['service']) for s in trace['samples']])
