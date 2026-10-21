"""Read closed report evidence as data, never execute or extract its contents."""
from pathlib import Path
import sys
from hm_recommender.data.io import sha256


def closed_report(path,reference):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
    sys.path.insert(0,'/opt/hmrec/scripts')
    from handoff import read_report
    path=Path(path)
    if sha256(path)!=reference['archive_sha256']:raise ValueError('Frozen archive hash differs')
    report=read_report(path)
    if report['run_id']!=reference['run_id'] or report['status']!='passed':raise ValueError('Closed reference identity differs')
    return report


def verify_selection(path,config):
    report=closed_report(path,config['selection']);result=report['result']
    if (report['kind']!='quality-handoff' or result['stage']!='Q4'
        or result.get('decision',{}).get('selected_method')!='H' or not result.get('selection_finalized')
        or not result.get('comparison_complete') or result['test_evaluated'] is not False):
        raise ValueError('Q4 does not select H')
    return report
