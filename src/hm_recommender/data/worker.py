"""Detached T2 worker: verify the installed image, then run the same pipeline."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from hm_recommender.data.io import atomic_json
from hm_recommender.data.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    code = subprocess.run([sys.executable, '-m', 'pytest', 'tests/unit',
        'tests/integration/test_ingest.py', 'tests/integration/test_data_pipeline.py', '-q'], check=False).returncode
    if code:
        atomic_json(args.run_dir / 'result.json', {'stage':'T2', 'status':'failed', 'verification_exit_code':code})
        return 1
    try:
        run_pipeline(args.config, args.run_dir)
        return 0
    except Exception as error:
        # Parser errors may include input values; keep diagnostics to error classes.
        print('T2 falló: ' + type(error).__name__ + '; revisar etapa y progreso.', flush=True)
        return 1
    finally:
        path = args.run_dir / 'result.json'
        result = json.loads(path.read_text()) if path.exists() else {'stage':'T2', 'status':'failed'}
        result['verification_exit_code'] = code
        atomic_json(path, result)


if __name__ == '__main__':
    sys.exit(main())
