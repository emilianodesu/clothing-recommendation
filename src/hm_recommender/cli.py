"""Commands shared by one-off jobs and the future scheduler."""
import argparse
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys


def check(imports_only=False):
    packages = {"torch": "torch", "polars": "polars", "pyarrow": "pyarrow", "numpy": "numpy",
        "fastapi": "fastapi", "uvicorn": "uvicorn", "qdrant-client": "qdrant_client",
        "psycopg": "psycopg", "mlflow-skinny": "mlflow", "pydantic": "pydantic"}
    for module in packages.values():
        importlib.import_module(module)
    import torch
    if torch.eye(2).sum().item() != 2.0 or torch.version.cuda is not None:
        raise ValueError("Expected an operational CPU-only PyTorch build")
    report = {"python": platform.python_version(), "architecture": platform.machine(),
              "packages": {name: version(name) for name in packages}, "torch_device": "cpu"}
    if not imports_only:
        from hm_recommender.database import connect
        from qdrant_client import QdrantClient
        services = {}
        for name in ("app", "airflow", "mlflow"):
            with connect(name) as connection:
                database, user = connection.execute("SELECT current_database(), current_user").fetchone()
                if (database, user) != ("hm_" + name, "hm_" + name):
                    raise ValueError("Wrong database identity")
                services[name + "_database"] = "healthy"
        client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"), timeout=5)
        client.get_collections()
        client.close()
        services["qdrant"] = "healthy"
        report["services"] = services
    report["status"] = "passed"
    return report


