"""Isolated synthetic acceptance. Never mounts host data or uses Compose services."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

PG='postgres:17.11-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826'
TESTS=['tests/integration/'+n+'.py' for n in ('test_recommendations','test_release_activation','test_serving_http','test_events','test_demo','test_ui','test_ui_metadata','test_pipeline','test_release_transfer','test_backup_restore')]


def admit(running,memory,cpus):
    if running.strip():raise ValueError('Acceptance requires an idle dedicated Docker engine; no services were stopped.')
    if memory<7*2**30 or cpus<2:raise ValueError('Acceptance requires at least 7 GiB effective memory and 2 CPUs.')


def summarize_junit(xml):
    root=ET.fromstring(xml)
    suites=[root] if root.tag=='testsuite' else list(root.iter('testsuite'))
    result={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    if not result['tests'] or any(result[k] for k in ('failures','errors','skipped')):
        raise ValueError('Acceptance requires executed tests, zero failures/errors/skips.')
    return result


def test_command(name,network,image):
    return ['docker','create','--name',name,'--network',network,'--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--memory','5g','--memory-swap','5g','--cpus','2',
        '--pids-limit','256','--tmpfs','/tmp:rw,size=2147483648,mode=1777',
        '-e','HM_APP_PASSWORD','-e','POSTGRES_HOST=postgres','-e','HMREC_SERVING_TESTS=1',
        '-e','POLARS_MAX_THREADS=2','-e','OMP_NUM_THREADS=2','-e','MKL_NUM_THREADS=2',
        image,'python','-c',
        "import pytest,sys,pathlib,base64;code=pytest.main(sys.argv[1:]);p=pathlib.Path('/tmp/acceptance.xml');print('ACCEPTANCE_XML:'+base64.b64encode(p.read_bytes()).decode() if p.exists() else 'ACCEPTANCE_XML_MISSING');sys.exit(code)",
        *TESTS,'-q','-p','no:cacheprovider','--junitxml=/tmp/acceptance.xml']


def run_logged(cmd,log):
    try:
        result=subprocess.run(cmd,text=True,capture_output=True,timeout=600)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
        log.write_text(decoded(error.stdout)+decoded(error.stderr))
        raise
    log.write_text(result.stdout+result.stderr)
    return result


def run(root):
    root=Path(root)
    def call(cmd,**kw):
        return subprocess.run(cmd,text=True,capture_output=True,check=True,timeout=kw.pop('timeout',60),**kw).stdout.strip()
    info=json.loads(call(['docker','info','--format','{{json .}}']))
    admit(call(['docker','ps','-q']),info['MemTotal'],info['NCPU'])
    image=call(['docker','image','inspect','hmrec-jobs:0.1.0','--format','{{.Id}}'])
    pgimage=call(['docker','image','inspect',PG,'--format','{{.Id}}'])
    # Installed scientific sources plus all tests/migrations must match the checkout.
    code="""import hashlib,pathlib,json
from hm_recommender.api.runtime import source_digest
h=hashlib.sha256()
for folder in ('tests','migrations'):
 for p in sorted(pathlib.Path(folder).rglob('*')):
  if p.is_file() and p.suffix in ('.py','.sql'):h.update(str(p).encode()+b'\\0'+p.read_bytes()+b'\\0')
print(json.dumps([source_digest(),h.hexdigest()]))"""
    source=hashlib.sha256();base=root/'src/hm_recommender'
    for p in sorted(base.rglob('*.py')):source.update(str(p.relative_to(base)).encode()+b'\0'+p.read_bytes()+b'\0')
    fixtures=hashlib.sha256()
    for folder in ('tests','migrations'):
        for p in sorted((root/folder).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.sql'):fixtures.update(str(p.relative_to(root)).encode()+b'\0'+p.read_bytes()+b'\0')
    actual=json.loads(call(['docker','run','--rm','--network','none','--read-only','--memory','512m','--cpus','1',image,'python','-c',code]))
    if actual!=[source.hexdigest(),fixtures.hexdigest()]:raise ValueError('Build jobs from this checkout before acceptance; image sources/tests differ.')
    name='hmrec-acceptance-'+uuid.uuid4().hex
    folder=root/'.runtime'/name;folder.mkdir(parents=True,mode=0o700)
    env=dict(os.environ,HM_APP_PASSWORD=secrets.token_hex(24))
    env['POSTGRES_PASSWORD']=env['HM_APP_PASSWORD']
    report=dict(protocol='synthetic-acceptance-v1',status='failed',source_commit=call(['git','rev-parse','HEAD']),
        source_clean=not bool(call(['git','status','--porcelain'])),image_id=image,postgres_image_id=pgimage,
        source_sha256=actual[0],fixtures_sha256=actual[1],test_files=TESTS,tests=None,cleanup_ok=False,
        real_data_mounted=False,test_evaluated=False,training_performed=False)
    created=[];network=False;started=time.monotonic()
    try:
        call(['docker','network','create','--internal',name]);network=True
        created.append(name+'-pg')
        pg=call(['docker','create','--name',name+'-pg','--network',name,'--network-alias','postgres',
            '--memory','512m','--memory-swap','512m','--cpus','1','--tmpfs','/var/lib/postgresql/data:rw,size=536870912',
            '-e','POSTGRES_USER=hm_app','-e','POSTGRES_DB=hm_app','-e','POSTGRES_PASSWORD',pgimage],env=env)
        call(['docker','start',pg])
        for _ in range(60):
            ready=subprocess.run(['docker','exec',pg,'pg_isready','-U','hm_app','-d','hm_app'],capture_output=True,timeout=10)
            if ready.returncode==0:break
            time.sleep(1)
        else:raise ValueError('Temporary PostgreSQL not ready')
        created.append(name+'-tests')
        cid=call(test_command(name+'-tests',name,image),env=env)
        result=run_logged(['docker','start','-a',cid],folder/'tests-private.log')
        payloads=[line.removeprefix('ACCEPTANCE_XML:') for line in result.stdout.splitlines() if line.startswith('ACCEPTANCE_XML:')]
        if len(payloads)!=1:raise ValueError('Missing unique test receipt')
        (folder/'acceptance.xml').write_bytes(base64.b64decode(payloads[0],validate=True))
        if result.returncode:raise ValueError('Synthetic tests failed; inspect private log.')
        report['tests']=summarize_junit((folder/'acceptance.xml').read_text())
        report['status']='passed'
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        report['error_type']=type(error).__name__
    finally:
        cleanup=True
        for cid in reversed(created):
            try:
                present=call(['docker','ps','-aq','--filter','name=^/'+cid+'$'])
                if present:call(['docker','rm','-f',present])
            except (OSError,subprocess.SubprocessError):cleanup=False
        if network:
            try:call(['docker','network','rm',name])
            except (OSError,subprocess.SubprocessError):cleanup=False
        report['cleanup_ok']=cleanup
        if not cleanup:report['status']='failed'
        report['duration_seconds']=time.monotonic()-started
        (folder/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Acceptance report: '+str(folder/'report.json'))
    print(json.dumps(report))
    return 0 if report['status']=='passed' else 1
