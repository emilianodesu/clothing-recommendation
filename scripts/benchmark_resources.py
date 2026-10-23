"""Read-only cgroup v2 samples, preserving lifetime counters and peaks."""
import json
import subprocess
import time
from events_memory import capture


def sample(containers,started):
    rows=[]
    for service,cid in containers.items():
        memory=capture(cid,service,'before_seed',started,services=tuple(containers))
        raw=subprocess.check_output(['docker','exec',cid,'cat','/sys/fs/cgroup/cpu.stat'],text=True,timeout=30)
        cpu=dict(line.split() for line in raw.splitlines())
        rows.append(dict(memory=memory,cpu_usage_usec=int(cpu['usage_usec'])))
    return rows


def accepted(rows):
    if {r['memory']['service'] for r in rows}!={'api','postgres','ui'}:return False
    for service in ('api','postgres','ui'):
        group=[r for r in rows if r['memory']['service']==service]
        if len(group)<60:return False
        stamps=[r['memory']['elapsed_seconds'] for r in group]
        if stamps[-1]-stamps[0]<720 or any(not 0<b-a<=35 for a,b in zip(stamps,stamps[1:])):return False
        first=group[0]['memory'];last=group[-1]['memory']
        expected={'api':4*2**30,'postgres':512*2**20,'ui':512*2**20}[service]
        expected_cpu={'api':2_000_000_000,'postgres':0,'ui':500_000_000}[service]
        if first['memory_bytes']!=expected or first['nano_cpus']!=expected_cpu:return False
        for a,b in zip(group,group[1:]):
            if b['cpu_usage_usec']<a['cpu_usage_usec']:return False
            if any(b['memory']['events'][k]<a['memory']['events'][k] for k in a['memory']['events']):return False
        for row in group:
            s=row['memory']
            if any(s[k]!=first[k] for k in ('container_id','started_at','restart_count','cgroup_inode','image_id')):return False
            if s['swap_current_bytes'] or s['oom_killed'] or s['memory_bytes']!=expected or s['cgroup_limit_bytes']!=expected or s['nano_cpus']!=expected_cpu:return False
            if s['swap_peak_bytes']!=first['swap_peak_bytes']:return False
        if any(last['events'][k]!=first['events'][k] for k in ('oom','oom_kill','oom_group_kill')):return False
    return bool(rows)
