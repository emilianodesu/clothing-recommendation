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
    args = parser.parse_args()
    try:
        if args.command == "check":
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
