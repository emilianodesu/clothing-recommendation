#!/usr/bin/env python3
"""Portable host entry point. Run from any working directory; no pip needed."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from bootstrap import PASSWORD_KEYS, prepare_configuration, project_name, read_configuration

ROOT = Path(__file__).resolve().parents[1]


def compose(root, values, *args, capture=False, log_path=None):
    environment = dict(os.environ)
    environment.pop("COMPOSE_FILE", None)
    environment.pop("COMPOSE_PROFILES", None)
    environment.update(values)
    command = ["docker", "compose", "--project-directory", str(root), "--env-file",
        str(root / ".env"), "--file", str(root / "compose.yaml"), "--project-name",
        values["COMPOSE_PROJECT_NAME"], *args]
    if log_path is not None:
        from handoff import execute
        code, _ = execute(command, root, environment, log_path)
        if code:
            raise subprocess.CalledProcessError(code, command)
        return ""
    result = subprocess.run(command, cwd=root, env=environment,
        text=True, capture_output=capture, check=True)
    return result.stdout if capture else ""


def run_bootstrap(root, skip_build=False):
    root = Path(root).resolve()
    subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True,
                   stdout=subprocess.DEVNULL)
    if not (root / ".env").exists():
        existing = subprocess.run(["docker", "volume", "ls", "--filter",
            "label=com.docker.compose.project=" + project_name(root), "--format", "{{.Name}}"],
            check=True, text=True, capture_output=True)
        if existing.stdout.strip():
            raise ValueError("Existen volúmenes del proyecto pero falta .env. Recuperar la configuración privada del respaldo.")
    values = prepare_configuration(root)
    compose(root, values, "config", "--quiet")
    if not skip_build:
        compose(root, values, "build", "app-check", "jobs")
    compose(root, values, "up", "--detach", "--wait", "--wait-timeout", "120", "postgres", "qdrant")
    # PostgreSQL only runs initdb scripts on an empty volume. Reconcile here too
    # so an interrupted first provisioning can resume without discarding data.
    compose(root, values, "exec", "-T", "postgres", "bash", "/docker-entrypoint-initdb.d/10-databases.sh")
    compose(root, values, "run", "--rm", "--no-deps", "-T", "jobs", "hmrec", "db", "migrate")
    compose(root, values, "run", "--rm", "--no-deps", "-T", "app-check")
    compose(root, values, "run", "--rm", "--no-deps", "-T", "jobs", "hmrec", "check")
    print("Bootstrap terminado. Datos y configuración existentes conservados.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    setup = sub.add_parser("bootstrap", help="Preparar y comprobar la infraestructura base")
    setup.add_argument("--skip-build", action="store_true", help="Usar imágenes construidas previamente")
    sub.add_parser("check", help="Comprobar dependencias y servicios reales")
    sub.add_parser("down", help="Detener conservando volúmenes")
    sub.add_parser("status", help="Mostrar los contenedores de este proyecto")
    verify = sub.add_parser("verify", help="Ejecutar pruebas de la entrega disponible")
    verify.add_argument("--suite", choices=("bootstrap", "unit", "data", "model", "diagnostics"), required=True)
    download = sub.add_parser("download-data", help="Descargar H&M desde Kaggle con credencial propia del host")
    download.add_argument("--check-access", action="store_true")
    validate = sub.add_parser("validate-data", help="Verificar el ZIP completo o sólo estructura/tamaño")
    validate.add_argument("--mode", choices=("quick", "full"), default="full")
    prepare = sub.add_parser("prepare-data", help="T2 en primer plano, para desarrollo; usar data-job para el reporte portable")
    prepare.add_argument("--dataset", choices=("hm", "synthetic"), default="hm")
    job = sub.add_parser("data-job", help="Iniciar, consultar, reanudar o exportar T2 desacoplado de la terminal")
    job.add_argument("job_args", nargs=argparse.REMAINDER)
    training = sub.add_parser("train-job", help="Entrenamiento T3 desacoplado, checkpoint y reporte por GitHub")
    training.add_argument("job_args", nargs=argparse.REMAINDER)
    diagnostic = sub.add_parser("diagnose-job", help="Diagnóstico T3 en lectura, sobre un checkpoint existente")
    diagnostic.add_argument("job_args", nargs=argparse.REMAINDER)
    tracking = sub.add_parser("tracking-up", help="Arrancar MLflow local; --port se conserva en .env")
    tracking.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        if args.action == "diagnose-job":
            from diagnose_job import main as diagnose_job_main
            return diagnose_job_main(args.job_args)
        if args.action == "train-job":
            from train_job import main as train_job_main
            return train_job_main(args.job_args)
        if args.action == "data-job":
            from data_job import main as data_job_main
            return data_job_main(args.job_args)
        if args.action == "bootstrap":
            run_bootstrap(ROOT, skip_build=args.skip_build)
            return 0
        values = read_configuration(ROOT / ".env")
        if args.action == "tracking-up":
            if args.port is not None:
                if not 1024 <= args.port <= 65535:
                    raise ValueError("Puerto fuera de rango")
                import re
                env_path = ROOT / '.env'
                text = re.sub(r'^MLFLOW_PORT=.*$', 'MLFLOW_PORT=' + str(args.port), env_path.read_text(), flags=re.MULTILINE)
                temporary = env_path.with_suffix('.env.partial')
                temporary.write_text(text); temporary.chmod(0o600); temporary.replace(env_path)
                values = read_configuration(env_path)
            compose(ROOT, values, "build", "jobs")
            compose(ROOT, values, "up", "--detach", "--wait", "--wait-timeout", "240", "mlflow")
            print("MLflow: http://127.0.0.1:" + values['MLFLOW_PORT'])
        elif args.action == "download-data":
            sys.path.insert(0, str(ROOT / "src"))
            from hm_recommender.data.download import download_archive, probe_access
            from hm_recommender.data.validate import COMPETITION
            path = Path(values["DATA_DIR"])
            if not path.is_absolute():
                path = ROOT / path
            result = probe_access() if args.check_access else str(download_archive(path / "kaggle_hm" / (COMPETITION + ".zip")))
            print(json.dumps(result))
        elif args.action == "validate-data":
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "hmrec", "data", "validate",
                    "--source", "/data/kaggle_hm/h-and-m-personalized-fashion-recommendations.zip", "--mode", args.mode)
        elif args.action == "prepare-data":
            config = "hm" if args.dataset == "hm" else "demo"
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "hmrec", "data", "pipeline",
                    "--config", "/opt/hmrec/configs/" + config + ".yaml", "--run-dir", "/artifacts/data-development/" + config)
        elif args.action == "check":
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "hmrec", "check")
        elif args.action == "down":
            compose(ROOT, values, "down")
        elif args.action == "status":
            compose(ROOT, values, "ps")
        elif args.action == "verify":
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest", "tests/unit", "-q")
            if args.suite == "data":
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_ingest.py", "tests/integration/test_data_pipeline.py", "-q")
            if args.suite == "diagnostics":
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_diagnostics.py", "tests/integration/test_epoch_comparison.py", "tests/integration/test_recent_history.py", "-q")
            if args.suite == "model":
                compose(ROOT, values, "up", "--detach", "--wait", "--wait-timeout", "240", "mlflow")
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_training.py", "-q")
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "-e", "HMREC_TRACKING_TESTS=1",
                        "-e", "MLFLOW_TRACKING_URI=http://mlflow:5000", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_tracking.py", "-q")
            if args.suite in ("bootstrap", "data", "model", "diagnostics"):
                subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests/host"), "-v"],
                               cwd=ROOT, check=True)
            if args.suite == "bootstrap":
                environment = dict(os.environ, HMREC_DOCKER_TESTS="1")
                subprocess.run([sys.executable, str(ROOT / "tests/integration/test_bootstrap.py"), "-v"],
                               cwd=ROOT, env=environment, check=True)
            print(json.dumps({"suite": args.suite, "status": "passed"}))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        message = str(error)
        if (ROOT / ".env").exists():
            try:
                for key, value in read_configuration(ROOT / ".env").items():
                    if key in PASSWORD_KEYS:
                        message = message.replace(value, "[REDACTED]")
            except ValueError:
                pass
        print("ERROR: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
