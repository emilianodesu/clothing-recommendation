"""Read-only cgroup v2 observations for T5; lifetime peaks are never reset."""
from datetime import datetime, timezone
import json
import math
import re
import subprocess
import time

PHASES = ('before_seed', 'after_seed', 'after_replay', 'after_restart')
SERVICES = ('api', 'ui', 'postgres')
EVENTS = ('low', 'high', 'max', 'oom', 'oom_kill', 'oom_group_kill')
STATS = ('anon', 'file', 'kernel', 'shmem')
FILES = ('memory.current', 'memory.peak', 'memory.swap.current', 'memory.swap.peak',
         'memory.max', 'memory.swap.max', 'memory.events', 'memory.stat', 'memory.pressure')
KEYS = {'phase','service','elapsed_seconds','captured_at','sample_seconds','container_id',
        'started_at','restart_count','cgroup_inode','image_id','memory_bytes','memory_swap_bytes',
        'nano_cpus','oom_killed','current_bytes','peak_bytes','swap_current_bytes','swap_peak_bytes',
        'cgroup_limit_bytes','cgroup_swap_limit_bytes','events','stat','pressure_us'}


def require(value):
    if not value:
        raise ValueError('Invalid T5 memory observation')


def count(value, minimum=0):
    return type(value) is int and value >= minimum


def number(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def timestamp(value):
    require(isinstance(value,str) and len(value)<=40)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(parsed.tzinfo is not None)
    return parsed


def validate_snapshot(s):
    require(isinstance(s,dict) and set(s)==KEYS)
    require(s['phase'] in PHASES and s['service'] in SERVICES)
    require(number(s['elapsed_seconds']) and number(s['sample_seconds']))
    timestamp(s['captured_at']);timestamp(s['started_at'])
    require(isinstance(s['container_id'],str) and re.fullmatch('[a-f0-9]{64}',s['container_id']))
    require(isinstance(s['image_id'],str) and re.fullmatch('sha256:[a-f0-9]{64}',s['image_id']))
    for key in ('restart_count','nano_cpus','current_bytes','peak_bytes','swap_current_bytes','swap_peak_bytes'):
        require(count(s[key]))
    for key in ('cgroup_inode','memory_bytes','cgroup_limit_bytes'):
        require(count(s[key],1))
    require(count(s['memory_swap_bytes'],-1) and type(s['oom_killed']) is bool)
    require(s['cgroup_swap_limit_bytes']=='max' or count(s['cgroup_swap_limit_bytes']))
    for key,fields in (('events',EVENTS),('stat',STATS),('pressure_us',('some','full'))):
        require(isinstance(s[key],dict) and set(s[key])==set(fields))
        require(all(count(v) for v in s[key].values()))


def generation(s):
    return (s['container_id'],s['started_at'],s['restart_count'],s['cgroup_inode'])


def interval(a,b):
    validate_snapshot(a);validate_snapshot(b)
    require(a['service']==b['service'] and b['elapsed_seconds']>a['elapsed_seconds'])
    require(PHASES.index(a['phase'])<PHASES.index(b['phase']))
    same=generation(a)==generation(b)
    result=dict(service=a['service'],from_phase=a['phase'],to_phase=b['phase'],
        seconds=b['elapsed_seconds']-a['elapsed_seconds'],comparable=same,
        reason='same_generation' if same else 'generation_changed',
        boundary_reached_before=a['peak_bytes']>=a['cgroup_limit_bytes'],
        events_delta=None,pressure_delta_us=None,peak_growth_bytes=None,swap_peak_growth_bytes=None)
    if same:
        require(all(a[k]==b[k] for k in ('image_id','memory_bytes','memory_swap_bytes','nano_cpus','cgroup_limit_bytes','cgroup_swap_limit_bytes')))
        for key,out in (('events','events_delta'),('pressure_us','pressure_delta_us')):
            result[out]={k:b[key][k]-a[key][k] for k in a[key]}
            require(all(count(v) for v in result[out].values()))
        for key,out in (('peak_bytes','peak_growth_bytes'),('swap_peak_bytes','swap_peak_growth_bytes')):
            result[out]=b[key]-a[key];require(count(result[out]))
    return result


def _inspect(cid):
    # Never export Docker Config/Env, mounts or host paths.
    info=json.loads(subprocess.check_output(['docker','inspect',cid],text=True,timeout=30))[0]
    require(info['State']['Running'] is True)
    return dict(container_id=info['Id'],started_at=info['State']['StartedAt'],restart_count=info['RestartCount'],
        image_id=info['Image'],memory_bytes=info['HostConfig']['Memory'],
        memory_swap_bytes=info['HostConfig']['MemorySwap'],nano_cpus=info['HostConfig']['NanoCpus'],
        oom_killed=info['State']['OOMKilled'])


def capture(cid,service,phase,started):
    begin=time.monotonic();before=_inspect(cid)
    # All paths are fixed internal metrics; no user input enters shell code.
    command="set -eu\nstat -c '%i' /sys/fs/cgroup\nprintf '\\0'\n"
    for name in FILES:
        command+='cat /sys/fs/cgroup/'+name+"\nprintf '\\0'\n"
    parts=subprocess.check_output(['docker','exec',cid,'sh','-c',command],text=True,timeout=30)
    require(len(parts)<65536)
    parts=parts.split('\0');require(len(parts)==len(FILES)+2 and parts[-1]=='')
    raw=dict(zip(FILES,parts[1:-1]));after=_inspect(cid)
    require(before==after)  # A restart during sampling invalidates the observation.
    def table(name):
        rows=[line.split() for line in raw[name].splitlines()]
        require(all(len(row)==2 for row in rows))
        require(len({row[0] for row in rows})==len(rows))
        return {key:int(value) for key,value in rows}
    events=table('memory.events');stat=table('memory.stat');pressure={}
    for line in raw['memory.pressure'].splitlines():
        key,*fields=line.split();require(key in ('some','full') and key not in pressure)
        pairs=dict(x.split('=',1) for x in fields);pressure[key]=int(pairs['total'])
    swap_limit=raw['memory.swap.max'].strip()
    result=dict(before,phase=phase,service=service,elapsed_seconds=time.monotonic()-started,
        captured_at=datetime.now(timezone.utc).isoformat(),sample_seconds=time.monotonic()-begin,
        cgroup_inode=int(parts[0]),current_bytes=int(raw['memory.current']),peak_bytes=int(raw['memory.peak']),
        swap_current_bytes=int(raw['memory.swap.current']),swap_peak_bytes=int(raw['memory.swap.peak']),
        cgroup_limit_bytes=int(raw['memory.max']),cgroup_swap_limit_bytes='max' if swap_limit=='max' else int(swap_limit),
        events={k:events[k] for k in EVENTS},stat={k:stat[k] for k in STATS},pressure_us=pressure)
    validate_snapshot(result)
    return result
