"""Persistent local quality-v1 budget. Call under the existing heavy-launch lock."""
import json
import math
from pathlib import Path
from handoff import write_json

LIMITS={'pilot':7200,'full':84600,'summary':1800}


def read(root):
    path=Path(root)/'quality-v1.json'
    value=json.loads(path.read_text()) if path.exists() else {'protocol':'quality-v1','attempts':{}}
    if not isinstance(value,dict) or set(value)!= {'protocol','attempts'} or value['protocol']!='quality-v1' or not isinstance(value['attempts'],dict):
        raise ValueError('Invalid quality budget ledger')
    for row in value['attempts'].values():
        if (not isinstance(row,dict) or set(row)!= {'size','allowance','seconds'} or row['size'] not in (*LIMITS,'demo')
            or type(row['allowance']) not in (int,float) or not 0<row['allowance']<=7200
            or (row['seconds'] is not None and (type(row['seconds']) not in (int,float) or not math.isfinite(row['seconds']) or row['seconds']<0))):
            raise ValueError('Corrupt budget attempt')
    return path,value


def reserve(root,run_id,size):
    if size not in (*LIMITS,'demo'): raise ValueError('Invalid budget category')
    path,value=read(root); attempts=value['attempts']
    if run_id in attempts or any(r['seconds'] is None for r in attempts.values()):
        raise ValueError('Inspect/export the previous attempt before starting another')
    used=sum(r['seconds'] for r in attempts.values() if r['size']==size)
    allowance=min(7200,LIMITS[size]-used) if size!='demo' else 7200
    if allowance<=0: raise ValueError('Quality campaign budget exhausted')
    attempts[run_id]={'size':size,'allowance':allowance,'seconds':None}
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    write_json(path,value)
    return allowance


def settle(root,run_id,seconds):
    path,value=read(root); row=value['attempts'][run_id]
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0: raise ValueError('Invalid runtime')
    if row['seconds'] is None:
        row['seconds']=seconds;write_json(path,value)
    return {size:sum(r['seconds'] or 0 for r in value['attempts'].values() if r['size']==size) for size in (*LIMITS,'demo')}
