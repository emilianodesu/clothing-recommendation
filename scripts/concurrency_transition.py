"""Exact-ID auxiliary stop/start; never remove containers, images or volumes."""
import json
import time
from mac24_terminal import command


def inspect(cid):
    template='{"container_id":{{json .Id}},"image_id":{{json .Image}},"running":{{.State.Running}},"started_at":{{json .State.StartedAt}},"restart_count":{{.RestartCount}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},"memory_bytes":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}}}'
    return json.loads(command(['docker','inspect',cid,'--format',template]))


def targets(capacity,t7):
    rows=capacity['result']['containers']
    other=[c for c in rows if c['scope']=='other']
    if len(other)!=2 or {c['service'] for c in other}!={'api','postgres'}:raise ValueError('Unexpected auxiliary project')
    pg=next(c for c in other if c['service']=='postgres')
    if pg['container_id']!=t7['result']['destination_postgres']['container_id']:raise ValueError('Restored PostgreSQL differs')
    if len({c['project_sha256'] for c in other})!=1:raise ValueError('Restored project differs')
    ml=[c for c in rows if c['scope']=='original' and c['service']=='mlflow']
    if len(ml)!=1:raise ValueError('MLflow differs')
    return [next(c for c in other if c['service']=='api'),pg,ml[0]]


def verify(row,current):
    if any(row[k]!=current[k] for k in ('container_id','image_id','memory_bytes','nano_cpus')):
        raise ValueError('Auxiliary identity or limits changed')


def stop(rows):
    for row in rows:
        current=inspect(row['container_id']);verify(row,current)
        if not current['running']:raise ValueError('Auxiliary was not running')
    for row in rows:
        command(['docker','stop','--time','30',row['container_id']],timeout=60)
        if inspect(row['container_id'])['running']:raise ValueError('Auxiliary did not stop')


def restore(rows):
    # Database before restored API; MLflow last. Only originally-running exact IDs.
    ordered=sorted(rows,key=lambda r:{'postgres':0,'api':1,'mlflow':2}[r['service']])
    output=[]
    for row in ordered:
        current=inspect(row['container_id']);verify(row,current)
        if not current['running']:command(['docker','start',row['container_id']],timeout=60)
        deadline=time.monotonic()+180
        while True:
            current=inspect(row['container_id']);verify(row,current)
            if current['running'] and current['health'] in ('healthy','none'):break
            if time.monotonic()>deadline:raise ValueError('Auxiliary not healthy after recovery')
            time.sleep(2)
        output.append(current)
    return output
