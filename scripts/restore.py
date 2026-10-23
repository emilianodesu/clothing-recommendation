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


def prepare_destination(source,destination,port):
    source=Path(source).resolve();destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise ValueError('Destination must not exist')
    if not destination.is_absolute() or not 1024<=int(port)<=65535:raise ValueError('Absolute destination and valid port required')
    names=subprocess.check_output(['git','ls-files','-z'],cwd=source).decode().split('\0')
    destination.mkdir(parents=True,mode=0o700)
    for name in filter(None,names):
        if name=='.env' or name.startswith(('.runtime/','artifacts/','data/','backups/','.git/')):continue
        src=source/name
        if src.is_symlink() or not src.is_file():raise ValueError('Only tracked regular source files are copied')
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
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
    if ready.get('status')!='ready':raise ValueError('Restored API is not ready')
    result=dict(restored,project=v['COMPOSE_PROJECT_NAME'],ready=ready,automatic_scheduling=False)
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
