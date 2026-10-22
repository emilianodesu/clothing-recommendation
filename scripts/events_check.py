"""T5 one-shot rehearsal with closed aggregate evidence, using localhost HTTP."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from bootstrap import read_configuration
from handoff import now,package_report,validate_context,write_json
from project import compose
from serving_check import fingerprint,source

ROOT=Path(__file__).resolve().parents[1]


def run(root,machine_id,expected_commit,source_run=None):
    values=read_configuration(root/'.env')
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    rid='events-'+machine_id+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    folder=artifacts/'handoff'/rid;folder.mkdir(parents=True,mode=0o700)
    stream=str(uuid.uuid4());container=values['COMPOSE_PROJECT_NAME']+'-'+rid
    report=dict(schema_version=1,kind='events-handoff',run_id=rid,machine_id=machine_id,
        context=os.environ.get('DOCKER_CONTEXT','default'),source_commit=None,source_clean=False,
        started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    env=dict(run_id=rid,source_commit=None,source_tree_sha256=None)
    result=dict(stage='T5',status='failed',phase='preflight');phase='preflight';started=time.monotonic()
    print('Ejecución: '+rid,flush=True)
    def call(*args):
        remaining=7200-(time.monotonic()-started)
        if remaining<=0:raise TimeoutError('T5 time budget exceeded')
        return compose(root,values,*args,capture=True,timeout=min(1800,remaining)).strip()
    def log(step):
        with (folder/'job.log').open('a') as out:out.write(step+'\n')
        print('T5: '+step,flush=True)
    def inspect(cid):
        return json.loads(subprocess.check_output(['docker','inspect',cid],text=True,timeout=30))[0]
    def resources(cid,process=None):
        info=inspect(cid)
        if process is None:
            process=json.loads(subprocess.check_output(['docker','exec',cid,'sh','-c',
                'printf "{\\"cgroup_peak_bytes\\":%s,\\"swap_peak_bytes\\":%s}" "$(cat /sys/fs/cgroup/memory.peak)" "$(cat /sys/fs/cgroup/memory.swap.peak)"'],text=True,timeout=30))
        return dict(image_id=info['Image'],memory_bytes=info['HostConfig']['Memory'],nano_cpus=info['HostConfig']['NanoCpus'],
            cgroup_peak_bytes=process['cgroup_peak_bytes'],swap_peak_bytes=process['swap_peak_bytes'],oom_killed=info['State']['OOMKilled'])
    try:
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        clean=not subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
        report.update(source_commit=commit,source_clean=clean);env['source_commit']=commit
        (folder/'commit.txt').write_text(commit+'\n')
        if commit!=expected_commit or not clean:raise ValueError('Expected clean checkout at received SHA')
        endpoint=subprocess.check_output(['docker','context','inspect','--format','{{.Endpoints.docker.Host}}'],text=True,timeout=30).strip()
        architecture=subprocess.check_output(['docker','info','--format','{{.Architecture}}'],text=True,timeout=30).strip()
        validate_context(endpoint,architecture)
        sys.path.insert(0,str(root/'src'))
        from hm_recommender.api.runtime import source_digest
        from hm_recommender.events.replay import http_request,replay,profile_digest,NoRedirect
        env.update(source_tree_sha256=source_digest(root/'src/hm_recommender'),uv_lock_sha256=fingerprint(root/'uv.lock'))
        request=http_request('http://127.0.0.1:'+values['API_PORT'],values['HM_APP_PASSWORD'])
        live=request('/health/live')
        if live['source_tree_sha256']!=env['source_tree_sha256']:raise ValueError('Rebuild and restart API before T5')
        if source_run:
            entry=source(artifacts,source_run)
            directory='data-jobs/'+source_run+'/work/release'
            dataset='/data/processed/hm-v1' if entry['dataset']=='hm' else '/artifacts/data-jobs/'+source_run+'/work/demo/dataset'
            if request('/health/ready')['release_id']!=entry['release_id']:raise ValueError('Activate the requested T4-A version first')
        else:
            phase='demo-package';log(phase)
            # Never replace an unrelated active service to run the synthetic demo.
            active=json.loads(call('exec','-T','api','python','-c',
                "import urllib.request,urllib.error,json\ntry:\n print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())\nexcept urllib.error.HTTPError as e:\n assert e.code==503;print('{}')"))
            output='events-demo/'+commit
            built=json.loads(call('run','--rm','--no-deps','-T','-e','HMREC_SOURCE_COMMIT='+commit,'jobs',
                'hmrec','events','demo-package','--output','/artifacts/'+output))
            directory=output+'/release';dataset='/artifacts/'+output+'/input/dataset'
            entry=dict(run_id=None,archive_sha256=None,release_id=built['release_id'],
                manifest_sha256=fingerprint(artifacts/directory/'manifest.json'),dataset='synthetic')
            if active.get('release_id') not in (None,entry['release_id']):raise ValueError('Use a separate clone for demo; unrelated release is active')
            request('/admin/releases/prepare',{'directory':directory})
            request('/admin/releases/activate',{'release_id':entry['release_id']})
        mode='simulation' if entry['dataset']=='hm' else 'synthetic'
        result=dict(stage='T5',protocol='events-v1',status='running',source_tree_sha256=env['source_tree_sha256'],
            source=entry,stream_id=stream,mode=mode,checks={},seed={},resources={},host={},restart_persistent=False,
            source_unchanged=False,test_evaluated=False,training_performed=False,load_benchmark=False,duration_seconds=0)
        phase='seed';log(phase)
        seed=json.loads(call('run','--name',container,'--no-deps','-T','events-job','hmrec','events','seed',
            '--dataset',dataset,'--directory','/artifacts/'+directory,'--stream-id',stream,'--mode',mode))
        if seed['process']['source_tree_sha256']!=env['source_tree_sha256']:raise ValueError('Seed image differs from checkout')
        result['seed']=seed['seed'];result['resources']['seed']=resources(container,seed['process'])
        phase='host-publication';log(phase)
        for service,port,target in [('api',values['API_PORT'],'8000/tcp'),('ui',values['UI_PORT'],'8501/tcp')]:
            cid=call('ps','-q',service)
            if inspect(cid)['NetworkSettings']['Ports'].get(target)!=[{'HostIp':'127.0.0.1','HostPort':port}]:raise ValueError('Expected localhost publication')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open('http://127.0.0.1:'+values['UI_PORT']+'/_stcore/health',timeout=10) as response:
            if response.status!=200:raise ValueError('UI unavailable')
        result['host']=dict(host='127.0.0.1',api_port=int(values['API_PORT']),ui_port=int(values['UI_PORT']),bindings_verified=True,ui_healthy=True)
        phase='replay';log(phase);result['checks']=replay(request,stream,entry['release_id'],mode)
        result['resources']['api']=resources(call('ps','-q','api'))
        phase='restart';log(phase);call('restart','api')
        deadline=time.monotonic()+180
        while True:
            try:
                if request('/health/ready')['release_id']!=entry['release_id']:raise ValueError('Active identity changed')
                break
            except (OSError,ValueError):
                if time.monotonic()>deadline:raise
                time.sleep(1)
        if profile_digest(request,stream)!=result['checks']['profile_sha256']:raise ValueError('Profile changed after restart')
        result['restart_persistent']=True
        for service in ('ui','postgres'):result['resources'][service]=resources(call('ps','-q',service))
        if source_run:
            if source(artifacts,source_run)!=entry:raise ValueError('Source changed')
        elif fingerprint(artifacts/directory/'manifest.json')!=entry['manifest_sha256']:raise ValueError('Demo source changed')
        result.update(source_unchanged=True,status='passed',duration_seconds=time.monotonic()-started)
        report.update(status='passed',result=result)
        from events_handoff import validate_events
        validate_events(report,result,env,{'manifest.json','result.json','environment.json','commit.txt','job.log'})
        code=0
    except (Exception,KeyboardInterrupt) as error:
        code=130 if isinstance(error,KeyboardInterrupt) else 1
        report['status']='interrupted' if code==130 else 'failed'
        result.update(status=report['status'],phase=phase,error_type=type(error).__name__)
        log(phase+': '+type(error).__name__)
    finally:
        # Only our unique one-shot container; never remove service volumes or stream data.
        subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    report.update(finished_at=now(),result=result,steps=[dict(name='events',log='job.log',status=report['status'],exit_code=code)])
    write_json(folder/'environment.json',env);write_json(folder/'result.json',result)
    archive=package_report(folder,report)
    print('Reporte cerrado: '+str(archive),flush=True)
    print('Estado: '+report['status']+'. UI: http://127.0.0.1:'+values['UI_PORT'],flush=True)
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine-id',required=True);parser.add_argument('--expected-commit',required=True)
    parser.add_argument('--source-run-id',required=True)
    args=parser.parse_args(argv)
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',args.machine_id) or not re.fullmatch(r'[a-f0-9]{40}',args.expected_commit):
        parser.error('Machine alias and full expected SHA required')
    return run(ROOT,args.machine_id,args.expected_commit,args.source_run_id)


if __name__=='__main__':raise SystemExit(main())
