"""Executed in existing scheduler; bounded child build, no publication call."""
import json
from pathlib import Path
import subprocess
import sys
import time

config,output=sys.argv[1:3]
program="""import json,sys
from pathlib import Path
from hm_recommender.pipeline.runner import build
result=build(Path(sys.argv[1]),Path(sys.argv[2]).parent/'build-reports')
Path(sys.argv[2]).write_text(json.dumps(result))
"""
started=time.monotonic()
with Path(output).with_suffix('.log').open('x') as log:
    child=subprocess.Popen([sys.executable,'-c',program,config,output],stdout=log,stderr=subprocess.STDOUT)
    timed_out=False
    try:code=child.wait(timeout=540)
    except subprocess.TimeoutExpired:
        timed_out=True;child.terminate()
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:child.kill();child.wait()
        code=124
Path(output).with_suffix('.exit.json').write_text(json.dumps(dict(exit_code=code,timed_out=timed_out,elapsed_seconds=time.monotonic()-started)))
sys.exit(code)
