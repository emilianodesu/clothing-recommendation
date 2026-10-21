"""T4-A preparation/parity only. No training, activation, test or quality ledger."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from hm_recommender.data.ingest import load_dataset
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.releases.package import build_history_release, verify_history_release, configuration
from hm_recommender.releases.parity import verify_parity, compare_aggregates
from hm_recommender.quality.contracts import FoldSpec, QualityConfig, FoldEvaluation
from hm_recommender.quality.temporal import build_snapshot, read_labels
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.evaluate import evaluate_ranking
from hm_recommender.quality.supervisor import resources, supervise


def run(config_path,run_dir,*,verify=True):
    started=time.monotonic();root=Path(run_dir);initial_free=shutil.disk_usage(root).free
    result={'stage':'T4-A','protocol':'history-release-v1','status':'failed',
        'test_evaluated':False,'quality_accepted':False,'activated':False,'training_performed':False}
    try:
        config=json.loads(Path(config_path).read_text())
        if config!=configuration():raise ValueError('Fixed release protocol required')
        env=json.loads((root/'environment.json').read_text());options=env['release']
        if options['data_as_of']!=config['data_as_of'] or env['dataset'] not in ('synthetic','hm'):
            raise ValueError('Only validation V3 preparation is authorized')
        atomic_json(root/'config.resolved.json',config)
        result.update(source_commit=env['source_commit'],dataset=env['dataset'],data_as_of=options['data_as_of'])
        code=subprocess.run([sys.executable,'-m','pytest','tests/unit/test_history_release.py',
            'tests/unit/test_recent_history_ranking.py','-q','-p','no:cacheprovider'],check=False).returncode if verify else 0
        result['verification_exit_code']=code
        if code:raise ValueError('Release verification failed')
        work=root/'work';work.mkdir(exist_ok=False)
        if env['dataset']=='synthetic':
            from hm_recommender.model.demo import prepare_demo_dataset
            dataset=prepare_demo_dataset(work/'demo')
        else:dataset=load_dataset(Path('/data/processed/hm-v1'))
        fold=FoldSpec.evaluation('V3')
        selection=None
        if env['dataset']=='hm':
            from hm_recommender.releases.evidence import closed_report
            refs=env['release_references'];selection=Path(refs['selection']['path'])
            if dataset.source_sha256!=config['source_sha256'] or sha256(dataset._root/'manifest.json')!=config['dataset_manifest_sha256']:
                raise ValueError('Frozen H&M dataset differs')
            for name in ('baseline','diagnostic'):closed_report(refs[name]['path'],config[name])
        os.environ['HMREC_SOURCE_COMMIT']=env['source_commit']
        package=build_history_release(dataset,date.fromisoformat(options['data_as_of']),work/'release',evidence_archive=selection)
        # Labels enter only after the immutable package has been built from all past data.
        labels=read_labels(dataset,fold).collect(engine='streaming')
        buyers=labels['customer_id'].unique().sort().to_list()
        if env['dataset']=='hm':
            from hm_recommender.quality.graph_stage import load_reference, load_local
            snapshot,rankings,baseline=load_reference(refs['baseline'],dataset,fold,'full',QualityConfig(),buyers,labels)
            original=rankings['H'];expected=load_local(Path(refs['baseline']['work_path'])/'evaluation-H',FoldEvaluation)
            if len(buyers)!=config['validation_buyers'] or package.files['catalog'].rows!=config['catalog_size']:
                raise ValueError('Frozen validation population differs')
        else:
            snapshot=build_snapshot(dataset,fold,work/'reference-snapshot',QualityConfig())
            original=rank_baseline(snapshot,buyers,'H',work/'reference-ranking')
            expected=evaluate_ranking(original,labels.lazy(),snapshot,work/'reference-evaluation')
        print('T4-A: checking exact validation parity',flush=True)
        parity=verify_parity(package,dataset,snapshot,labels,original,expected,work/'parity')
        if env['dataset']=='hm':
            from hm_recommender.releases.evidence import closed_report
            diagnostic=closed_report(refs['diagnostic']['path'],config['diagnostic'])['result']['history_validation']
            compare_aggregates(parity['metrics'],diagnostic['methods']['recent_then_popular']['metrics'])
            # Recheck closed artifacts after reading them; original mounts are read-only.
            load_reference(refs['baseline'],dataset,fold,'full',QualityConfig(),buyers,labels)
        checked=verify_history_release(work/'release')
        result.update(status='passed',parity=parity,inputs={'source_sha256':dataset.source_sha256,
            'dataset_manifest_sha256':sha256(dataset._root/'manifest.json')},
            references={name:config[name] for name in ('selection','baseline','diagnostic')},
            package={'release_id':checked.release_id,'manifest_sha256':sha256(work/'release/manifest.json'),
                'schema_version':2,'selection_archive_verified':checked.selection_archive_verified,
                'past_buyers':checked.files['customers'].rows,'history_rows':checked.files['histories'].rows,
                'catalog_size':checked.files['catalog'].rows,
                'bytes':sum(f.size_bytes for f in checked.files.values())+(work/'release/manifest.json').stat().st_size,
                'files':[{'name':f.path,'sha256':f.sha256,'size_bytes':f.size_bytes,'rows':f.rows} for f in checked.files.values()]})
    except Exception as error:
        result.update(status='failed',failure_reason=type(error).__name__)
        # Do not log dataframe exceptions or private identifiers from source rows.
        print('T4-A failed: '+type(error).__name__,flush=True)
    final_free=shutil.disk_usage(root).free
    result.update(job_duration_seconds=time.monotonic()-started,job_resources=resources(),
        disk={'start_free_bytes':initial_free,'min_free_bytes':min(initial_free,final_free),'end_free_bytes':final_free})
    atomic_json(root/'result.json',result)
    return 0 if result['status']=='passed' else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True);parser.add_argument('--supervise',action='store_true')
    args=parser.parse_args()
    if args.supervise:
        config=configuration()
        return supervise([sys.executable,'-m','hm_recommender.releases.worker','--config',str(args.config),
            '--run-dir',str(args.run_dir)],args.run_dir,config['max_seconds'],config['min_free_bytes'])
    return run(args.config,args.run_dir)


if __name__=='__main__':sys.exit(main())
