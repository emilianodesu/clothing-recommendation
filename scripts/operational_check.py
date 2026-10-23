"""One-shot operational-v1 rehearsal. Historical runners and validators are untouched."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from bootstrap import read_configuration
from clock_check import checkout_commit,runner_digest
from events_clock import require
from events_memory import capture,diagnostics,as_resource
from handoff import now,package_report,write_json,validate_context
from operational_io import OperationalIO
from operational_timing import Monitor
from operational_handoff import PROTOCOL,final_validation,OperationalFailure
from serving_check import fingerprint,source

ROOT=Path(__file__).resolve().parents[1]


def run(root,machine_id,expected_commit,source_run=None):
    root=Path(root);values=read_configuration(root/'.env')
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else root/artifacts
    rid='events-'+machine_id+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    folder=artifacts/'handoff'/rid;folder.mkdir(parents=True,mode=0o700)
    container=values['COMPOSE_PROJECT_NAME']+'-'+rid;stream=str(uuid.uuid4())
    report=dict(schema_version=1,kind='events-handoff',run_id=rid,machine_id=machine_id,
        context=os.environ.get('DOCKER_CONTEXT','default'),source_commit=None,source_clean=False,
        started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    env=dict(run_id=rid,source_commit=None,source_tree_sha256=None,uv_lock_sha256=None,runner_tree_sha256=None)
    monitor=Monitor();io=OperationalIO(root,values,monitor);phase='preflight';attempted_seed=False;code=1
    observations=[];capture_errors=[]
    result=dict(stage='T5',protocol=PROTOCOL,status='failed',source_tree_sha256=None,source=None,stream_id=stream,
        mode=None,checks={},seed={},resources={},host={},restart_persistent=False,source_unchanged=False,
        test_evaluated=False,training_performed=False,load_benchmark=False,duration_seconds=0,
        diagnostics=diagnostics([],[]),temporal=monitor.evidence(),t5_closed=False,
        cleanup=dict(attempted=False,exit_code=None,error_type=None,utc_seconds=0.,monotonic_seconds=0.))
    print('Ejecución: '+rid,flush=True)
    def stage(name):
        nonlocal phase
        phase=name;print('T5 operativo: '+phase,flush=True)
    def observe(label):
        stage('memory-'+label)
        for service in ('api','ui','postgres'):
            count_before=len(observations)
            try:
                cid=io.call('ps','-q',service)
                def sample(left):
                    observation=capture(cid,service,label,monitor.marks[0]['monotonic_seconds'],
                        output=lambda command,**kw:io.output(command,**dict(kw,timeout=min(kw.get('timeout',30),left()))))
                    observations.append(observation)
                monitor.perform('sample_'+label+'_'+service,90,sample)
            except (Exception,KeyboardInterrupt) as error:
                if len(observations)==count_before:capture_errors.append(dict(phase=label,service=service,error_type=type(error).__name__))
                raise
        result['diagnostics']=diagnostics(observations,capture_errors)
    try:
        commit=checkout_commit(root,expected_commit)
        report.update(source_commit=commit,source_clean=True);env['source_commit']=commit
        monitor.start()
        endpoint=io.output(['docker','context','inspect','--format','{{.Endpoints.docker.Host}}'],text=True,label='docker_context').strip()
        arch=io.output(['docker','info','--format','{{.Architecture}}'],text=True,label='docker_info').strip()
        validate_context(endpoint,arch)
        sys.path.insert(0,str(root/'src'))
        from hm_recommender.api.runtime import source_digest
        from hm_recommender.events.replay import replay,profile_digest
        def code_identity(left):
            env.update(source_tree_sha256=source_digest(root/'src/hm_recommender'),uv_lock_sha256=fingerprint(root/'uv.lock'),runner_tree_sha256=runner_digest(root))
            result['source_tree_sha256']=env['source_tree_sha256']
        monitor.perform('code_digest',30,code_identity)
        require(io.request('/health/live')['source_tree_sha256']==env['source_tree_sha256'])
        if source_run:
            entry=monitor.perform('source',120,lambda left:source(artifacts,source_run))
            directory='data-jobs/'+source_run+'/work/release'
            dataset='/data/processed/hm-v1' if entry['dataset']=='hm' else '/artifacts/data-jobs/'+source_run+'/work/demo/dataset'
            require(io.request('/health/ready')['release_id']==entry['release_id'])
        else:
            stage('demo-package')
            active=json.loads(io.call('exec','-T','api','python','-c',
                "import urllib.request,urllib.error\ntry:\n print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=10).read().decode())\nexcept urllib.error.HTTPError as e:\n assert e.code==503;print('{}')"))
            output='events-demo/'+commit
            built=json.loads(io.call('run','--rm','--no-deps','-T','-e','HMREC_SOURCE_COMMIT='+commit,'jobs',
                'hmrec','events','demo-package','--output','/artifacts/'+output,label='demo_package'))
            directory=output+'/release';dataset='/artifacts/'+output+'/input/dataset'
            entry=dict(run_id=None,archive_sha256=None,release_id=built['release_id'],manifest_sha256=fingerprint(artifacts/directory/'manifest.json'),dataset='synthetic')
            require(active.get('release_id') in (None,entry['release_id']))
            io.request('/admin/releases/prepare',{'directory':directory});io.request('/admin/releases/activate',{'release_id':entry['release_id']})
            require(io.request('/health/ready')['release_id']==entry['release_id'])
        mode='simulation' if entry['dataset']=='hm' else 'synthetic';result.update(source=entry,mode=mode)
        observe('before_seed');stage('seed');attempted_seed=True
        seed=json.loads(io.call('run','--name',container,'--no-deps','-T','events-job','hmrec','events','seed',
            '--dataset',dataset,'--directory','/artifacts/'+directory,'--stream-id',stream,'--mode',mode,label='seed'))
        require(seed['process']['source_tree_sha256']==env['source_tree_sha256'])
        info=io.inspect(container)
        result['seed']=seed['seed'];result['resources']['seed']=dict(image_id=info['Image'],memory_bytes=info['HostConfig']['Memory'],
            nano_cpus=info['HostConfig']['NanoCpus'],cgroup_peak_bytes=seed['process']['cgroup_peak_bytes'],
            swap_peak_bytes=seed['process']['swap_peak_bytes'],oom_killed=info['State']['OOMKilled'])
        observe('after_seed');stage('host-publication')
        for service,port,target in [('api',values['API_PORT'],'8000/tcp'),('ui',values['UI_PORT'],'8501/tcp')]:
            require(io.inspect(io.call('ps','-q',service))['NetworkSettings']['Ports'].get(target)==[{'HostIp':'127.0.0.1','HostPort':port}])
        io.ui_health()
        result['host']=dict(host='127.0.0.1',api_port=int(values['API_PORT']),ui_port=int(values['UI_PORT']),bindings_verified=True,ui_healthy=True)
        stage('replay');result['checks']=replay(io.request,stream,entry['release_id'],mode)
        observe('after_replay');result['resources']['api']=as_resource(observations[-3])
        stage('restart');io.call('restart','api',label='restart',limit=180)
        restart_mark=monitor.marks[-1]
        while True:
            m=monitor.marks[-1]
            ready_left=180-max(m['utc_seconds']-restart_mark['utc_seconds'],m['monotonic_seconds']-restart_mark['monotonic_seconds'])
            if ready_left<=0:raise TimeoutError('Readiness deadline')
            try:
                ready=io.request('/health/ready',limit=ready_left);require(ready['release_id']==entry['release_id']);break
            except (OSError,ValueError):
                monitor.retry_ready()
                m=monitor.marks[-1]
                if max(m['utc_seconds']-restart_mark['utc_seconds'],m['monotonic_seconds']-restart_mark['monotonic_seconds'])>=180:raise TimeoutError('Readiness deadline')
                monitor.perform('pause',2,lambda left:time.sleep(min(1,left())))
        require(profile_digest(io.request,stream)==result['checks']['profile_sha256']);result['restart_persistent']=True
        observe('after_restart')
        for service in ('ui','postgres'):result['resources'][service]=as_resource(next(s for s in reversed(observations) if s['service']==service))
        require(monitor.perform('source_final',120,lambda left:source(artifacts,source_run))==entry if source_run else fingerprint(artifacts/directory/'manifest.json')==entry['manifest_sha256'])
        result['source_unchanged']=True;code=0
    except (Exception,KeyboardInterrupt) as error:
        code=130 if isinstance(error,KeyboardInterrupt) else 1
        result.update(phase=phase,error_type=type(error).__name__,failure=dict(component='execution',criterion='exception'))
    finally:
        if attempted_seed:
            wall,mono=time.time(),time.monotonic();result['cleanup']['attempted']=True
            try:
                cleanup=subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
                result['cleanup']['exit_code']=cleanup.returncode
                if cleanup.returncode:report['warnings'].append('seed_cleanup_failed')
            except (OSError,subprocess.SubprocessError) as error:
                result['cleanup']['error_type']=type(error).__name__;report['warnings'].append('seed_cleanup_failed')
            result['cleanup'].update(utc_seconds=time.time()-wall,monotonic_seconds=time.monotonic()-mono)
        monitor.finish()
        if not monitor.released:report['warnings'].append('guard_release_failed')
    result.update(diagnostics=diagnostics(observations,capture_errors),temporal=monitor.evidence())
    duration=result['temporal']['assessment']['monotonic_seconds'];result['duration_seconds']=max(0,duration or 0)
    if code==0 and report['warnings']:
        code=1;result.update(phase='cleanup',error_type='RuntimeError',failure=dict(component='execution',criterion='exception'))
    result['status']=report['status']='passed' if code==0 else ('interrupted' if code==130 else 'failed')
    report['result']=result
    if code==0:
        try:final_validation(report,result,env)
        except (ValueError,KeyError,TypeError) as error:
            code=1;result['status']=report['status']='failed'
            result.update(phase='final-validation',error_type=type(error).__name__,
                failure=error.failure if isinstance(error,OperationalFailure) else dict(component='execution',criterion='exception'))
    report.update(finished_at=now(),steps=[dict(name='events-operational',log='job.log',status=report['status'],exit_code=code)])
    (folder/'commit.txt').write_text((report['source_commit'] or 'unavailable')+'\n')
    (folder/'job.log').write_text('operational T5\nstatus: '+report['status']+'\n')
    write_json(folder/'environment.json',env);write_json(folder/'result.json',result)
    archive=package_report(folder,report)
    print('Reporte cerrado: '+str(archive),flush=True)
    print('Estado: '+report['status']+'. T5 requiere revisión; no acredita carga T8.',flush=True)
    return code
