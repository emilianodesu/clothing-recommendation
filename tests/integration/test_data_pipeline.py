import json
from pathlib import Path

from hm_recommender.data.pipeline import run_pipeline


def test_synthetic_pipeline_reconciles_and_emits_only_small_reports(tmp_path):
    config = tmp_path / 'demo.yaml'
    config.write_text('dataset: synthetic\ndata_dir: ' + str(tmp_path / 'data') + '\nartifacts_dir: ' + str(tmp_path / 'artifacts') + '\n')
    output = tmp_path / 'run'
    report = run_pipeline(config, output)
    assert report['status'] == 'passed'
    assert report['counts']['transactions'] == 7
    assert report['examples']['transactions'] == 7
    assert all(s['status'] == 'passed' for s in report['steps'])
    assert report['peak_rss_bytes'] > 0
    assert (output / 'result.json').stat().st_size < 100_000
    assert 'user-a' not in (output / 'result.json').read_text()
    assert json.loads((output / 'result.json').read_text())['status'] == 'passed'
