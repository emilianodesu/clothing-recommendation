#!/usr/bin/env python3
"""One bounded UI deployment; keep API/PostgreSQL and recover auxiliaries."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import traceback
import uuid
from http.client import HTTPConnection

from bootstrap import read_configuration
from clock_check import checkout_commit
from clock_guard import AwakeGuard
from handoff import now,write_json
import mac24_ui_recovery as r
import ui_deploy_control as control
from ui_recovery_control import require,identity,select_targets,restored_services,admit,G
from ui_recovery_public import verify_recovered_inventory

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.runtime/ui-deploy-v1'
SOURCE=ROOT/'.runtime/ui-recovery-buildx-v1'
SOURCE_SHA='c5d758bfe2e750a6b2f3a9db7cbad5a5594794e4b52644365e818621d232dd35'
HISTORICAL_IMAGE='sha256:38b8937c7c16245f3198e5a1e1561a9dae75d5857d78cc4bfdcfcdb211fc4fe1'
TARGET_UI='fb1cce631c29550e7e138628c2cbbcbb6b86639bd775b9c9e7c5bd99219234da'
OLD_UI='5a59c1c6014f5c5fc4980783f398cff5b45c116d52ac09c1951feb12450fa4d8'
HISTORICAL_COMMIT='6130943641e599121119660693ed276cf3b3fbca'

SIGNATURE_PROGRAM="""import hashlib,json
from hm_recommender.database import connect
from hm_recommender.portability import state,TABLES
from psycopg import sql
with connect() as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 value=dict(state=state(c),counts={t:c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in TABLES})
 print(hashlib.sha256(json.dumps(value,default=str,sort_keys=True).encode()).hexdigest())
"""

LIVE_RENDER_PROGRAM="""import json,os
import httpx
from streamlit.testing.v1 import AppTest
with httpx.Client(base_url=os.environ['HMREC_API_URL'],trust_env=False,timeout=30) as c:
 response=c.get('/health/ready');response.raise_for_status()
 response=c.get('/streams');response.raise_for_status();streams=response.json()['items']
a=AppTest.from_file('/opt/hmrec/src/hm_recommender/ui/app.py',default_timeout=60).run()
assert not a.exception and not a.error
assert a.title[0].value=='Tu próxima selección'
if streams:
 selected=a.selectbox(key='stream').value
 sid=selected['stream_id'] if isinstance(selected,dict) else selected
 chosen=next(s for s in streams if s['stream_id']==sid)
 captions='\\n'.join(c.value for c in a.caption)
 for expected in ('Revisión '+str(chosen['revision']),str(chosen['published_at']),str(chosen['received_before'])):assert expected in captions
