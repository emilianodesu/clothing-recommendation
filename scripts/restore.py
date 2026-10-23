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
    destination.mkdir(parents=True,mode=0o700)
    for name in files:
        if name=='.env' or name.startswith(('.runtime/','artifacts/','data/','backups/','.git/')):continue
        src=source/name
        if src.is_symlink() or not src.is_file():raise ValueError('Only tracked regular source files are copied')
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
    (destination/'source-inventory.json').write_text(json.dumps({'files':files},sort_keys=True))
    values=prepare_configuration(destination)
    values['API_PORT']=str(port)
    with (destination/'.env').open('w') as f:
        for key,value in values.items():f.write(key+'='+value+'\n')
    return values


def restore_into(source,archive,destination,checksum,port):
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
    images=verify_images(source)
    v=prepare_destination(source,destination,port)
    incoming=destination/'artifacts'/'restore-input.zip';shutil.copyfile(archive,incoming);os.chmod(incoming,0o600)
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
    result=dict(restored,images=images,parity_queries=checked,project=v['COMPOSE_PROJECT_NAME'],ready=ready,automatic_scheduling=False)
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