def main():
    parser = argparse.ArgumentParser(prog="hmrec")
    commands = parser.add_subparsers(dest="command", required=True)
    checks = commands.add_parser("check", help="Check actual runtime imports and local services")
    checks.add_argument("--imports-only", action="store_true")
    db = commands.add_parser("db")
    db.add_argument("action", choices=("migrate",))
    config = commands.add_parser("config")
    config.add_argument("path", type=Path)
    data = commands.add_parser("data")
    data_commands = data.add_subparsers(dest="data_action", required=True)
    validate = data_commands.add_parser("validate")
    validate.add_argument("--source", type=Path, required=True)
    validate.add_argument("--mode", choices=("quick", "full"), default="full")
    ingestion = data_commands.add_parser("ingest")
    ingestion.add_argument("--source", type=Path, required=True)
    ingestion.add_argument("--output", type=Path, required=True)
    ingestion.add_argument("--synthetic", action="store_true")
    pipeline = data_commands.add_parser("pipeline")
    pipeline.add_argument("--config", type=Path, required=True)
    pipeline.add_argument("--run-dir", type=Path, required=True)
    features = commands.add_parser("features")
    features.add_argument("action", choices=("build",))
    features.add_argument("--dataset", type=Path, required=True)
    features.add_argument("--data-as-of", type=__import__('datetime').date.fromisoformat, required=True)
    features.add_argument("--output", type=Path, required=True)
    features.add_argument("--vocabulary", type=Path)
    release = commands.add_parser("release", help="Prepare or verify a local H package; no activation")
    release_commands = release.add_subparsers(dest="release_action", required=True)
    release_build = release_commands.add_parser("build")
    release_build.add_argument("--dataset", type=Path, required=True)
    release_build.add_argument("--data-as-of", type=__import__('datetime').date.fromisoformat, required=True)
    release_build.add_argument("--output", type=Path, required=True)
    release_build.add_argument("--evidence-archive", type=Path)
    release_verify = release_commands.add_parser("verify")
    release_verify.add_argument("directory", type=Path)
    serving = commands.add_parser('serving')
    serving_commands = serving.add_subparsers(dest='serving_action', required=True)
    serving_commands.add_parser('run')
    serving_prepare = serving_commands.add_parser('prepare')
    serving_prepare.add_argument('--directory', required=True)
    serving_activate = serving_commands.add_parser('activate')
    serving_activate.add_argument('--release-id', required=True)
    serving_commands.add_parser('rollback')
    events=commands.add_parser('events')
    event_commands=events.add_subparsers(dest='events_action',required=True)
    event_seed=event_commands.add_parser('seed')
    event_seed.add_argument('--dataset',type=Path,required=True)
    event_seed.add_argument('--directory',type=Path,required=True)
    event_seed.add_argument('--stream-id',required=True)
    event_seed.add_argument('--mode',choices=('synthetic','simulation'),required=True)
    demo=event_commands.add_parser('demo-package')
    demo.add_argument('--output',type=Path,required=True)
    orchestration=commands.add_parser('pipeline')
    operations=orchestration.add_subparsers(dest='pipeline_action',required=True)
    register=operations.add_parser('register');register.add_argument('--stream-id',required=True)
    refresh=operations.add_parser('refresh');refresh.add_argument('--reports',type=Path,default=Path('/artifacts/pipeline/reports'))
    for action in ('build','publish'):
        op=operations.add_parser(action);op.add_argument('--config',type=Path,required=True)
        op.add_argument('--reports',type=Path,default=Path('/artifacts/pipeline/reports'))
    args = parser.parse_args()
    try:
        if args.command=='pipeline':
            from hm_recommender.pipeline import runner
            if args.pipeline_action=='register':output=runner.register(args.stream_id)
            elif args.pipeline_action=='refresh':output=runner.refresh(args.reports)
            elif args.pipeline_action=='build':output=runner.build(args.config,args.reports)
            else:output=runner.publish(args.config)
        elif args.command=='events':
            if args.events_action=='seed':
                from hm_recommender.events.aggregate import initialize_stream
                output=initialize_stream(args.dataset,args.directory,args.stream_id,mode=args.mode)
                from hm_recommender.database import connect
                from hm_recommender.api.runtime import source_digest
                with connect() as conn:
                    row=conn.execute('SELECT count(*),sum(frequency),min(day),max(day) FROM stream_daily_seed WHERE stream_id=%s',(args.stream_id,)).fetchone()
                output['seed']=dict(daily_rows=row[0],purchases=int(row[1] or 0),first_day=str(row[2]),last_day=str(row[3]),data_as_of=output['data_as_of'])
                cgroup=Path('/sys/fs/cgroup')
                output['process']=dict(cgroup_peak_bytes=int((cgroup/'memory.peak').read_text()),
                    swap_peak_bytes=int((cgroup/'memory.swap.peak').read_text()),source_tree_sha256=source_digest())
            else:
                from hm_recommender.model.demo import prepare_demo_dataset
                from hm_recommender.releases.package import build_history_release
                from datetime import date
                ds=prepare_demo_dataset(args.output/'input')
                manifest=build_history_release(ds,date(2020,9,9),args.output/'release')
                output={'release_id':manifest.release_id,'dataset':'synthetic'}
        elif args.command == 'serving':
            if args.serving_action == 'run':
                import uvicorn
                uvicorn.run('hm_recommender.api.app:create_app',factory=True,host='0.0.0.0',port=8000,
                    workers=1,access_log=False,log_level='warning')
                return 0
            from hm_recommender.api.admin import command
            output = command(args.serving_action,directory=getattr(args,'directory',None),
                release_id=getattr(args,'release_id',None))
        elif args.command == "check":
            output = check(args.imports_only)
        elif args.command == "db":
            from hm_recommender.database import migrate
            output = {"applied": migrate(Path(os.environ.get("HMREC_MIGRATIONS_DIR", "/opt/hmrec/migrations")))}
        elif args.command == "data":
            from hm_recommender.data.validate import HM_SOURCE, validate_source
            if args.data_action == "validate":
                output = validate_source(args.source, args.mode)
            elif args.data_action == "ingest":
                from hm_recommender.data.ingest import ingest
                output = ingest(args.source, args.output, expected=None if args.synthetic else HM_SOURCE).model_dump(mode="json")
            else:
                from hm_recommender.data.pipeline import run_pipeline
                output = run_pipeline(args.config, args.run_dir)
        elif args.command == "release":
            from hm_recommender.releases.package import build_history_release, verify_history_release
            if args.release_action == "verify":
                output = verify_history_release(args.directory).model_dump(mode="json")
            else:
                from hm_recommender.data.ingest import load_dataset
                output = build_history_release(load_dataset(args.dataset), args.data_as_of, args.output,
                    evidence_archive=args.evidence_archive).model_dump(mode="json")
        elif args.command == "features":
            from hm_recommender.data.ingest import load_dataset
            from hm_recommender.features.build import build_features
            output = build_features(load_dataset(args.dataset), args.data_as_of, args.output, vocabulary=args.vocabulary).model_dump(mode="json")
        else:
            from hm_recommender.config import load_config
            output = load_config(args.path)
        print(json.dumps(output, indent=2))
        return 0
    except Exception as error:
        message = str(error)
        for key, value in os.environ.items():
            if key.endswith("_PASSWORD") and value:
                message = message.replace(value, "[REDACTED]")
        print("ERROR: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
