#!/usr/bin/env python3
"""Build and verify H locally; exchange only closed T4-A reports."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from data_job import start, inspect_run, export, fingerprint, validate_run_id
from handoff import read_report
from release_handoff import load_release_config


def references_for(artifacts,config):
    references={}
    for name in ('selection','baseline','diagnostic'):
        frozen=config[name];rid=frozen['run_id']
        archive=next((artifacts/f/(rid+'.zip') for f in ('handoff','incoming') if (artifacts/f/(rid+'.zip')).is_file()),None)
        if archive is None:raise ValueError('Missing frozen '+name+' report; download its private Release')
        if fingerprint(archive)!=frozen['archive_sha256']:raise ValueError('Frozen report hash differs')
        report=read_report(archive)
        if report['run_id']!=rid or report['status']!='passed':raise ValueError('Frozen report is not passed')
        references[name]=dict(frozen,path='/artifacts/'+str(archive.relative_to(artifacts)))
    work=artifacts/'data-jobs'/config['baseline']['run_id']/'work'
    for name in ('snapshot','ranking-P','ranking-H','evaluation-P','evaluation-H'):
        if not (work/name/'manifest.json').is_file():raise ValueError('Missing private Q1 artifacts; ZIPs cannot reconstruct them')
    references['baseline']['work_path']='/artifacts/'+str(work.relative_to(artifacts))
    return references


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='action',required=True)
    launch=commands.add_parser('start')
    launch.add_argument('--dataset',choices=('synthetic','hm'),required=True)
    launch.add_argument('--data-as-of',required=True);launch.add_argument('--expected-commit',required=True)
    launch.add_argument('--machine-id',default='mac24');launch.add_argument('--profile',default='hm-project')
    launch.add_argument('--resources',default='mac24')
    for name in ('status','export'):
        command=commands.add_parser(name);command.add_argument('--run-id',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='start':
            if args.data_as_of!='2020-09-09' or not re.fullmatch('[a-f0-9]{40}',args.expected_commit):
                raise ValueError('T4-A requires explicit cutoff 2020-09-09 and full expected commit')
            start(args.machine_id,args.profile,args.dataset,args.expected_commit,args.resources,
                release_options={'data_as_of':args.data_as_of})
        else:
            validate_run_id(args.run_id)
            if not args.run_id.startswith('release-'):raise ValueError('Release run_id required')
            if args.action=='export':print('Paquete para GitHub: '+str(export(args.run_id)))
            else:
                _,run,_,state,result,status=inspect_run(args.run_id)
                print(json.dumps({'run_id':args.run_id,'status':status,'exit_code':state.get('ExitCode'),
                    'oom_killed':state.get('OOMKilled',False),'limits':run.get('effective_limits'),'result':result},indent=2))
                return 0 if status in ('running','passed') else 1
        return 0
    except KeyboardInterrupt:return 130
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('ERROR: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
