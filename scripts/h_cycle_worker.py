"""Run inside the existing scheduler. A single child build, bounded to 540 s."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CHILD = r'''
import json,sys,time
from pathlib import Path
from hm_recommender.pipeline.runner import build
def resources():
    root=Path('/sys/fs/cgroup')
    return {name:(root/name).read_text() for name in (
        'memory.current','memory.peak','memory.swap.current','memory.swap.peak',
        'memory.events','memory.max','cpu.stat')}
output=Path(sys.argv[2])
before=resources()
start=time.monotonic();wall_start=time.time()
result=build(Path(sys.argv[1]),output.parent/'build-reports')
end=time.monotonic();wall_end=time.time()
record=dict(start=start,end=end,wall_start=wall_start,wall_end=wall_end,
            before=before,after=resources(),result=result)
with output.open('x') as f:json.dump(record,f)
'''


def run_child(config,output):
    output=Path(output);started=time.monotonic()
    env=dict(os.environ)
    for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','POLARS_MAX_THREADS'):
        env[key]='2'
    with output.with_suffix('.log').open('x') as log:
        child=subprocess.Popen([sys.executable,'-c',CHILD,str(config),str(output)],
                               env=env,stdout=log,stderr=subprocess.STDOUT)
        timed_out=False
        try:code=child.wait(timeout=540)
        except subprocess.TimeoutExpired:
            timed_out=True;child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            code=124
    # This marker never precedes wait(): recovery must not overlap a live child.
    with output.with_suffix('.exit.json').open('x') as f:
        json.dump(dict(exit_code=code,timed_out=timed_out,elapsed_seconds=time.monotonic()-started),f)
    return code


if __name__=='__main__':sys.exit(run_child(*sys.argv[1:3]))
