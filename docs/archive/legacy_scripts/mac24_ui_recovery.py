#!/usr/bin/env python3
"""One authorized UI recovery preparation; never replaces the serving UI."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import subprocess
import sys
import time
import traceback
import uuid

from bootstrap import read_configuration
from clock_check import checkout_commit
from clock_guard import AwakeGuard
from handoff import write_json, now
from mac24_ui_inventory import snapshot, docker as query, TEMPLATE, verify_stable
from mac24_terminal import RELEASE
from mac24_t7 import check_airflow
from pipeline_check import request_json
from project import compose
from ui_recovery_control import require, identity, select_targets, admit, restored_services, G
import ui_recovery_public as public

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = 'colima-hm-project'
STATE = ROOT / '.runtime/ui-recovery-terminal-v1'
HISTORY = '6130943641e599121119660693ed276cf3b3fbca'
UI_SHA = '5a59c1c6014f5c5fc4980783f398cff5b45c116d52ac09c1951feb12450fa4d8'
INVENTORY = ROOT / '.runtime/ui-inventory-f5c6fc2580044621a6e599e8804e2510/report.json'
PROBE_SOURCE = '''import hashlib,json,platform,time,urllib.request
import httpx,streamlit
from pathlib import Path
for attempt in range(45):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health',timeout=1) as r:
   assert r.status==200 and r.read(1024).strip()==b'ok'
  break
 except Exception:
  if attempt==44:raise
  time.sleep(2)
print(json.dumps(dict(health='ok',python=platform.python_version(),streamlit=streamlit.__version__,httpx=httpx.__version__,ui_sha256=hashlib.sha256(Path('/opt/hmrec/src/hm_recommender/ui/app.py').read_bytes()).hexdigest())))
'''


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def emit(message):
    # A lost SSH output pipe must not prevent the finally recovery block.
    try:print(message,flush=True)
    except (BrokenPipeError,OSError):pass


def run_command(args, *, log=None, timeout=60, env=None):
    # Long-running output stays private; progress is emitted every 20 seconds.
    if log is None:
        result=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout,env=env)
        require(result.returncode==0,'Command failed; see private logs')
        return result.stdout.strip()
    with log.open('w') as output:
        process=subprocess.Popen(args,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        end=time.monotonic()+timeout
        try:
            while True:
                try:
                    code=process.wait(timeout=min(20,max(.01,end-time.monotonic())))
                    require(code==0,'Command failed; see '+log.name)
                    return ''
                except subprocess.TimeoutExpired:
                    if time.monotonic()>=end:raise
                    emit('Preparación UI: proceso activo; log '+log.name)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()
            raise


def docker(*args, **kwargs):return run_command(['docker','--context',CONTEXT,*args],**kwargs)


def inspect(cid):return json.loads(query('inspect','--format',TEMPLATE,cid))


def named(name):
    ids=query('ps','-aq','--no-trunc','--filter','name=^/'+name+'$').splitlines()
    require(len(ids)<=1,'Duplicate temporary name')
    return ids[0] if ids else None


def healthy(cid):
    end=time.monotonic()+180
    while True:
        row=inspect(cid)
        if row['running'] and row['health']=='healthy':return row
        require(time.monotonic()<end,'Recovery health timeout')
        time.sleep(2)


def airflow(values):
    check_airflow(ROOT,values)
    program="from hm_recommender.database import connect\nwith connect('airflow') as c:\n c.execute('SET TRANSACTION READ ONLY')\n print(c.execute(\"SELECT count(*) FROM dag_run WHERE state IN ('running','queued')\").fetchone()[0])"
    require(compose(ROOT,values,'exec','-T','airflow-scheduler','/opt/hmrec/.venv/bin/python','-c',program,capture=True,timeout=30).strip()=='0','Airflow active')
    program="from hm_recommender.database import connect\nwith connect('mlflow') as c:\n c.execute('SET TRANSACTION READ ONLY')\n print(c.execute(\"SELECT count(*) FROM runs WHERE status='RUNNING'\").fetchone()[0])"
    require(compose(ROOT,values,'exec','-T','mlflow','python','-c',program,capture=True,timeout=30).strip()=='0','MLflow has active runs; inspect before maintenance')


def ready(values):
    value=request_json(int(values['API_PORT']),values['HM_APP_PASSWORD'],'/health/ready')
    require(value['release_id']==RELEASE,'Original H release differs')
    return value


def disk_free(text):
    return int(text.splitlines()[-1].split()[3])*1024


def followup_inventory(before, original):
    previous=ROOT/'.runtime/ui-recovery-terminal-v1'
    report=ROOT/'evidence/ui-recovery-maintenance-mac24.json'
    require(sha(report)==public.REPORT_SHA,'Published antecedent changed')
    state=json.loads((previous/'state.json').read_text())
    verify_stable(original,state['before'])
    public.verify_attempt(state,json.loads(report.read_text()),
        (previous/'builder-bootstrap.log').read_text(errors='replace'),
        (previous/'build.log').exists(),(previous/'probe-create.log').exists())
    comparable=dict(before)
    prior_builder=named(state['builder_container'])
    if prior_builder and prior_builder not in state['before']:
        require(not comparable[prior_builder]['running'],'Prior builder still active')
        comparable.pop(prior_builder)
    require(named(state['probe_name']) is None,'Unexpected prior probe')
    public.verify_recovered_inventory(state['before'],comparable,state['aux'])
    return dict(prior_run_id=state['run_id'],prior_state_sha256=sha(previous/'state.json'))


def plugin_followup_inventory(before, antecedent):
    previous=ROOT/'.runtime/ui-recovery-public-v1'
    report=ROOT/'evidence/ui-recovery-public-mac24.json'
    require(sha(report)==public.PLUGIN_REPORT_SHA,'Published plugin mismatch antecedent changed')
    state=json.loads((previous/'state.json').read_text())
    unexpected=any((previous/name).exists() for name in ('builder-create.log','builder-bootstrap.log',
        'build.log','probe-create.log','public-buildkit-pull.log','public-manifest-0.log'))
    public.verify_plugin_attempt(state,json.loads(report.read_text()),
        (previous/'failure.log').read_text(errors='replace'),unexpected)
    require(all(state.get(k)==v for k,v in antecedent.items()),'Prior chain of evidence changed')
    verify_stable(state['before'],before)
    require(named(state['builder_container']) is None and named(state['probe_name']) is None,
            'Unexpected container from plugin mismatch attempt')
    return dict(plugin_source_run_id=state['run_id'],plugin_source_state_sha256=sha(previous/'state.json'))


def preflight(commit, values, *, public_followup=False, buildx_followup=False):
    checkout_commit(ROOT,commit)
    require(run_command(['git','branch','--show-current'])=='main','Expected main')
    require(not STATE.exists(),'Preparation already recorded; use restore-only if needed')
    require(run_command(['uname','-m'])=='arm64','Expected Apple Silicon')
    require('AC Power' in run_command(['pmset','-g','batt']),'Connect AC')
    lid=run_command(['ioreg','-r','-k','AppleClamshellState','-d','4'])
    require(re.search(r'"AppleClamshellState"\s*=\s*No\b',lid),'Open laptop lid')
    evidence=json.loads((ROOT/'evidence/ui-recovery-mac24.json').read_text())
    matches=[p for p in (ROOT/'.runtime').glob('ui-recovery*/*.json')
             if p.is_file() and not p.is_symlink() and p.stat().st_size<4*1024*1024 and sha(p)==evidence['private_report_sha256']]
    require(bool(matches),'Missing private capacity report matching published hash')
    prior=json.loads(INVENTORY.read_text())
    require(prior['status']=='passed' and prior['source_commit']=='7fe23502cfa52134ed77565149f0af97e816fbfb','Inventory origin differs')
    before=snapshot()
    antecedent=followup_inventory(before,prior['before']) if public_followup else {}
    if buildx_followup:antecedent.update(plugin_followup_inventory(before,antecedent))
    if not public_followup:verify_stable(prior['before'],before)
    review=json.loads((ROOT/'evidence/portability-review-mac24.json').read_text())
    aux=select_targets(before,values['COMPOSE_PROJECT_NAME'],review['result']['destination_postgres']['container_id'])
    kept={cid:r for cid,r in before.items() if r['running'] and cid not in {x['container_id'] for x in aux}}
    engine=json.loads(query('info','--format','{"memory":{{.MemTotal}},"cpus":{{.NCPU}},"architecture":{{json .Architecture}}}'))
    require(engine['cpus']==4 and 13*G<=engine['memory']<=14*G and engine['architecture']=='aarch64','Unexpected VM')
    planned=admit(kept,engine['memory'],2*G)
    require(shutil.disk_usage(ROOT).free>=10*G,'Host disk <10 GiB')
    vm_free=disk_free(run_command(['colima','ssh','--profile','hm-project','--','sudo','-n','df','-Pk','/var/lib/docker']))
    require(vm_free>=10*G,'VM disk <10 GiB')
    airflow(values);initial_ready=ready(values)
    ui=next(r for r in kept.values() if r['service']=='ui')
    require(query('exec',ui['container_id'],'sha256sum','/opt/hmrec/src/hm_recommender/ui/app.py').split()[0]==UI_SHA,'Running UI differs')
    run_command(['git','cat-file','-e',HISTORY+'^{commit}'])
    buildx=query('buildx','version')
    verify_stable(before,snapshot())
    return dict(before=before,aux=aux,kept=kept,engine=engine,ready=initial_ready,planned_build_bytes=planned,vm_disk_free_bytes=vm_free,buildx=buildx,**antecedent)


def build_docker(state, *args, **kwargs):
    if not state.get('public_followup'):return docker(*args,**kwargs)
    config=STATE/'docker-public'
    public.verify_config(config)
    public.verify_plugin(config,state['buildx_plugin_sha256'])
    return run_command(['docker','--config',str(config),'--context',CONTEXT,*args],
                       env=public.environment(config),**kwargs)


def prepare_public_registry(state):
    config=STATE/'docker-public'
    plugin=public.selected_buildx(json.loads(query('info','--format','{{json .ClientInfo.Plugins}}')))
    state.update(buildx_plugin_path=str(plugin),buildx_plugin_sha256=sha(plugin),buildx_identity_verified=False)
    save(state)
    endpoint=query('context','inspect',CONTEXT,'--format','{{.Endpoints.docker.Host}}')
    public.verify_endpoint(endpoint,Path.home())
    original_engine=query('info','--format','{{.ID}}')
    require(bool(original_engine),'Missing engine identity')
    public.create_config(config,plugin)
    run_command(['docker','--config',str(config),'context','create',CONTEXT,'--docker','host='+endpoint],
                env=public.environment(config),log=STATE/'public-context.log')
    require(build_docker(state,'info','--format','{{.ID}}')==original_engine,'Private context engine differs')
    state['private_buildx']=build_docker(state,'buildx','version');save(state)
    require(state['private_buildx']==state['buildx'],'Private Buildx version differs')
    state['buildx_identity_verified']=True;save(state)
    source=run_command(['git','show',HISTORY+':docker/app.Dockerfile'])
    refs=re.findall(r'(?:^FROM |COPY --from=)(\S+)',source,re.MULTILINE)
    require(len(refs)==2 and all(re.fullmatch(r'(?:python|astral/uv):[^\s@]+@sha256:[0-9a-f]{64}',r) for r in refs),
            'Unexpected historical public base images')
    state['public_images']=[public.BUILDKIT,*refs];save(state)
    for index,ref in enumerate(state['public_images']):
        build_docker(state,'buildx','imagetools','inspect',ref,log=STATE/f'public-manifest-{index}.log',timeout=120)
    # Download only the bounded builder image before any service pause. No
    # container is created here; a registry/rate-limit error leaves services up.
    build_docker(state,'pull','--platform','linux/arm64',public.BUILDKIT,log=STATE/'public-buildkit-pull.log',timeout=300)
    state['public_buildkit_image_id']=query('image','inspect','--format','{{.Id}}',public.BUILDKIT)
    state['public_registry_checked']=True;save(state)


def save(state):write_json(STATE/'state.json',state)


def stop_temporary(state):
    # Removal/logging are secondary to stopping processes before restoring services.
    errors=[];stopped=True;removed=True
    try:
        probe=named(state['probe_name'])
        if probe:
            labels=json.loads(query('inspect','--format','{{json .Config.Labels}}',probe))
            require(labels.get('hmrec.ui-recovery')==state['run_id'],'Probe ownership differs')
            if state.get('image_id'):require(inspect(probe)['image_id']==state['image_id'],'Probe image differs')
            if inspect(probe)['running']:docker('stop','--time','10',probe,timeout=30)
            require(not inspect(probe)['running'],'Probe still running')
            try:docker('logs',probe,log=STATE/'probe.log',timeout=30)
            except Exception as error:state['probe_log_error']=type(error).__name__
            try:docker('rm',probe,timeout=30)
            except Exception as error:
                removed=False;errors.append(dict(resource='probe_removal',error_type=type(error).__name__))
    except Exception as error:
        stopped=False;errors.append(dict(resource='probe_stop',error_type=type(error).__name__))
    try:
        builder=named(state['builder_container'])
        if builder:
            current=inspect(builder)
            if state.get('builder_id'):require(current['container_id']==state['builder_id'],'Builder ownership differs')
            if current['running']:docker('stop','--time','15',builder,timeout=45)
            require(not inspect(builder)['running'],'Builder still running')
    except Exception as error:
        stopped=False;errors.append(dict(resource='builder_stop',error_type=type(error).__name__))
    state['temporary_cleanup_complete']=removed and stopped
    state['cleanup_errors']=errors
    require(stopped,'Temporary processes not confirmed stopped; recovery deferred')
    # Preserve stopped builder, its cache and historical worktree.
    return True


def recover(state,values):
    # Do not restore the old high-limit inventory over a live build/probe.
    state['temporary_stopped']=stop_temporary(state);save(state)
    state['restoration']=restored_services(state['aux'],inspect,
        lambda cid:docker('start',cid,timeout=60),healthy)
    state['auxiliaries_restored']=all(r['restored'] for r in state['restoration']);save(state)
    after=snapshot()
    for cid,row in state['before'].items():
        if cid not in {r['container_id'] for r in state['aux']}:
            verify_stable({cid:row},{cid:after[cid]})
    state['original_services_preserved']=True
    require(state['auxiliaries_restored'],'An auxiliary still needs recovery')
    airflow(values);require(ready(values)==state['ready'],'Original release changed')
    state['recovery_complete']=True;save(state)


def recover_or_verify(state,values):
    if state.get('maintenance_started',True):return recover(state,values)
    # A failed public registry check never authorizes starting a service that
    # somebody else stopped. This branch is also used by restore-only.
    verify_stable(state['before'],snapshot())
    require(ready(values)==state['ready'],'Original release changed')
    state.update(original_services_preserved=True,auxiliaries_restored=True,
                 recovery_complete=True,temporary_stopped=True,temporary_cleanup_complete=True)
    save(state)


def construct(state):
    source=STATE/'source'
    previous=os.umask(0o022)
    try:run_command(['git','worktree','add','--detach',str(source),HISTORY],log=STATE/'worktree.log')
    finally:os.umask(previous)
    (STATE/'worktree.log').chmod(0o600)
    require(sha(source/'src/hm_recommender/ui/app.py')==UI_SHA,'Historical UI checksum differs')
    certs=ssl.create_default_context().get_ca_certs(binary_form=True)
    require(bool(certs),'Missing trusted local CA')
    ca=STATE/'build-ca.pem';ca.write_text(''.join(ssl.DER_cert_to_PEM_cert(c) for c in certs))
    options='memory=2g,memory-swap=2g,cpu-period=100000,cpu-quota=100000,restart-policy=no'
    if state.get('public_followup'):options+=',image='+public.BUILDKIT
    build_docker(state,'buildx','create','--name',state['builder'],'--driver','docker-container',
        '--driver-opt',options,
        CONTEXT,log=STATE/'builder-create.log')
    build_docker(state,'buildx','inspect',state['builder'],'--bootstrap',log=STATE/'builder-bootstrap.log',timeout=300)
    cid=named(state['builder_container']);require(bool(cid),'Builder container not found')
    state['builder_id']=cid;save(state)
    limits=json.loads(query('inspect','--format','{"memory":{{.HostConfig.Memory}},"swap":{{.HostConfig.MemorySwap}},"period":{{.HostConfig.CpuPeriod}},"quota":{{.HostConfig.CpuQuota}}}',cid))
    require(limits==dict(memory=2*G,swap=2*G,period=100000,quota=100000),'Builder limits differ')
    state['builder_limits']=limits;state['buildkit_image']=inspect(cid)['image_id'];save(state)
    if state.get('public_followup'):
        require(state['buildkit_image']==state['public_buildkit_image_id'],'Builder image differs from public preflight')
    state['build_started']=True;save(state)
    build_docker(state,'buildx','build','--builder',state['builder'],'--platform','linux/arm64','--load',
        '-f',str(source/'docker/app.Dockerfile'),'--secret','id=build_ca,src='+str(ca),
        '-t',state['image_tag'],str(source),log=STATE/'build.log',timeout=1800)
    state['image_id']=query('image','inspect','--format','{{.Id}}',state['image_tag']);save(state)
    docker('stop','--time','15',cid,timeout=45)
    require(not inspect(cid)['running'],'Builder still active')
    admit(snapshot(),state['engine']['memory'],G//2)
    docker('create','--name',state['probe_name'],'--label','hmrec.ui-recovery='+state['run_id'],
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--memory','512m','--memory-swap','512m','--cpus','0.5','--pids-limit','128','--tmpfs','/tmp:rw,nosuid,size=128m',
        '--user','10001:10001','--entrypoint','streamlit',state['image_id'],
        'run','/opt/hmrec/src/hm_recommender/ui/app.py','--server.address=0.0.0.0','--server.port=8501',
        '--server.headless=true','--browser.gatherUsageStats=false',log=STATE/'probe-create.log')
    probe=named(state['probe_name']);require(bool(probe),'Probe missing')
    state['probe_id']=probe;save(state)
    limits=json.loads(query('inspect','--format','{"memory":{{.HostConfig.Memory}},"swap":{{.HostConfig.MemorySwap}},"nano_cpus":{{.HostConfig.NanoCpus}},"network":{{json .HostConfig.NetworkMode}},"readonly":{{.HostConfig.ReadonlyRootfs}},"ports":{{json .HostConfig.PortBindings}}}',probe))
    require(limits['memory']==G//2 and limits['swap']==G//2 and limits['nano_cpus']==500000000 and limits['network']=='none' and limits['readonly'] and not limits['ports'],'Probe isolation differs')
    state['probe_limits']=limits
    state['probe_started']=True;save(state)
    docker('start',probe)
    checks=json.loads(docker('exec',probe,'python','-B','-c',PROBE_SOURCE,timeout=100))
    require(checks['ui_sha256']==UI_SHA and checks['health']=='ok','Recovery image checks failed')
    state['probe_checks']=checks;state['image_verified']=True;save(state)


def summary(state):
    return {k:state[k] for k in ('run_id','procedure_commit','status','stage','created_at','historical_commit',
        'image_id','image_verified','build_started','probe_started','engine','planned_build_bytes','vm_disk_free_bytes','buildx','buildkit_image',
        'builder_limits','probe_limits','probe_checks','restoration','auxiliaries_restored',
        'original_services_preserved','recovery_complete','temporary_stopped','deployment_started','error_type',
        'failure_stage','recovery_error_type','probe_log_error','temporary_cleanup_complete','cleanup_errors',
        'public_followup','prior_run_id','prior_state_sha256','public_registry_checked','public_images',
        'public_buildkit_image_id','maintenance_started','buildx_followup','plugin_source_run_id',
        'plugin_source_state_sha256','buildx_plugin_sha256','private_buildx','buildx_identity_verified') if k in state}


def publish(state):
    checkout_commit(ROOT,state['procedure_commit'])
    repository=json.loads(run_command(['gh','repo','view','--json','nameWithOwner,isPrivate']))
    require(repository.get('nameWithOwner')=='emilianodesu/clothing-recommendation','Unexpected project repository')
    filename=('ui-recovery-buildx-mac24.json' if state.get('buildx_followup') else
              'ui-recovery-public-mac24.json' if state.get('public_followup') else 'ui-recovery-maintenance-mac24.json')
    destination=ROOT/'evidence'/filename
    require(not destination.exists(),'Aggregate already exists; preserve and review it')
    write_json(destination,summary(state))
    run_command(['git','add',str(destination)])
    run_command(['git','commit','--only',str(destination),'-m','docs: report mac24 UI recovery maintenance'],log=STATE/'publish-commit.log')
    run_command(['git','-c','credential.helper=','-c','credential.helper=!gh auth git-credential','push','origin','HEAD:main'],log=STATE/'publish-push.log',timeout=120)
    emit('Resumen publicado en GitHub.')


def execute_prepared(state,values,guard):
    state['maintenance_started']=False
    try:
        if state.get('public_followup'):
            state['stage']='public_registry_preflight';save(state)
            emit('UI recovery: verificar imágenes públicas sin llavero; servicios activos')
            prepare_public_registry(state);guard.check()
            verify_stable(state['before'],snapshot())
            airflow(values);require(ready(values)==state['ready'],'Original release changed')
            require(shutil.disk_usage(ROOT).free>=10*G,'Host disk <10 GiB after pull')
            require(disk_free(run_command(['colima','ssh','--profile','hm-project','--','sudo','-n','df','-Pk','/var/lib/docker']))>=10*G,'VM disk <10 GiB after pull')
        state['stage']='stop_six_auxiliaries';save(state)
        state['maintenance_started']=True;save(state)
        emit('UI recovery: detener los seis auxiliares autorizados')
        for row in state['aux']:
            current=inspect(row['container_id']);identity(row,current)
            require(current['running'] and current['health']=='healthy','Auxiliary changed before stop')
            docker('stop','--time','30',row['container_id'],timeout=60)
            require(not inspect(row['container_id'])['running'],'Auxiliary did not stop')
        require({cid for cid,row in snapshot().items() if row['running']}==set(state['kept']),'Unexpected active service')
        admit(snapshot(),state['engine']['memory'],2*G);guard.check()
        state['stage']='build_and_probe';save(state)
        emit('UI recovery: construir imagen histórica y probar Streamlit aislado')
        construct(state);guard.check()
    except BaseException as error:
        state['error_type']=type(error).__name__;state['failure_stage']=state['stage']
        (STATE/'failure.log').write_text(traceback.format_exc())
    finally:
        state['stage']='recover_auxiliaries';save(state)
        emit('UI recovery: recuperar auxiliares' if state['maintenance_started'] else 'UI recovery: verificar servicios sin pausa')
        try:recover_or_verify(state,values)
        except BaseException as error:
            state['recovery_error_type']=type(error).__name__;state['recovery_complete']=False
            (STATE/'recovery-failure.log').write_text(traceback.format_exc())
        state['status']='passed' if state['image_verified'] and state['recovery_complete'] and state['temporary_cleanup_complete'] and 'error_type' not in state else 'failed'
        state['stage']='complete' if state['recovery_complete'] else 'recovery_pending';save(state)
        write_json(STATE/'summary.json',summary(state));emit(json.dumps(summary(state),sort_keys=True))

def main():
    global STATE
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-commit',required=True)
    p.add_argument('--restore-only',action='store_true');p.add_argument('--publish',action='store_true')
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--public-registry-followup',action='store_true')
    modes.add_argument('--buildx-followup',action='store_true');a=p.parse_args()
    public_followup=a.public_registry_followup or a.buildx_followup
    if a.buildx_followup:STATE=ROOT/'.runtime/ui-recovery-buildx-v1'
    elif a.public_registry_followup:STATE=ROOT/'.runtime/ui-recovery-public-v1'
    os.umask(0o077);os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT=CONTEXT,GIT_TERMINAL_PROMPT='0')
    values=read_configuration(ROOT/'.env')
    artifacts=Path(values['ARTIFACTS_DIR']);artifacts=artifacts if artifacts.is_absolute() else ROOT/artifacts
    # Close an SSH terminal gracefully via finally; SIGKILL/power loss requires restore-only.
    def interrupt(*_):raise KeyboardInterrupt('Signal received')
    for sig in (signal.SIGHUP,signal.SIGTERM):signal.signal(sig,interrupt)
    with (artifacts/'.heavy-launch.lock').open('a') as lock,AwakeGuard() as guard:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.restore_only:
            state=json.loads((STATE/'state.json').read_text())
            require(state['procedure_commit']==a.expected_commit,'Recovery commit differs')
            for name,digest in state['runner_files'].items():require(sha(ROOT/name)==digest,'Recovery code differs')
            recover_or_verify(state,values)
            state['stage']='complete';save(state)
            write_json(STATE/'summary.json',summary(state));emit(json.dumps(summary(state),sort_keys=True))
            emit('Auxiliares recuperados; no se repitió construcción.' if state.get('maintenance_started',True)
                 else 'Servicios verificados sin pausa ni arranque; no se repitió preparación.')
            if a.publish:publish(state)
            return 0
        emit('UI recovery: comprobaciones previas; sin pausas todavía')
        ctx=preflight(a.expected_commit,values,public_followup=public_followup,buildx_followup=a.buildx_followup)
        STATE.mkdir(mode=0o700)
        rid='ui-recovery-'+uuid.uuid4().hex[:12]
        state=dict(ctx,run_id=rid,procedure_commit=a.expected_commit,created_at=now(),historical_commit=HISTORY,
            public_followup=public_followup,buildx_followup=a.buildx_followup,
            builder=rid,builder_container='buildx_buildkit_'+rid+'0',probe_name=rid+'-probe',
            image_tag='hmrec-ui-recovery:'+rid,status='failed',stage='reserved',image_verified=False,
            deployment_started=False,build_started=False,probe_started=False,recovery_complete=False,auxiliaries_restored=False,
            runner_files={str(f.relative_to(ROOT)):sha(f) for f in (ROOT/'scripts').glob('*.py')})
        save(state);emit('Logs privados: '+str(STATE))
        execute_prepared(state,values,guard)
        if a.publish and state['recovery_complete']:publish(state)
        elif not state['recovery_complete']:
            mode='--buildx-followup ' if a.buildx_followup else '--public-registry-followup ' if a.public_registry_followup else ''
            emit('Recuperación/verificación pendiente: usar únicamente '+mode+'--restore-only con el mismo SHA.')
        return 0 if state['status']=='passed' else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print('DETENIDO: '+type(error).__name__+(': '+str(error) if isinstance(error,ValueError) else '')+'. Conservar logs; no repetir preparación.',file=sys.stderr)
        sys.exit(1)
