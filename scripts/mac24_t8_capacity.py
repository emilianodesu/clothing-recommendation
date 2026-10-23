#!/usr/bin/env python3
"""T8-B admission snapshot. No service mutation, workload, training or test."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from bootstrap import read_configuration
from clock_check import checkout_commit,runner_digest
from handoff import write_json,now,package_report,read_report
from mac24_terminal import command
from capacity_handoff import SERVICES,POLICY,decision,validate,require

ROOT=Path(__file__).resolve().parents[1]


def run(commit):
    os.umask(0o077)
    os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT='colima-hm-project')
    checkout_commit(ROOT,commit);started=now()
    v=read_configuration(ROOT/'.env');art=Path(v['ARTIFACTS_DIR'])
    if not art.is_absolute():art=ROOT/art
    audit=json.loads((ROOT/'evidence/benchmark-review-mac24.json').read_text());require(audit['t8_a_closed'])
    archive=art/'handoff'/(audit['run_id']+'.zip')
    require(hashlib.sha256(archive.read_bytes()).hexdigest()==audit['archive']['sha256'])
    require(read_report(archive)['status']=='passed')
    engine=json.loads(command(['docker','info','--format','{"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}}}']))
    require(engine['cpus']==4 and 13*2**30<=engine['memory_bytes']<=14*2**30)
    ids=command(['docker','ps','--no-trunc','--format','{{.ID}}']).splitlines();require(bool(ids))
    template='{"container_id":{{json .Id}},"image_id":{{json .Image}},"memory_bytes":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
    containers=[];observed={}
    for cid in sorted(ids):
        row=json.loads(command(['docker','inspect',cid,'--format',template]))
        observed[cid]=dict(row)
        project=row.pop('project') or ''
        row['scope']='original' if project==v['COMPOSE_PROJECT_NAME'] else 'other'
        row['project_sha256']=hashlib.sha256(project.encode()).hexdigest()
        if row['service'] not in SERVICES:row['service']='other'
        containers.append(row)
    require(set(ids)==set(command(['docker','ps','--no-trunc','--format','{{.ID}}']).splitlines()))
    for cid in sorted(ids):
        require(json.loads(command(['docker','inspect',cid,'--format',template]))==observed[cid])
    require({'api','ui','postgres'}<={c['service'] for c in containers if c['scope']=='original'})
    rid='capacity-mac24-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    r=dict(protocol='capacity-v1',status='passed',engine=engine,containers=containers,policy=POLICY,
        decision=decision(engine,containers),baseline_run_id=audit['run_id'],load_started=False,test_evaluated=False)
    env=dict(source_commit=commit,runner_tree_sha256=runner_digest(ROOT),baseline_archive_sha256=audit['archive']['sha256'])
    report=dict(schema_version=1,kind='capacity-handoff',run_id=rid,machine_id='mac24',context=os.environ['DOCKER_CONTEXT'],source_commit=commit,
        source_clean=True,started_at=started,finished_at=now(),status='passed',warnings=[],steps=[dict(name='capacity',log='job.log',status='passed',exit_code=0)],result=r)
    validate(report,r,env)
    folder=art/'handoff'/rid;folder.mkdir()
    write_json(folder/'result.json',r);write_json(folder/'environment.json',env)
    (folder/'commit.txt').write_text(commit+'\n');(folder/'job.log').write_text('capacity T8-B\nstatus: passed\n')
    archive=package_report(folder,report);read_report(archive)
    print('Decisión de admisión: '+r['decision']['admission'],flush=True)
    print('Reporte cerrado: '+str(archive),flush=True)
    logs=ROOT/'.runtime'/rid;logs.mkdir(parents=True)
    command([sys.executable,'scripts/github-handoff.py','upload',str(archive)],log=logs/'upload.log',timeout=300)
    print('Publicado: https://github.com/emilianodesu/clothing-recommendation/releases/tag/handoff-'+rid,flush=True)
    print('Inventario passed; no se inició carga ni se acredita concurrencia. Revisión principal pendiente.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    try:run(p.parse_args().expected_commit)
    except Exception as error:
        print('DETENIDO: '+type(error).__name__+'. Conservar reportes; no iniciar carga.',file=sys.stderr);sys.exit(1)
