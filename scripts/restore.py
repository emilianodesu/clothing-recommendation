#!/usr/bin/env python3
"""Restore a trusted private backup into a NEW local installation. No source mutation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from bootstrap import prepare_configuration,project_name
from project import compose


def checksum_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while True:
            b=f.read(1024*1024)
            if not b:break
            h.update(b)
    return h.hexdigest()


def copy_verified(source,target,expected):
    shutil.copyfile(source,target);os.chmod(target,0o600)
    if checksum_file(target)!=expected:raise ValueError('Copied backup differs from authorized SHA-256')


def source_inventory(source):
    saved=source/'source-inventory.json'
    if saved.is_file():
        files=json.loads(saved.read_text())['files']
        for name,digest in files.items():
            p=Path(name)
            if p.is_absolute() or '..' in p.parts or p.parts[0] in ('.git','.env','artifacts','data','backups','.runtime'):
                raise ValueError('Unsafe source inventory')
            if (source/p).is_symlink() or checksum_file(source/p)!=digest:raise ValueError('Source inventory changed')
        return files
    names=subprocess.check_output(['git','ls-files','-z'],cwd=source).decode().split('\0')
    return {name:checksum_file(source/name) for name in filter(None,names)}


def verify_images(source):
    h=hashlib.sha256();base=Path(source)/'src/hm_recommender'
    for p in sorted(base.rglob('*.py')):h.update(str(p.relative_to(base)).encode()+b'\0'+p.read_bytes()+b'\0')
    expected=h.hexdigest();identities={}
    for image in ('hmrec-jobs:0.1.0','hmrec-app:0.1.0'):
        identity=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
        actual=subprocess.check_output(['docker','run','--rm','--network','none','--entrypoint','/opt/hmrec/.venv/bin/python',identity,'-c',
            'from hm_recommender.api.runtime import source_digest;print(source_digest())'],text=True).strip()
        if actual!=expected:raise ValueError('Cached image code differs; build the current checkout first')
        identities[image]=identity
    return identities


def prepare_destination(source,destination,port):
    source=Path(source).resolve();destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise ValueError('Destination must not exist')
    if not destination.is_absolute() or not 1024<=int(port)<=65535:raise ValueError('Absolute destination and valid port required')
    files=source_inventory(source)
    destination.mkdir(parents=True,mode=0o711)
    for name in files:
        if name=='.env' or name.startswith(('.runtime/','artifacts/','data/','backups/','.git/')):continue
        src=source/name
        if src.is_symlink() or not src.is_file():raise ValueError('Only tracked regular source files are copied')
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
        if src.stat().st_mode & 0o111:target.chmod(0o755)
    (destination/'source-inventory.json').write_text(json.dumps({'files':files},sort_keys=True))
    values=prepare_configuration(destination)
    values['API_PORT']=str(port)
    with (destination/'.env').open('w') as f:
        for key,value in values.items():f.write(key+'='+value+'\n')
    return values


def reserve_networks(values):
    # Explicit /24s avoid exhausting Docker's default /16 address pools across rehearsals.
    import ipaddress
    ids=subprocess.check_output(['docker','network','ls','-q'],text=True).split()
    networks=json.loads(subprocess.check_output(['docker','network','inspect',*ids],text=True)) if ids else []
    used=[ipaddress.ip_network(c['Subnet']) for n in networks for c in ((n.get('IPAM') or {}).get('Config') or []) if c.get('Subnet')]
    chosen={}
    for role in ('backend','api-publication'):
        candidate=next((ipaddress.ip_network('10.253.'+str(i)+'.0/24') for i in range(1,255)
            if not any(ipaddress.ip_network('10.253.'+str(i)+'.0/24').overlaps(n) for n in used if n.version==4)),None)
        if candidate is None:raise ValueError('No free recovery subnet; inspect Docker networks')
        command=['docker','network','create','--subnet',str(candidate),'--label','com.docker.compose.project='+values['COMPOSE_PROJECT_NAME'],
                 '--label','com.docker.compose.network='+role]
        if role=='backend':command+=['--internal']
        command += [values['COMPOSE_PROJECT_NAME']+'_'+role]
        subprocess.run(command,check=True,capture_output=True)
        used.append(candidate);chosen[role]=str(candidate)
    return chosen


def restore_into(source,archive,destination,checksum,port,*,pin_images=False):
    source=Path(source).resolve();archive=Path(archive).resolve();destination=Path(destination)
    h=hashlib.sha256()
    with archive.open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    if h.hexdigest()!=checksum:raise ValueError('Backup SHA-256 mismatch')
    # Fail before provisioning if volumes or containers already belong to the target identity.
    name=project_name(destination)
    for command in (['docker','volume','ls','--filter','label=com.docker.compose.project='+name,'-q'],
                    ['docker','ps','-a','--filter','label=com.docker.compose.project='+name,'-q']):
        if subprocess.check_output(command,text=True).strip():raise ValueError('Destination already owns Docker resources')
    if archive.stat().st_size>20*2**30:raise ValueError('Backup exceeds supported size')
    if shutil.disk_usage(destination.parent).free<3*archive.stat().st_size+2**30:raise ValueError('Insufficient destination disk space')
    images=verify_images(source)
    v=prepare_destination(source,destination,port)
    if pin_images:
        from server_support import write_private
        write_private(destination/'.runtime/server-images.json', {'services': {
            service: {'image': images['hmrec-app:0.1.0' if service in ('api','ui','app-check') else 'hmrec-jobs:0.1.0']}
            for service in ('api','ui','app-check','jobs','events-job','serving-probe')}})
    networks=reserve_networks(v)
    (destination/'artifacts/restore-networks.json').write_text(json.dumps(networks))
    incoming=destination/'artifacts'/'restore-input.zip';copy_verified(archive,incoming,checksum)
    def call(*args):return compose(destination,v,*args,capture=True,timeout=2100)
    # No build/pull: provision caches explicitly beforehand, credentials freshly generated.
    call('up','-d','--pull','never','--no-build','--wait','postgres')
    call('run','--rm','--no-deps','-T','--pull','never','jobs','hmrec','db','migrate')
    restored=json.loads(call('run','--rm','--no-deps','-T','--pull','never','jobs','hmrec','portability','restore','/artifacts/restore-input.zip','--registry','/artifacts/serving'))
    call('up','-d','--no-deps','--pull','never','--no-build','--wait','api')
    # Localhost health and current code must match before success is returned.
    import urllib.request
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:'+str(port)+'/health/ready',timeout=10) as response:ready=json.load(response)
    import zipfile
    with zipfile.ZipFile(incoming) as z:
        if z.getinfo('backup.json').file_size>1024*1024:raise ValueError('Oversized validation manifest')
        manifest=json.loads(z.read('backup.json'))
    if ready.get('status')!='ready' or ready.get('release_id')!=manifest['state']['pointer'][0][0]:raise ValueError('Restored API identity differs')
    with opener.open('http://127.0.0.1:'+str(port)+'/health/live',timeout=10) as response:live=json.load(response)
    expected=hashlib.sha256()
    base=source/'src/hm_recommender'
    for p in sorted(base.rglob('*.py')):expected.update(str(p.relative_to(base)).encode()+b'\0'+p.read_bytes()+b'\0')
    if live['source_tree_sha256']!=expected.hexdigest():raise ValueError('Restored API code differs')
    checked=0
    for case in manifest['parity']:
        req=urllib.request.Request('http://127.0.0.1:'+str(port)+'/recommend',data=json.dumps(case['request']).encode(),headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=30) as response:actual=json.load(response)
        actual.pop('request_id',None)
        if actual!=case['response']:raise ValueError('Restored recommendation differs')
        checked+=1
    for image,identity in images.items():
        if subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()!=identity:raise ValueError('Image tag changed during restore')
    result=dict(restored,networks=networks,images=images,parity_queries=checked,project=v['COMPOSE_PROJECT_NAME'],ready=ready,automatic_scheduling=False)
    (destination/'artifacts'/'restore-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--sha256',required=True);p.add_argument('--port',type=int,required=True)
    a=p.parse_args(argv)
    restore_into(Path(__file__).resolve().parents[1],a.archive,a.destination,a.sha256,a.port)
    return 0
if __name__=='__main__':raise SystemExit(main())


def offline_check(destination):
    """Temporarily remove only the destination's external network; keep DB internal."""
    from bootstrap import read_configuration
    v=read_configuration(Path(destination)/'.env')
    cid=compose(destination,v,'ps','-q','api',capture=True).strip()
    network=v['COMPOSE_PROJECT_NAME']+'_api-publication'
    backend=v['COMPOSE_PROJECT_NAME']+'_backend'
    if subprocess.check_output(['docker','network','inspect',backend,'--format','{{.Internal}}'],text=True).strip()!='true':raise ValueError('Backend must be internal')
    with __import__('zipfile').ZipFile(Path(destination)/'artifacts/restore-input.zip') as z:cases=json.loads(z.read('backup.json'))['parity']
    program="""import json,sys,urllib.request
cases=json.loads(sys.argv[1]);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8000/health/ready') as r: assert json.load(r)['status']=='ready'
for case in cases:
 req=urllib.request.Request('http://127.0.0.1:8000/recommend',data=json.dumps(case['request']).encode(),headers={'Content-Type':'application/json'})
 with opener.open(req) as r: actual=json.load(r)
 actual.pop('request_id',None)
 assert actual==case['response']
print(len(cases))
"""
    subprocess.run(['docker','network','disconnect',network,cid],check=True,capture_output=True)
    try:
        networks=json.loads(subprocess.check_output(['docker','inspect',cid,'--format','{{json .NetworkSettings.Networks}}'],text=True))
        if set(networks)!={backend}:raise ValueError('Unexpected network attached to restored API')
        result=subprocess.check_output(['docker','exec',cid,'python','-c',program,json.dumps(cases)],text=True,stderr=subprocess.DEVNULL,timeout=120)
        if int(result)!=len(cases):raise ValueError('Offline parity incomplete')
        return len(cases)
    finally:subprocess.run(['docker','network','connect',network,cid],check=True,capture_output=True)
