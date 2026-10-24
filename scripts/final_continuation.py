"""One bounded continuation of the audited reservation whose worker never started."""
import hashlib
import json
from pathlib import Path
from final_control import reserve,verify_startup_recovery,unstarted_preparation
from final_handoff import require,WINDOW
from handoff import read_report
from concurrency_transition import inspect,verify
from mac24_terminal import command

STARTUP_RUN='final-mac24-20261024T062636Z-a62a3010'
STARTUP_SHA='0f9ebe74b916eb51a7ae82cde4bc847b83a610236093d64489b7f48c346e6493'
PREP_RUN='final-mac24-20261024T061640Z-94b97146'
PREP_SHA='127d43d24b2117f050ded59e36ed0953a51258a5c4117db8907b8535b6e05329'
WORKER_SOURCE='fc2ab313ffaa4f708c62f54bd322eeae9d8a41d1dba585a27cb661242a9902d1'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def unused_worker(private,saved,current):
    require(current['running'] is False and current['image_id']==saved['image_id'])
    for name in ('go.json','complete.json','evaluation','evaluation-result.json'):
        p=private/name;require(not p.exists() and not p.is_symlink())


def reserve_continuation(art,parent_sha,value):
    require(digest(art/'.final-test-once.json')==parent_sha)
    reserve(art/'.final-test-continuation.json',dict(value,reservation_parent_sha256=parent_sha))


def check(root,ctx):
    art=ctx['art']
    require(not (art/'.final-test-continuation.json').exists())
    expected={PREP_RUN,STARTUP_RUN}
    require({p.name for p in (art/'handoff').glob('final-mac24-*')}==expected|{x+'.zip' for x in expected})
    require({p.name for p in (art/'final-private').glob('final-mac24-*')}==expected)
    reports={}
    for rid,sha in ((PREP_RUN,PREP_SHA),(STARTUP_RUN,STARTUP_SHA)):
        path=art/'handoff'/(rid+'.zip');require(digest(path)==sha)
        reports[rid]=read_report(path)
        require(reports[rid]['status']=='failed' and reports[rid]['result']['test_started'] is False and reports[rid]['result']['evaluation'] is None)
    unstarted_preparation(art/'final-private'/PREP_RUN)
    state=root/'.runtime/t8-final-preparation-followup'
    saved=json.loads((state/'state.json').read_text())
    parent=art/'.final-test-once.json';reservation=json.loads(parent.read_text())
    verify_startup_recovery(saved,reservation,reports[STARTUP_RUN])
    require(saved['aux']==ctx['aux'] and saved['worker_name']=='hmrec-'+STARTUP_RUN)
    require(reservation['protocol']=='final-hp-v1' and reservation['selected_method']=='H' and reservation['window']==WINDOW)
    require(reservation['limits']==dict(memory_bytes=5*2**30,cpus=2,threads=2,max_seconds=5400))
    for key in ('source_sha256','dataset_manifest_sha256'):
        require(reservation[key]==ctx['selection'][key])
    require(reservation['worker_source_sha256']==WORKER_SOURCE)
    private=art/'final-private'/STARTUP_RUN
    require(private.is_dir() and not private.is_symlink() and Path(saved['private']).resolve()==private.resolve())
    ids=command(['docker','ps','-a','--no-trunc','--filter','name=^/'+saved['worker_name']+'$','--format','{{.ID}}']).splitlines()
    require(len(ids)==1)
    unused_worker(private,saved,inspect(ids[0]))
    recovered=json.loads((state/'recovery.json').read_text())
    require(len(recovered)==6 and {r['container_id'] for r in recovered}=={r['container_id'] for r in ctx['aux']})
    for row in ctx['aux']:
        receipt=next(r for r in recovered if r['container_id']==row['container_id']);verify(row,receipt)
        current=inspect(row['container_id']);verify(row,current)
        require(receipt['running'] is True and receipt['health']=='healthy' and current['running'] is True and current['health']=='healthy')
    return dict(reservation_parent_sha256=digest(parent),startup_archive_sha256=STARTUP_SHA)
