"""Wall-clock and disk watchdog outside the scientific worker process."""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time

from hm_recommender.data.io import atomic_json


def resources():
    result={'peak_rss_bytes':max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)*(1 if sys.platform=='darwin' else 1024)}
    for name in ('memory.current','memory.peak','memory.swap.current','memory.swap.peak'):
        path=Path('/sys/fs/cgroup')/name
        if path.exists(): result[name]=int(path.read_text())
    for name in ('memory.events','memory.stat'):
        path=Path('/sys/fs/cgroup')/name
        if path.exists():result[name]={k:int(v) for k,v in (line.split() for line in path.read_text().splitlines())}
    return result


def supervise(command,run_dir,seconds,min_free_bytes,*,interval=1):
    started=time.monotonic(); reason=None
    initial_free=shutil.disk_usage(run_dir).free;minimum_free=initial_free
    process=subprocess.Popen(command,start_new_session=True)
    try:
        while process.poll() is None:
            minimum_free=min(minimum_free,shutil.disk_usage(run_dir).free)
            if time.monotonic()-started>=seconds:reason='timeout'
            elif minimum_free<min_free_bytes:reason='disk'
            if reason:
                os.killpg(process.pid,signal.SIGKILL);break
            time.sleep(interval)
        code=process.wait()
    except BaseException:
        if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
        process.wait(); raise
    path=run_dir/'result.json'
    try: result=json.loads(path.read_text()) if path.exists() else {}
    except (ValueError,OSError):result={}
    elapsed=time.monotonic()-started
    final_free=shutil.disk_usage(run_dir).free
    minimum_free=min(minimum_free,final_free)
    if reason is None and elapsed>=seconds:reason='timeout'
    if reason is None and minimum_free<min_free_bytes:reason='disk'
    if reason or code or result.get('status')!='passed':
        result.update(status='failed',failure_reason=reason or 'worker_exit')
        code=code or 1
    result['disk']={'start_free_bytes':initial_free,'min_free_bytes':min(minimum_free,final_free),'end_free_bytes':final_free}
    result.update(job_duration_seconds=elapsed,job_resources=resources())
    atomic_json(path,result)
    return code if code>0 else (1 if code else 0)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args();env=json.loads((args.run_dir/'environment.json').read_text())
    return supervise([sys.executable,'-m','hm_recommender.quality.worker','--config',str(args.config),'--run-dir',str(args.run_dir)],
        args.run_dir,env['quality_budget_seconds'],5*2**30)

if __name__=='__main__':sys.exit(main())
