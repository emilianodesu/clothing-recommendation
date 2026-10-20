#!/usr/bin/env python3
"""Run fixed Q1–Q3 and exchange closed aggregate reports; no test/T4."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile
from data_job import capture,export,fingerprint,inspect_run,start,validate_run_id
from handoff import read_report,history_complete


def reference_for(artifacts,run_id):
    validate_run_id(run_id)
    if not run_id.startswith('diagnose-'):raise ValueError('Reference must be a recent-history diagnostic')
    archive=next((artifacts/folder/(run_id+'.zip') for folder in ('handoff','incoming') if (artifacts/folder/(run_id+'.zip')).is_file()),None)
    if archive is None:raise ValueError('Missing closed reference ZIP; download it from the private Release')
    report=read_report(archive)
    if report['run_id']!=run_id or report['kind']!='diagnostic-handoff' or report['status']!='passed' or not history_complete(report['result']):
        raise ValueError('Reference must be a successful recent-history report')
    with ZipFile(archive) as bundle:environment=json.loads(bundle.read('environment.json'))
    if environment['diagnostic_source']['config']['demo']:raise ValueError('Full V3 requires the original full H&M reference')
    return {'run_id':run_id,'archive_sha256':fingerprint(archive),'path':'/artifacts/'+str(archive.relative_to(artifacts))}



def graph_reference_for(artifacts,run_id,fold,size):
    validate_run_id(run_id)
    if not run_id.startswith('quality-'):raise ValueError('Q2 requires a Q1 quality reference')
    archive=next((artifacts/folder/(run_id+'.zip') for folder in ('handoff','incoming') if (artifacts/folder/(run_id+'.zip')).is_file()),None)
    if archive is None:raise ValueError('Missing closed Q1 reference ZIP; download from private Release')
    report=read_report(archive);result=report.get('result',{})
    if (report['run_id']!=run_id or report['kind']!='quality-handoff' or report['status']!='passed'
        or result.get('stage')!='Q1' or result.get('size')!=size or result.get('fold',{}).get('fold_id')!=fold):
        raise ValueError('Q1 reference fold/size/stage mismatch')
    work=artifacts/'data-jobs'/run_id/'work'
    for name in ('snapshot','ranking-P','ranking-H','evaluation-P','evaluation-H'):
        if not (work/name/'manifest.json').is_file():raise ValueError('Missing private Q1 artifacts; ZIP alone is insufficient')
    return {'run_id':run_id,'archive_sha256':fingerprint(archive),'path':'/artifacts/'+str(archive.relative_to(artifacts)),
        'work_path':'/artifacts/'+str(work.relative_to(artifacts))}


def ranker_reference_for(artifacts,run_id,fold,size):
    validate_run_id(run_id)
    archive=next((artifacts/folder/(run_id+'.zip') for folder in ('handoff','incoming')
                  if (artifacts/folder/(run_id+'.zip')).is_file()),None)
    if archive is None:raise ValueError('Missing closed Q2 reference ZIP')
    report=read_report(archive);result=report.get('result',{})
    if (report['run_id']!=run_id or report['kind']!='quality-handoff' or report['status']!='passed'
        or result.get('stage')!='Q2' or result.get('size')!=size or result.get('fold',{}).get('fold_id')!=fold):
        raise ValueError('Q3 requires a matching successful Q2 reference')
    baseline=graph_reference_for(artifacts,result['reference']['run_id'],fold,size)
    if baseline['archive_sha256']!=result['reference']['archive_sha256']:
        raise ValueError('Q2 ancestry differs from closed Q1 archive')
    work=artifacts/'data-jobs'/run_id/'work'
    for name in ('graph','ranking-G','evaluation-G'):
        if not (work/name/'manifest.json').is_file():raise ValueError('Missing private Q2 artifacts; ZIP alone is insufficient')
    return {'run_id':run_id,'archive_sha256':fingerprint(archive),'path':'/artifacts/'+str(archive.relative_to(artifacts)),
        'work_path':'/artifacts/'+str(work.relative_to(artifacts)),'baseline_reference':baseline}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='action',required=True)
    launch=commands.add_parser('start')
    launch.add_argument('--stage',default='baselines');launch.add_argument('--config',default='quality-v1')
    launch.add_argument('--fold',choices=('V1','V2','V3'),required=True);launch.add_argument('--size',choices=('demo','pilot','full'),required=True)
    launch.add_argument('--machine-id',default='mac24');launch.add_argument('--profile',default='hm-project');launch.add_argument('--resources',default='mac24')
    launch.add_argument('--expected-commit',required=True);launch.add_argument('--reference-run-id')
    launch.add_argument('--pool',choices=('H','G'))
    for name in ('status','export','stop'):
        command=commands.add_parser(name);command.add_argument('--run-id',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='start':
            if args.stage not in ('baselines','graph','ranker') or args.config!='quality-v1':raise ValueError('Only Q1–Q3 / quality-v1 implemented')
            if (args.stage=='ranker')!=bool(args.pool):raise ValueError('Only Q3 requires --pool H|G')
            if (args.stage in ('graph','ranker') or (args.fold=='V3' and args.size=='full'))!=bool(args.reference_run_id):raise ValueError('Q2 requires Q1; Q3 requires Q2; Q1 only full V3 needs its diagnostic reference')
            options={'stage':args.stage,'fold':args.fold,'size':args.size,'reference_run_id':args.reference_run_id}
            if args.pool:options['pool']=args.pool
            start(args.machine_id,args.profile,'synthetic' if args.size=='demo' else 'hm',args.expected_commit,args.resources,
                quality_options=options)
        else:
            if not args.run_id.startswith('quality-'):raise ValueError('Quality run_id required')
            directory,run,env,state,result,status=inspect_run(args.run_id)
            if args.action=='export':print('Paquete para GitHub: '+str(export(args.run_id)))
            elif args.action=='stop':
                if status!='running' or state.get('Status')=='preparing':raise ValueError('No active quality worker')
                capture(env,'docker','stop','--time','10',run['container_id']);print('Stopped; export this attempt before retrying.')
            else:
                print(json.dumps({'run_id':args.run_id,'status':status,'exit_code':state.get('ExitCode'),'oom_killed':state.get('OOMKilled',False),
                    'limits':run.get('effective_limits'),'budget':run.get('quality_budget'),'result':result},indent=2))
                return 0 if status in ('running','passed') else 1
        return 0
    except KeyboardInterrupt:return 130
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('ERROR: '+str(error),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