print(json.dumps(dict(protocol='ui-live-render-v1',rendered=True,metadata_matches_api=bool(streams),streams_present=bool(streams),writes_performed=False)))
"""


def save(state):write_json(STATE/'state.json',state)


def compose_env(values):
    env=dict(os.environ)
    for key in ('COMPOSE_FILE','COMPOSE_PROFILES','DOCKER_HOST'):env.pop(key,None)
    env.update(values);env['DOCKER_CONTEXT']=r.CONTEXT
    return env


def resolved(values,override=None):
    return json.loads(r.run_command(control.compose_command(ROOT,values,override,'--profile','*','config','--format','json'),
                                    env=compose_env(values)))


def full_inspect(cid):return json.loads(r.query('inspect',cid))[0]


def ui_id(values):
    rows=r.snapshot()
    matches=[cid for cid,row in rows.items() if row['project']==values['COMPOSE_PROJECT_NAME'] and row['service']=='ui']
    require(len(matches)==1,'Expected one UI container')
    return matches[0]


def signature(state):
    cid=next(cid for cid,row in state['kept'].items() if row['service']=='api')
    value=r.docker('exec',cid,'python','-B','-c',SIGNATURE_PROGRAM,timeout=180)
    require(bool(re.fullmatch('[0-9a-f]{64}',value)),'Invalid application state signature')
    return value


def source_inventory(before):
    report_path=ROOT/'evidence/ui-recovery-buildx-mac24.json'
    require(r.sha(report_path)==SOURCE_SHA,'Recovery report changed')
    report=json.loads(report_path.read_text());prior=json.loads((SOURCE/'state.json').read_text())
    review=json.loads((ROOT/'evidence/ui-recovery-review-mac24.json').read_text())
    require(review['recovery_preparation_closed'] and review['source_report_sha256']==SOURCE_SHA,'Recovery not accepted')
    require(report['status']=='passed' and report['image_id']==HISTORICAL_IMAGE,'Unexpected recovery image')
    require(all(prior.get(key)==value for key,value in report.items()),'Private recovery state differs')
    require(r.named(prior['probe_name']) is None,'Recovery probe remains')
    builder=prior['builder_id'];require(builder in before and not before[builder]['running'],'Recovery builder changed')
    require(before[builder]['image_id']==report['buildkit_image'],'Recovery builder image changed')
    old=dict(before);old.pop(builder)
    verify_recovered_inventory(prior['before'],old,prior['aux'])
    require(r.query('image','inspect','--format','{{.Id}}',HISTORICAL_IMAGE)==HISTORICAL_IMAGE,'Recovery image unavailable')
    return prior


def preflight(commit,values):
    checkout_commit(ROOT,commit)
    require(r.run_command(['git','branch','--show-current'])=='main','Expected main')
    require(not STATE.exists(),'Deployment already reserved; inspect saved state instead of repeating')
    require(r.run_command(['uname','-m'])=='arm64','Expected Apple Silicon')
    require('AC Power' in r.run_command(['pmset','-g','batt']),'Connect AC')
    require(re.search(r'"AppleClamshellState"\s*=\s*No\b',r.run_command(['ioreg','-r','-k','AppleClamshellState','-d','4'])),'Open lid')
    require(r.sha(ROOT/'src/hm_recommender/ui/app.py')==TARGET_UI,'Candidate UI changed')
    for path in ('docker/app.Dockerfile','pyproject.toml','uv.lock'):
        require(r.run_command(['git','show',HISTORICAL_COMMIT+':'+path])==(ROOT/path).read_text().strip(),'Build dependencies changed')
    before=r.snapshot();prior=source_inventory(before)
    t7=json.loads((ROOT/'evidence/portability-review-mac24.json').read_text())
    aux=select_targets(before,values['COMPOSE_PROJECT_NAME'],t7['result']['destination_postgres']['container_id'])
    kept={cid:row for cid,row in before.items() if row['running'] and cid not in {a['container_id'] for a in aux}}
    original_ui=next(cid for cid,row in kept.items() if row['service']=='ui')
    require(r.query('exec',original_ui,'sha256sum','/opt/hmrec/src/hm_recommender/ui/app.py').split()[0]==OLD_UI,'Live UI differs')
    engine=json.loads(r.query('info','--format','{"memory":{{.MemTotal}},"cpus":{{.NCPU}},"architecture":{{json .Architecture}}}'))
    require(engine['cpus']==4 and 13*G<=engine['memory']<=14*G and engine['architecture']=='aarch64','Unexpected engine capacity')
    base=resolved(values);ui=base['services']['ui'];contract=control.ui_contract(full_inspect(original_ui),ui['environment'])
    require(contract['environment']==ui['environment'],'Live UI environment differs from Compose')
    host=contract['host'];require(host['Memory']==G//2 and host['NanoCpus']==500000000 and host['ReadonlyRootfs'],'UI resource contract differs')
    require(host['PortBindings']=={'8501/tcp':[{'HostIp':'127.0.0.1','HostPort':str(values.get('UI_PORT','8501'))}]},'UI binding differs')
    state=dict(before=before,aux=aux,kept=kept,original_ui=original_ui,engine=engine,
        source_run_id=prior['run_id'],source_report_sha256=SOURCE_SHA,source_state_sha256=r.sha(SOURCE/'state.json'),
        historical_image=HISTORICAL_IMAGE,candidate_ui_sha256=TARGET_UI,
        planned_build_bytes=admit(kept,engine['memory'],2*G),ready=r.ready(values),
        base_config=base,ui_contract=contract,environment_keys=list(ui['environment']),
        configuration_hashes={name:r.sha(ROOT/name) for name in ('.env','compose.yaml')},
        buildx=r.query('buildx','version'))
    r.airflow(values);state['state_signature']=signature(state)
    before_maintenance(state,values)
    return state


def before_maintenance(state,values):
    for name,digest in state['configuration_hashes'].items():require(r.sha(ROOT/name)==digest,'Deployment configuration changed')
    r.verify_stable(state['before'],r.snapshot())
    r.airflow(values);require(r.ready(values)==state['ready'],'H readiness changed')
    require(signature(state)==state['state_signature'],'Application state changed before maintenance')
    require(shutil.disk_usage(ROOT).free>=10*G,'Host disk <10 GiB')
    free=r.disk_free(r.run_command(['colima','ssh','--profile','hm-project','--','sudo','-n','df','-Pk','/var/lib/docker']))
    require(free>=10*G,'VM disk <10 GiB');state['vm_disk_free_bytes']=free


def stop_temporaries(state):
    # Stop every owned process before restoring auxiliary memory limits.
    error=None
    cid=r.named(state['regression_name'])
    if cid:
        labels=json.loads(r.query('inspect','--format','{{json .Config.Labels}}',cid))
        require(labels.get('hmrec.ui-deploy')==state['run_id'],'Regression container ownership differs')
        require(r.inspect(cid)['image_id']==state['image_id'],'Regression image differs')
        if r.inspect(cid)['running']:r.docker('stop','--time','10',cid,timeout=30)
        require(not r.inspect(cid)['running'],'Regression still running')
        try:r.docker('rm',cid,timeout=30)
        except Exception as exc:error=type(exc).__name__
    r.stop_temporary(state);state['temporary_stopped']=True
    if error:
        state['temporary_cleanup_complete']=False
        state['cleanup_errors'].append(dict(resource='regression_removal',error_type=error))
    save(state)


def parse_result(log,protocol):
    results=[]
    for line in log.read_text(errors='replace').splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if isinstance(value,dict) and value.get('protocol')==protocol:results.append(value)
    require(len(results)==1,'Missing or ambiguous verification result')
    return results[0]


def run_regression(state):
    stop_temporaries(state);admit(r.snapshot(),state['engine']['memory'],G//2)
    source=(ROOT/'tests/integration/test_ui_metadata.py').read_text()
    program="import json,types,unittest\nm=types.ModuleType('ui_regression')\nexec(compile("+repr(source)+",'test_ui_metadata.py','exec'),m.__dict__)\nsuite=unittest.defaultTestLoader.loadTestsFromModule(m)\nresult=unittest.TextTestRunner(verbosity=2).run(suite)\npassed=result.wasSuccessful() and result.testsRun==1 and not result.skipped\nprint(json.dumps(dict(protocol='ui-regression-v1',passed=passed,tests=result.testsRun,skipped=len(result.skipped))))\nraise SystemExit(0 if passed else 1)\n"
    r.docker('create','--name',state['regression_name'],'--label','hmrec.ui-deploy='+state['run_id'],
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--memory','512m','--memory-swap','512m','--cpus','0.5','--pids-limit','128',
        '--tmpfs','/tmp:rw,nosuid,size=128m','--user','10001:10001','--entrypoint','python',
        state['image_id'],'-B','-c',program,log=STATE/'regression-create.log')
    cid=r.named(state['regression_name']);require(bool(cid),'Regression container missing')
    state['regression_id']=cid;save(state)
    raw=full_inspect(cid);limits=raw['HostConfig']
    require(limits['Memory']==G//2 and limits['MemorySwap']==G//2 and limits['NanoCpus']==500000000
            and limits['NetworkMode']=='none' and limits['ReadonlyRootfs'] and not limits['PortBindings'],
            'Regression isolation differs')
    state['regression_limits']={key:limits[key] for key in ('Memory','MemorySwap','NanoCpus','NetworkMode','ReadonlyRootfs','PortBindings')};save(state)
    r.docker('start',cid);r.docker('wait',cid,log=STATE/'regression-wait.log',timeout=180)
    r.docker('logs',cid,log=STATE/'regression.log')
    require(r.query('inspect','--format','{{.State.ExitCode}}',cid)=='0','UI regression failed')
    value=parse_result(STATE/'regression.log','ui-regression-v1')
    require(value==dict(protocol='ui-regression-v1',passed=True,tests=1,skipped=0),'Regression incomplete')
    state['regression']=value;save(state);stop_temporaries(state)


def prepare_overrides(state,values):
    require(resolved(values)==state['base_config'],'Base Compose configuration changed')
    for mode,image in [('candidate',state['candidate_image']),('rollback',state['historical_image'])]:
        path=STATE/(mode+'.json');write_json(path,{'services':{'ui':{'image':image}}})
        control.verify_override(state['base_config'],resolved(values,path),image)
    state['overrides_ready']=True;save(state)


def verify_ui(state,values,mode):
    image=state['candidate_image'] if mode=='candidate' else state['historical_image']
    expected=TARGET_UI if mode=='candidate' else OLD_UI
    cid=ui_id(values);row=r.healthy(cid)
    require(row['image_id']==image and row['restart_count']==0,'UI image or restart count differs')
    require(control.ui_contract(full_inspect(cid),state['environment_keys'])==state['ui_contract'],'UI runtime contract changed')
    require(r.query('exec',cid,'sha256sum','/opt/hmrec/src/hm_recommender/ui/app.py').split()[0]==expected,'Deployed UI source differs')
    client=HTTPConnection('127.0.0.1',int(values.get('UI_PORT','8501')),timeout=10)
    try:
        client.request('GET','/_stcore/health');response=client.getresponse()
        require(response.status==200 and response.read(1024).strip()==b'ok','Host UI health failed')
    finally:client.close()
    state['ui_http_health']=True
    # AppTest renders the installed file against the real API without clicking
    # buttons. It makes only GETs and read-only recommendation requests.
    log=STATE/('live-render-'+mode+'.log')
    r.docker('exec',cid,'python','-B','-c',LIVE_RENDER_PROGRAM,log=log,timeout=180)
    result=parse_result(log,'ui-live-render-v1')
    require(result.get('rendered') and not result.get('writes_performed'),'Live UI render failed')
    r.verify_stable({cid:row},{cid:r.inspect(cid)})
    state['ui_api_read_verified']=True;state['ui_live_render']=result;save(state)
    return cid


def apply_ui(state,values,mode):
    image=state['candidate_image'] if mode=='candidate' else state['historical_image']
    for name,digest in state['configuration_hashes'].items():require(r.sha(ROOT/name)==digest,'Configuration changed before apply')
    path=STATE/(mode+'.json')
    require(json.loads(path.read_text())=={'services':{'ui':{'image':image}}},'UI override changed')
    control.verify_override(state['base_config'],resolved(values,path),image)
    require(r.query('image','inspect','--format','{{.Id}}',image)==image,'UI image missing before apply')
    r.run_command(control.apply_command(ROOT,values,path),env=compose_env(values),log=STATE/('apply-'+mode+'.log'),timeout=180)
    cid=verify_ui(state,values,mode)
    write_json(STATE/'active-override.json',{'services':{'ui':{'image':image}}})
    return cid


@contextmanager
def recovery_signals(state):
    """Finish bounded recovery even when SSH closes during cleanup."""
    previous={sig:signal.getsignal(sig) for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM)}
    def defer(sig,_frame):
        state.setdefault('signals_during_recovery',[]).append(sig)
        r.emit('Señal recibida durante recuperación; terminando restauración acotada.')
    try:
        for sig in previous:signal.signal(sig,defer)
        yield
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)


def check_current_ui(state):
    cid=state['current_ui'];current=r.inspect(cid)
    require(current['running'] and current['health']=='healthy','Current UI unhealthy')
    r.verify_stable({cid:state['verified_ui']},{cid:current})


def rollback(state,values):
    state.update(status='failed',rollback_started=True,rollback_verified=False,deployment_verified=False,ui_outcome='rollback_pending')
    save(state)
    try:
        state['current_ui']=apply_ui(state,values,'rollback')
        state['verified_ui']=r.inspect(state['current_ui'])
        state.update(rollback_verified=True,ui_outcome='rollback');save(state)
    except BaseException as error:
        state['rollback_error_type']=type(error).__name__
        (STATE/'rollback-failure.log').write_text(traceback.format_exc());save(state)


def revalidate_or_rollback(state,values):
    if not state['deployment_started']:return
    if state['deployment_verified'] or state['rollback_verified']:
        try:check_current_ui(state);return
        except BaseException as error:
            state['error_type']=type(error).__name__;state['failure_stage']='current_ui_revalidation'
            (STATE/'ui-revalidation-failure.log').write_text(traceback.format_exc())
    rollback(state,values)


def finish(state,values):
    with recovery_signals(state):finish_protected(state,values)


def finish_protected(state,values):
    state.update(recovery_complete=False,final_joint_health_verified=False)
    stop_temporaries(state)
    if not state['maintenance_started']:
        r.verify_stable(state['before'],r.snapshot())
        require(r.ready(values)==state['ready'] and signature(state)==state['state_signature'],'Unchanged-state verification failed')
        state.update(recovery_complete=True,original_api_postgres_preserved=True,state_preserved=True,ready_preserved=True)
        save(state);return
    try:
        revalidate_or_rollback(state,values)
    finally:
        state['restoration']=restored_services(state['aux'],r.inspect,
            lambda cid:r.docker('start',cid,timeout=60),r.healthy)
        state['auxiliaries_restored']=all(row['restored'] for row in state['restoration']);save(state)
    # A candidate can fail when the auxiliaries return. Recheck the live
    # generation, not only the success flag saved before their restoration.
    if state['deployment_verified'] or state['rollback_verified']:
        revalidate_or_rollback(state,values)
    if state['deployment_started']:
        require(state['deployment_verified'] or state['rollback_verified'],'UI recovery pending')
        check_current_ui(state)
    after=r.snapshot();current=state.get('current_ui',state['original_ui'])
    temporaries=[cid for name in ('builder_container','probe_name','regression_name') if (cid:=r.named(state[name]))]
    control.verify_inventory(state['before'],after,state['aux'],state['original_ui'],current,temporaries)
    state.update(original_api_postgres_preserved=True,final_joint_health_verified=True)
    require(r.ready(values)==state['ready'],'H readiness changed');state['ready_preserved']=True
    require(signature(state)==state['state_signature'],'Application state changed');state['state_preserved']=True
    r.airflow(values);state['recovery_complete']=True;save(state)


def execute(state,values,guard):
    try:
        state['stage']='public_registry_preflight';save(state)
        r.emit('UI deploy: verificar plugin e imágenes públicas; servicios activos')
        r.prepare_public_registry(state);before_maintenance(state,values);guard.check()
        state.update(stage='stop_six_auxiliaries',maintenance_started=True);save(state)
        r.emit('UI deploy: detener los seis auxiliares; API/PostgreSQL/UI siguen activos')
        for row in state['aux']:
            current=r.inspect(row['container_id']);identity(row,current)
            require(current['running'] and current['health']=='healthy','Auxiliary changed before stop')
            r.docker('stop','--time','30',row['container_id'],timeout=60)
            require(not r.inspect(row['container_id'])['running'],'Auxiliary did not stop')
        require({cid for cid,row in r.snapshot().items() if row['running']}==set(state['kept']),'Unexpected active service')
        admit(r.snapshot(),state['engine']['memory'],2*G)
        state['stage']='candidate_build';save(state);r.emit('UI deploy: construir candidata y comprobar arranque aislado')
        r.construct(state);state['candidate_image']=state['image_id'];save(state)
        state['stage']='synthetic_ui_regression';save(state);r.emit('UI deploy: comprobar actualización de revisión 4→5 sin datos reales')
        run_regression(state);prepare_overrides(state,values);guard.check()
        require(signature(state)==state['state_signature'],'Application state changed before UI apply')
        state.update(stage='apply_ui',deployment_started=True,ui_outcome='pending');save(state)
        r.emit('UI deploy: actualizar únicamente UI; conservar API/PostgreSQL')
        state['current_ui']=apply_ui(state,values,'candidate')
        state['verified_ui']=r.inspect(state['current_ui'])
        state.update(deployment_verified=True,ui_outcome='candidate');save(state)
    except BaseException as error:
        state['error_type']=type(error).__name__;state['failure_stage']=state['stage']
        (STATE/'failure.log').write_text(traceback.format_exc());save(state)
    finally:
        r.emit('UI deploy: finalizar recuperación y verificar todos los servicios')
        try:finish(state,values)
        except BaseException as error:
            state['recovery_error_type']=type(error).__name__;state['recovery_complete']=False
            (STATE/'recovery-failure.log').write_text(traceback.format_exc())
        state['status']='passed' if (state['deployment_verified'] and state['recovery_complete']
            and state['temporary_cleanup_complete'] and 'error_type' not in state) else 'failed'
        state['stage']='complete' if state['recovery_complete'] else 'recovery_pending'
        save(state);write_json(STATE/'summary.json',control.summary(state));r.emit(json.dumps(control.summary(state),sort_keys=True))


def publish(state):
    checkout_commit(ROOT,state['procedure_commit'])
    repo=json.loads(r.run_command(['gh','repo','view','--json','nameWithOwner,isPrivate']))
    require(repo==dict(nameWithOwner='emilianodesu/clothing-recommendation',isPrivate=True),'Expected private project repository')
    destination=ROOT/'evidence/ui-deployment-mac24.json';require(not destination.exists(),'Deployment report already exists')
    write_json(destination,control.summary(state));r.run_command(['git','add',str(destination)])
    r.run_command(['git','commit','--only',str(destination),'-m','docs: report mac24 UI deployment'],log=STATE/'publish-commit.log')
    r.run_command(['git','-c','credential.helper=','-c','credential.helper=!gh auth git-credential','push','origin','HEAD:main'],log=STATE/'publish-push.log',timeout=120)
    r.emit('Resumen de despliegue publicado en GitHub.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expected-commit',required=True)
    modes=parser.add_mutually_exclusive_group(required=True)
    for mode in ('preflight-only','deploy','restore-only'):modes.add_argument('--'+mode,action='store_true')
    parser.add_argument('--publish',action='store_true');args=parser.parse_args()
    os.umask(0o077);os.environ.update(SSL_CERT_FILE='/etc/ssl/cert.pem',DOCKER_CONTEXT=r.CONTEXT,GIT_TERMINAL_PROMPT='0')
    # Reuse reviewed helpers with a new source/state; never execute recovery.main.
    r.ROOT=ROOT;r.STATE=STATE;r.HISTORY=args.expected_commit;r.UI_SHA=TARGET_UI
    values=read_configuration(ROOT/'.env');art=Path(values['ARTIFACTS_DIR'])
    if not art.is_absolute():art=ROOT/art
    def interrupted(*_):raise KeyboardInterrupt('Signal received')
    for sig in (signal.SIGHUP,signal.SIGTERM):signal.signal(sig,interrupted)
    with (art/'.heavy-launch.lock').open('a') as lock,AwakeGuard() as guard:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.restore_only:
            state=json.loads((STATE/'state.json').read_text())
            require(state['procedure_commit']==args.expected_commit,'Recovery commit differs')
            require(not state['recovery_complete'],'Recovery already completed; do not repeat')
            for name,digest in state['runner_files'].items():require(r.sha(ROOT/name)==digest,'Recovery code changed')
            try:finish(state,values)
            except BaseException as error:
                state['recovery_error_type']=type(error).__name__;state['recovery_complete']=False
                (STATE/'recovery-failure.log').write_text(traceback.format_exc())
            # A recovery does not erase the failed deployment result.
            state['status']='failed';state['stage']='complete' if state['recovery_complete'] else 'recovery_pending';save(state)
            write_json(STATE/'summary.json',control.summary(state));r.emit(json.dumps(control.summary(state),sort_keys=True))
            if args.publish and state['recovery_complete']:publish(state)
            return 0 if state['recovery_complete'] else 1
        r.emit('UI deploy: comprobaciones previas; sin pausas ni cambios')
        ctx=preflight(args.expected_commit,values)
        if args.preflight_only:r.emit('Preflight OK; sin reserva, construcción ni despliegue.');return 0
        STATE.mkdir(mode=0o700);rid='ui-deploy-'+uuid.uuid4().hex[:12]
        state=dict(ctx,protocol='ui-deploy-v1',run_id=rid,procedure_commit=args.expected_commit,created_at=now(),
            public_followup=True,builder=rid,builder_container='buildx_buildkit_'+rid+'0',probe_name=rid+'-probe',
            regression_name=rid+'-regression',image_tag='hmrec-ui:'+rid,status='failed',stage='reserved',
            image_verified=False,build_started=False,probe_started=False,maintenance_started=False,
            deployment_started=False,deployment_verified=False,rollback_started=False,rollback_verified=False,
            recovery_complete=False,ui_outcome='unchanged',visible_browser_verified=False,
            runner_files={str(path.relative_to(ROOT)):r.sha(path) for path in (ROOT/'scripts').glob('*.py')})
        save(state);r.emit('Logs privados: '+str(STATE));execute(state,values,guard)
        if args.publish and state['recovery_complete']:publish(state)
        elif not state['recovery_complete']:r.emit('Recuperación pendiente: usar sólo --restore-only con el mismo SHA; no repetir --deploy.')
        return 0 if state['status']=='passed' else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        r.emit('DETENIDO: '+type(error).__name__+(': '+str(error) if isinstance(error,ValueError) else '')+'. Conservar estado y logs.')
        sys.exit(1)
