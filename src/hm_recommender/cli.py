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
    args = parser.parse_args()
    try:
        if args.command == "check":
            output = check(args.imports_only)
        elif args.command == "db":
            from hm_recommender.database import migrate
            output = {"applied": migrate(Path(os.environ.get("HMREC_MIGRATIONS_DIR", "/opt/hmrec/migrations")))}
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
