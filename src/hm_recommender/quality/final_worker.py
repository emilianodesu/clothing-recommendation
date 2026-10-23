"""Bounded private worker. Waits for host go marker and retains final cgroup."""
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from hm_recommender.data.io import atomic_json, sha256
from hm_recommender.data.ingest import load_dataset
from hm_recommender.quality.final import evaluate_final


def evaluate(config,root):
    dataset=load_dataset(Path(config['dataset_dir']))
    if dataset.source_sha256!=config['source_sha256'] or sha256(dataset._root/'manifest.json')!=config['dataset_manifest_sha256']:
        raise ValueError('Dataset differs from frozen source')
    result=evaluate_final(dataset,root/'evaluation')
    load_dataset(Path(config['dataset_dir']))  # verify original partition hashes again
    atomic_json(root/'evaluation-result.json',result)


def supervise(root, *, timeout=5400, wait_seconds=300):
    root=Path(root); deadline=time.monotonic()+wait_seconds
    atomic_json(root/'ready.json',{'ready':True})
    while not (root/'go.json').exists():
        if time.monotonic()>deadline:raise TimeoutError('Host did not authorize worker')
        time.sleep(.2)
    start=time.monotonic();wall=time.time()
    result=dict(exit_code=None,duration_seconds=0.,clock_discrepancy_seconds=0.,error_type=None)
    child=None
    try:
        with (root/'worker-private.log').open('w') as log:
            child=subprocess.Popen([sys.executable,'-m','hm_recommender.quality.final_worker','child',str(root)],stdout=log,stderr=subprocess.STDOUT)
            try:result['exit_code']=child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
                result['exit_code']=child.returncode;result['error_type']='TimeoutError'
    except BaseException as e:
        result['error_type']=type(e).__name__
        (root/'supervisor-private.log').write_text(traceback.format_exc())
        # A completion marker must never authorize recovery over a live child.
        if child is not None and child.poll() is None:
            child.terminate()
            try:result['exit_code']=child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill();result['exit_code']=child.wait()
    result['duration_seconds']=time.monotonic()-start
    result['clock_discrepancy_seconds']=abs(time.time()-wall-result['duration_seconds'])
    atomic_json(root/'complete.json',result)
    return result


if __name__=='__main__':
    mode,path=sys.argv[1:];root=Path(path)
    if mode=='child':evaluate(json.loads((root/'config.json').read_text()),root)
    elif mode=='supervise':
        supervise(root)
        # Host captures final memory. No work and no retries after completion.
        while True:time.sleep(60)
    else:raise ValueError('Unknown worker mode')
