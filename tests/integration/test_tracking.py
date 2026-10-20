import json
import os
from pathlib import Path
import tempfile
import uuid

import pytest
import torch

from hm_recommender.database import connect
from hm_recommender.model.tracking import log_run


@pytest.mark.skipif(os.environ.get('HMREC_TRACKING_TESTS') != '1', reason='requires local PostgreSQL/MLflow')
def test_mlflow_postgres_and_http_artifacts(tmp_path):
    report = {'experiment_id': 'verification-' + uuid.uuid4().hex,
              'source_commit': 'a' * 40,
              'training': {'config': {'seed': 42}, 'input_identity': {'sha256': 'b' * 64},
                           'rows_consumed': 10, 'duration_seconds': 1., 'peak_memory_bytes': 1024},
              'evaluation': {'map_at_12': .5, 'ndcg_at_12': .6, 'recall_at_200': .7, 'coverage': .8}}
    (tmp_path / 'training.json').write_text(json.dumps(report['training']))
    (tmp_path / 'evaluation.json').write_text(json.dumps(report['evaluation']))
    torch.save({'weights': torch.eye(2)}, tmp_path / 'model.pt')
    result = log_run(tmp_path, report, tmp_path / 'model.pt')
    assert result['verified'] and result['artifact_uri'].startswith('mlflow-artifacts:')
    with connect('mlflow') as connection:
        assert connection.execute('SELECT count(*) FROM runs WHERE run_uuid = %s', (result['run_id'],)).fetchone()[0] == 1
    from mlflow import MlflowClient
    with tempfile.TemporaryDirectory() as directory:
        path = Path(MlflowClient().download_artifacts(result['run_id'], 'model.pt', directory))
        assert torch.equal(torch.load(path, weights_only=True)['weights'], torch.eye(2))
