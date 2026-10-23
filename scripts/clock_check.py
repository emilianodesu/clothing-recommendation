"""Collect a read-only T5 clock follow-up; no Docker, credentials or job replay."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
import uuid
from events_clock import analyze_timing, collect_power, clock_metadata, require
from clock_handoff import PROTOCOL, complete
from handoff import read_report, package_report, write_json, now, MAX_TOTAL

ROOT = Path(__file__).resolve().parents[1]


def checkout_commit(root, expected):
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
    dirty = subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
    require(re.fullmatch('[a-f0-9]{40}',expected) and commit == expected and not dirty)
    return commit


def runner_digest(root):
    digest = hashlib.sha256()
    for path in sorted((root/'scripts').rglob('*.py')):
        digest.update(str(path.relative_to(root/'scripts')).encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def run(root, machine_id, expected_commit, source_report):
    root, source_report = Path(root), Path(source_report)
    require(re.fullmatch('[a-z][a-z0-9_-]{0,31}',machine_id))
    commit = checkout_commit(root, expected_commit)
    require(source_report.is_file() and not source_report.is_symlink() and source_report.stat().st_size <= MAX_TOTAL)
    digest = hashlib.sha256(source_report.read_bytes()).hexdigest()
    source = read_report(source_report)
    value = source['result']
    require(source['machine_id'] == machine_id and value['protocol'] == 'events-memory-v1')
    require(source['status'] in ('passed','failed') and value['diagnostics']['complete'])
    timing = analyze_timing(source['started_at'],source['finished_at'],value['duration_seconds'],value['diagnostics']['snapshots'])
    run_id = 'events-'+machine_id+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    folder = root/'artifacts/handoff'/run_id
    folder.mkdir(parents=True,mode=0o700)
    report = dict(schema_version=1,kind='events-handoff',run_id=run_id,machine_id=machine_id,context='host-read-only',
                  source_commit=commit,source_clean=True,started_at=now(),finished_at=None,status='failed',warnings=[],steps=[])
    host = clock_metadata()
    power = collect_power(timing['started_at'],timing['finished_at'])
    require(hashlib.sha256(source_report.read_bytes()).hexdigest() == digest)
    status = 'passed' if complete(power) else 'failed'
    result = dict(stage='T5',protocol=PROTOCOL,status=status,training_performed=False,test_evaluated=False,t5_closed=False,
        clock_diagnostic=dict(source=dict(run_id=source['run_id'],machine_id=machine_id,source_commit=source['source_commit'],
            archive_sha256=digest,package_id=source['package_id'],status=source['status'],protocol=value['protocol']),
            host=host,timing=timing,power=power,complete=complete(power)))
    env = dict(run_id=run_id,source_commit=commit,runner_tree_sha256=runner_digest(root))
    report.update(finished_at=now(),status=status,result=result,
        steps=[dict(name='clock-followup',log='job.log',status=status,exit_code=0 if status=='passed' else 1)])
    (folder/'commit.txt').write_text(commit+'\n')
    (folder/'job.log').write_text('read-only clock follow-up\ncollection: '+power['collection_status']+'\n')
    write_json(folder/'result.json',result);write_json(folder/'environment.json',env)
    archive = package_report(folder,report)
    print('Reporte cerrado: '+str(archive))
    print('Estado de recolección: '+status+'. No acredita cierre T5 ni causalidad.')
    return archive


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine-id',required=True)
    parser.add_argument('--expected-commit',required=True)
    parser.add_argument('--source-report',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        path=run(ROOT,args.machine_id,args.expected_commit,args.source_report)
        return 0 if read_report(path)['status']=='passed' else 1
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        print('No se completó el reporte: '+type(error).__name__+'. Conservar fuente y cualquier carpeta incompleta.')
        return 2


if __name__ == '__main__':raise SystemExit(main())
