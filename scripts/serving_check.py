"""T4-B foreground acceptance rehearsal. Closed aggregates only; never test labels."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from bootstrap import read_configuration
from handoff import now, package_report, read_report, validate_context, write_json
from project import compose
from serving_host import probe_host

ROOT=Path(__file__).resolve().parents[1]


def fingerprint(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def source(artifacts,run_id):
    if not re.fullmatch(r'release-[A-Za-z0-9_-]+',run_id):raise ValueError('Invalid T4-A run ID')
    archive=next((artifacts/folder/(run_id+'.zip') for folder in ('handoff','incoming')
        if (artifacts/folder/(run_id+'.zip')).is_file()),None)
    if archive is None:raise ValueError('Missing closed T4-A archive')
    report=read_report(archive)
    if report['kind']!='release-handoff' or report['status']!='passed' or report['run_id']!=run_id:
        raise ValueError('Source must be the requested passed T4-A')
    package=report['result']['package'];directory=artifacts/'data-jobs'/run_id/'work/release'
    if directory.is_symlink() or not directory.resolve().is_relative_to(artifacts.resolve()):raise ValueError('Unsafe source path')
    if fingerprint(directory/'manifest.json')!=package['manifest_sha256']:raise ValueError('Private package does not match T4-A')
    if {p.name for p in directory.iterdir()}!={'manifest.json',*(f['name'] for f in package['files'])}:
        raise ValueError('Private package inventory differs')
    for entry in package['files']:
        path=directory/entry['name']
        if path.is_symlink() or path.stat().st_size!=entry['size_bytes'] or fingerprint(path)!=entry['sha256']:
            raise ValueError('Private package content differs')
    return dict(run_id=run_id,archive_sha256=fingerprint(archive),release_id=package['release_id'],
        manifest_sha256=package['manifest_sha256'],dataset=report['result']['dataset'])


def run(root,machine_id,expected_commit,reference_run,candidate_run):
    values=read_configuration(root/'.env')
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    rid='serving-'+machine_id+'-'+stamp+'-'+uuid.uuid4().hex[:8]
    folder=artifacts/'handoff'/rid;folder.mkdir(parents=True,mode=0o700)
    report=dict(schema_version=1,kind='serving-handoff',run_id=rid,machine_id=machine_id,
        context=os.environ.get('DOCKER_CONTEXT','default'),source_commit=None,source_clean=False,
        started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    env=dict(run_id=rid,source_commit=None,source_tree_sha256=None)
    result={'stage':'T4-B','status':'failed','phase':'preflight'};phase='preflight';started=time.monotonic()
    print('Ejecución: '+rid,flush=True)

    def call(*args):
        remaining=7200-(time.monotonic()-started)
        if remaining<=0:raise TimeoutError('T4-B time budget exceeded')
        return compose(root,values,*args,capture=True,timeout=min(600,remaining)).strip()

    def log(step):
        with (folder/'job.log').open('a') as stream:stream.write(step+'\n')
        print('T4-B: '+step,flush=True)

    def admin(action,**params):
        args=[action]
        for key,value in params.items():args.extend(['--'+key.replace('_','-'),value])
        return json.loads(call('exec','-T','api','hmrec','serving',*args))

    def health():
        return json.loads(call('exec','-T','api','python','-c',
            "import json,urllib.request,urllib.error; "
            "r=urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=10); print(r.read().decode())"))

    def probe(key,entry):
        nonlocal phase
        phase=key+'-internal'
        log(key)
        data=json.loads(call('run','--rm','--no-deps','-T','serving-probe','python','-m','hm_recommender.api.probe',
            '--directory','/artifacts/data-jobs/'+entry['run_id']+'/work/release',
            '--release-id',entry['release_id'],'--expected-code',env['source_tree_sha256'],'--host-reference'))
        result['checks'][key]=data
        phase=key+'-host';log(phase)
        cid=call('ps','-q','api')
        bindings=json.loads(subprocess.check_output(['docker','inspect',cid,'--format',
            '{{json .NetworkSettings.Ports}}'],text=True,timeout=30))
        result['host_checks'][key]=probe_host(int(values['API_PORT']),entry['release_id'],
            env['source_tree_sha256'],data['host_reference_sha256'],bindings)

    def resource():
        cid=call('ps','-q','api')
        template='{"image_id":{{json .Image}},"memory_bytes":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}},"oom_killed":{{.State.OOMKilled}},"running":{{.State.Running}}}'
        r=json.loads(subprocess.check_output(['docker','inspect','--format',template,cid],text=True,timeout=30))
        r.update(json.loads(call('exec','-T','api','python','-c',
            "import json,os; from pathlib import Path; p=Path('/sys/fs/cgroup'); "
            "print(json.dumps({'cgroup_peak_bytes':int((p/'memory.peak').read_text()),"
            "'swap_peak_bytes':int((p/'memory.swap.peak').read_text()),'threads':int(os.environ['POLARS_MAX_THREADS'])}))")))
        return r

    try:
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        clean=not subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
        report.update(source_commit=commit,source_clean=clean);env['source_commit']=commit
        (folder/'commit.txt').write_text(commit+'\n')
        if commit!=expected_commit or not clean:raise ValueError('Expected a clean checkout at the received commit')
        endpoint=subprocess.check_output(['docker','context','inspect','--format','{{.Endpoints.docker.Host}}'],text=True,timeout=30).strip()
        architecture=subprocess.check_output(['docker','info','--format','{{.Architecture}}'],text=True,timeout=30).strip()
        validate_context(endpoint,architecture)
        sys.path.insert(0,str(root/'src'))
        from hm_recommender.api.runtime import source_digest
        env['source_tree_sha256']=source_digest(root/'src/hm_recommender')
        env['uv_lock_sha256']=fingerprint(root/'uv.lock')
        sources={'reference':source(artifacts,reference_run),'candidate':source(artifacts,candidate_run)}
        if sources['reference']['release_id']==sources['candidate']['release_id']:raise ValueError('Need two different versions')
        if health()['source_tree_sha256']!=env['source_tree_sha256']:raise ValueError('Rebuild and restart API first')
        # Refuse to replace an unrelated active version; caller may explicitly use it as reference.
        active=json.loads(call('exec','-T','api','python','-c',
            "import json,urllib.request,urllib.error\ntry:\n r=urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=10);print(r.read().decode())\nexcept urllib.error.HTTPError as e:\n assert e.code==503;print('{}')"))
        if active.get('release_id') not in (None,sources['reference']['release_id'],sources['candidate']['release_id']):
            raise ValueError('An unrelated version is active')
        result=dict(stage='T4-B',protocol='history-serving-v2',status='running',source_tree_sha256=env['source_tree_sha256'],
            sources=sources,checks={},host_checks={},resources={},final_release_id=None,activated=False,
            test_evaluated=False,quality_accepted=False,training_performed=False,duration_seconds=0)
        for phase in ('reference','candidate'):
            entry=sources[phase]
            prepared=admin('prepare',directory='data-jobs/'+entry['run_id']+'/work/release')
            if prepared['release_id']!=entry['release_id']:raise ValueError('Prepared identity differs')
            admin('activate',release_id=entry['release_id']);probe(phase,entry)
        result['resources']['before_restart']=resource()
        phase='restart';log('restart-api')
        call('restart','api')
        # The container can be live before model recovery; wait for readiness with bounded retry.
        call('exec','-T','api','python','-c',
            "import time,urllib.request\nfor n in range(120):\n try:\n  urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2);break\n except Exception:\n  time.sleep(1)\nelse:raise RuntimeError('API readiness timeout')")
        probe('restart',sources['candidate'])
        phase='rollback';admin('rollback');probe('rollback',sources['reference'])
        phase='final';admin('activate',release_id=sources['candidate']['release_id']);probe('final',sources['candidate'])
        result['resources']['after_restart']=resource()
        # Ensure the source package and closed report did not change during the check.
        if sources!={k:source(artifacts,v['run_id']) for k,v in sources.items()}:raise ValueError('Source changed during verification')
        result.update(status='passed',final_release_id=sources['candidate']['release_id'],activated=True,duration_seconds=time.monotonic()-started)
        report.update(status='passed',result=result)
        from serving_handoff import validate_serving
        validate_serving(report,result,env,{'manifest.json','result.json','environment.json','commit.txt','job.log'})
        code=0
    except (Exception,KeyboardInterrupt) as error:
        code=130 if isinstance(error,KeyboardInterrupt) else 1
        report['status']='interrupted' if code==130 else 'failed'
        # Keep partial aggregate observations, never raw command output or request bodies.
        result.update(status=report['status'],phase=phase,error_type=type(error).__name__)
        result.pop('activated',None)
        result['activation_state']='inspect_service_before_retry'
        log(phase+': '+type(error).__name__)
    report.update(finished_at=now(),result=result,steps=[dict(name='serving',log='job.log',status=report['status'],exit_code=code)])
    write_json(folder/'environment.json',env);write_json(folder/'result.json',result)
    archive=package_report(folder,report)
    print('Reporte cerrado: '+str(archive),flush=True)
    print('Estado: '+report['status']+'; conservar el estado del servicio e inspeccionar antes de reintentar.',flush=True)
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine-id',required=True);parser.add_argument('--expected-commit',required=True)
    parser.add_argument('--reference-run-id',required=True);parser.add_argument('--candidate-run-id',required=True)
    args=parser.parse_args(argv)
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',args.machine_id) or not re.fullmatch(r'[a-f0-9]{40}',args.expected_commit):
        parser.error('Machine alias and full expected SHA required')
    return run(ROOT,args.machine_id,args.expected_commit,args.reference_run_id,args.candidate_run_id)


if __name__=='__main__':raise SystemExit(main())
