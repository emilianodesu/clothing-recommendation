"""T2 stages, reused by the CLI and detached jobs. Reports contain no customer rows."""
from datetime import datetime, timezone, date
from importlib.metadata import version
import json
from pathlib import Path
import platform
import resource
import shutil
import time

from hm_recommender.config import load_config
from hm_recommender.data.ingest import ingest
from hm_recommender.data.io import atomic_json, locked, sha256
from hm_recommender.data.synthetic import make_zip
from hm_recommender.data.validate import COMPETITION, HM_SOURCE
from hm_recommender.features.build import build_examples, build_features


def now():
    return datetime.now(timezone.utc).isoformat()


def run_pipeline(config_path: Path, run_dir: Path):
    config = load_config(config_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(run_dir / 'config.resolved.json', config)
    report = {'schema_version':1, 'stage':'T2', 'status':'running', 'started_at':now(), 'steps':[],
              'config_sha256':sha256(config_path), 'dataset':config['dataset'], 'python':platform.python_version(),
              'architecture':platform.machine(), 'versions':{p:version(p) for p in ('polars','pyarrow','pydantic')},
              'scope':'data and temporal features; no trained model or recommendation metrics'}
    started = time.monotonic()
    data_dir, artifacts = Path(config['data_dir']), Path(config['artifacts_dir'])
    data_dir.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    report['disk_free_before_bytes'] = shutil.disk_usage(data_dir).free

    def stage(name, operation):
        step = {'name':name, 'status':'running'}
        report['steps'].append(step)
        atomic_json(run_dir / 'result.json', report)
        print(json.dumps({'stage':name, 'status':'running'}), flush=True)
        tick = time.monotonic()
        try:
            result = operation()
        except Exception:
            step['status'] = 'failed'
            raise
        else:
            step['status'] = 'passed'
            return result
        finally:
            step['duration_seconds'] = round(time.monotonic() - tick, 3)
            atomic_json(run_dir / 'result.json', report)

    try:
        with locked(artifacts / '.data-pipeline'):
            name = config['dataset']
            source = data_dir / 'kaggle_hm' / (COMPETITION + '.zip') if name == 'hm' else make_zip(data_dir / 'synthetic/source.zip')
            if name == 'hm' and shutil.disk_usage(data_dir).free < 20 * 2**30:
                raise ValueError('Se requieren 20 GiB libres adicionales para salidas y temporales de T2')
            dataset = stage('ingest', lambda:ingest(source, data_dir / 'processed' / (name + '-v1'), expected=HM_SOURCE if name == 'hm' else None))
            report.update(dataset_id=dataset.dataset_id, source_sha256=dataset.source_sha256,
                          counts=dataset.counts, min_date=str(dataset.min_date), max_date=str(dataset.max_date),
                          partitions=len(dataset.partitions))
            root = artifacts / 'features' / (name + '-v1')
            train = stage('features-validation-cut', lambda:build_features(dataset, date.fromisoformat(config['validation_start']), root / 'train'))
            vocabulary = root / 'train' / train.files['vocabulary'].path
            valid = stage('features-test-cut', lambda:build_features(dataset, date.fromisoformat(config['test_start']), root / 'test', vocabulary=vocabulary))
            test = stage('features-serving-cut', lambda:build_features(dataset, date.fromisoformat(config['test_end']), root / 'serving', vocabulary=vocabulary))
            examples = stage('weekly-examples', lambda:build_examples(dataset, root / 'examples',
                validation_start=date.fromisoformat(config['validation_start']), test_start=date.fromisoformat(config['test_start']), test_end=date.fromisoformat(config['test_end'])))
            report['features'] = {'train_cut':train.counts, 'test_cut':valid.counts, 'end_snapshot':test.counts}
            report['examples'] = examples['counts']
            report['output_bytes'] = sum(p.stat().st_size for directory in (dataset._root, root) for p in directory.rglob('*') if p.is_file())
            report['manifest_sha256'] = {'dataset':sha256(dataset._root / 'manifest.json'),
                'train_features':sha256(root / 'train/manifest.json'), 'test_features':sha256(root / 'test/manifest.json'),
                'end_features':sha256(root / 'serving/manifest.json'), 'examples':sha256(root / 'examples/manifest.json')}
            report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        report.update(finished_at=now(), duration_seconds=round(time.monotonic()-started, 3),
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if platform.system() == 'Linux' else 1),
                      disk_free_after_bytes=shutil.disk_usage(data_dir).free)
        peak = Path('/sys/fs/cgroup/memory.peak')
        if peak.is_file():
            report['cgroup_peak_bytes'] = int(peak.read_text())
        atomic_json(run_dir / 'result.json', report)
    return report
