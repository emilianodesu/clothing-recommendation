"""Q4: consume private frozen evaluations without loading source data or training."""
import shutil
from pathlib import Path
import subprocess
import sys
import time

from hm_recommender.data.io import atomic_json
from hm_recommender.quality.compare import compare_campaign
from hm_recommender.quality.contracts import digest
from hm_recommender.quality.supervisor import resources


def run_summary(config,environment,run_dir,*,verify=True):
    started=time.monotonic();free=shutil.disk_usage(run_dir).free
    result={'stage':'Q4','protocol':'quality-v1','size':'summary','status':'failed',
        'test_evaluated':False,'quality_accepted':False,'source_commit':environment['source_commit']}
    try:
        atomic_json(run_dir/'config.resolved.json',config.model_dump())
        code=subprocess.run([sys.executable,'-m','pytest','tests/unit/test_quality_selection.py','-q','-p','no:cacheprovider'],check=False).returncode if verify else 0
        result['verification_exit_code']=code
        if code:raise ValueError('Q4 verification failed')
        campaign=environment['quality_campaign'];declaration=campaign['declaration']
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
        sys.path.insert(0,'/opt/hmrec/scripts')
        from quality_summary import validate_declaration
        validate_declaration(declaration)
        expected={(fold,role,rid) for fold,roles in declaration['folds'].items() for role,rid in roles.items() if rid is not None}
        actual={(entry['fold'],entry['role'],entry['run_id']) for entry in campaign['entries']}
        if expected!=actual or len(actual)!=len(campaign['entries']):raise ValueError('Campaign declaration changed')
        result.update(compare_campaign(campaign['entries'],config,run_dir/'work',size=declaration['size']))
        result['inputs']['campaign_declaration_sha256']=digest(declaration)
        result['status']='passed'
    except Exception as error:
        result.update(status='failed',failure_reason=type(error).__name__)
        print('Q4 failed: '+type(error).__name__+': '+str(error),flush=True)
    final=shutil.disk_usage(run_dir).free
    result['disk']={'start_free_bytes':free,'min_free_bytes':min(free,final),'end_free_bytes':final}
    result.update(job_duration_seconds=time.monotonic()-started,job_resources=resources())
    atomic_json(run_dir/'result.json',result)
    return 0 if result['status']=='passed' else 1
