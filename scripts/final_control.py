"""Final-test reservation and exact-ID maintenance; no experiment retries."""
import json
import os
import time
from concurrency_transition import inspect, verify
from mac24_terminal import command


def reserve(path,value):
    with path.open('x') as f:
        os.chmod(path,0o600)
        json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())


def recovery_allowed(running,started,complete):
    return not running or not started or complete


def maintenance_targets(rows):
    expected={('original',s) for s in ('api','postgres','ui','mlflow','airflow-api','airflow-scheduler','airflow-dag-processor')}|{('other','api'),('other','postgres')}
    if len(rows)!=9 or {(r['scope'],r['service']) for r in rows}!=expected or len({r['container_id'] for r in rows})!=9:
        raise ValueError('Unexpected maintenance inventory')
    order={'airflow-scheduler':0,'airflow-dag-processor':1,'airflow-api':2,'api':3,'mlflow':4,'postgres':5}
    return sorted([r for r in rows if r['scope']=='other' or r['service'] not in ('api','postgres','ui')],key=lambda r:order[r['service']])


def restore_maintenance(rows):
    # Original database stays up; start dependent services before scheduler last.
    order={'postgres':0,'api':1,'mlflow':2,'airflow-api':3,'airflow-dag-processor':4,'airflow-scheduler':5}
    if len(rows)!=6 or len({r['container_id'] for r in rows})!=6:raise ValueError('Invalid recovery inventory')
    output=[]
    for row in sorted(rows,key=lambda r:order[r['service']]):
        current=inspect(row['container_id']);verify(row,current)
        if not current['running']:command(['docker','start',row['container_id']],timeout=60)
        deadline=time.monotonic()+180
        while True:
            current=inspect(row['container_id']);verify(row,current)
            if current['running'] and current['health'] in ('healthy','none'):break
            if time.monotonic()>deadline:raise ValueError('Recovery health timeout')
            time.sleep(2)
        output.append(dict(scope=row['scope'],service=row['service'],**current))
    return output


def unstarted_preparation(private):
    """A preparation failure is not permission to repeat any actual evaluation."""
    for name in ('config.json','go.json','ready.json','complete.json','evaluation-result.json','evaluation'):
        if (private/name).exists() or (private/name).is_symlink():
            raise ValueError('Real worker evidence exists; no preparation follow-up')


def verify_startup_recovery(saved,reservation,report):
    commit='fbcfca350fe865eec5c51220d22f0db2244029b6'
    run='final-mac24-20261024T062636Z-a62a3010'
    if any(v['source_commit']!=commit or v['run_id']!=run for v in (saved,reservation,report)):
        raise ValueError('Unrecognized startup recovery origin')
    if saved['image_id']!='sha256:'+reservation['image_sha256']:
        raise ValueError('Reserved image differs')
    r=report['result']
    if report['status']!='failed' or r['test_started'] is not False or r['evaluation'] is not None or r['auxiliaries_restored'] is not False:
        raise ValueError('Not the unstarted failed recovery')
