"""Local MLflow server and HTTP artifact verification; no cloud credentials."""
import os
from pathlib import Path
from urllib.parse import quote


def serve():
    artifact_dir = Path('/mlartifacts')
    artifact_dir.mkdir(exist_ok=True)
    if os.getuid() == 0:
        os.chown(artifact_dir, 10001, 10001)
        os.setgid(10001); os.setuid(10001)
    password = quote(os.environ.pop('HM_MLFLOW_PASSWORD'), safe='')
    os.environ['MLFLOW_BACKEND_STORE_URI'] = 'postgresql+psycopg://hm_mlflow:' + password + '@postgres:5432/hm_mlflow'
    os.execvp('mlflow', ['mlflow', 'server', '--host', '0.0.0.0', '--port', '5000', '--workers', '1',
        '--serve-artifacts', '--artifacts-destination', str(artifact_dir), '--default-artifact-root', 'mlflow-artifacts:/',
        '--allowed-hosts', 'mlflow:5000,localhost:*,127.0.0.1:*'])


def log_run(directory, report, model, resume_id=None):
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT', '15')
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES', '2')
    os.environ.setdefault('MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR', '1')
    os.environ.setdefault('MLFLOW_ARTIFACT_UPLOAD_DOWNLOAD_TIMEOUT', '180')
    import mlflow
    from mlflow import MlflowClient
    from hm_recommender.data.io import atomic_json
    mlflow.set_tracking_uri(os.environ['MLFLOW_TRACKING_URI'])
    mlflow.set_experiment('hm-two-tower-t3')
    with mlflow.start_run(run_id=resume_id, run_name=report['experiment_id']) as run:
        atomic_json(directory / 'mlflow.json', {'run_id': run.info.run_id, 'tracking_transport': 'http'})
        mlflow.log_params({**report['training']['config'], 'source_commit': report['source_commit'],
                          'input_identity': report['training']['input_identity']['sha256'],
                          'experiment_id': report['experiment_id']})
        mlflow.log_metrics({**{key: report['evaluation'][key] for key in ('map_at_12', 'ndcg_at_12', 'recall_at_200', 'coverage')},
            'rows_consumed': report['training']['rows_consumed'], 'duration_seconds': report['training']['duration_seconds'],
            'peak_rss_bytes': report['training']['peak_memory_bytes']})
        mlflow.log_artifact(str(directory / 'training.json'))
        mlflow.log_artifact(str(directory / 'evaluation.json'))
        mlflow.log_artifact(str(model))
        uri = run.info.artifact_uri
        if not uri.startswith('mlflow-artifacts:'):
            raise ValueError('MLflow must proxy artifacts over HTTP')
        # Download a metadata artifact through HTTP to prove round-trip persistence.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            downloaded = Path(MlflowClient().download_artifacts(run.info.run_id, 'evaluation.json', tmp))
            if downloaded.read_bytes() != (directory / 'evaluation.json').read_bytes():
                raise ValueError('MLflow artifact round-trip mismatch')
        return {'run_id': run.info.run_id, 'artifact_uri': uri, 'transport': 'http', 'verified': True}


if __name__ == '__main__':
    serve()
