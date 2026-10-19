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


def compose(root, values, *args, capture=False):
    environment = dict(os.environ)
    environment.pop("COMPOSE_FILE", None)
    environment.pop("COMPOSE_PROFILES", None)
    environment.update(values)
    result = subprocess.run(["docker", "compose", "--project-directory", str(root), "--env-file",
        str(root / ".env"), "--file", str(root / "compose.yaml"), "--project-name",
        values["COMPOSE_PROJECT_NAME"], *args], cwd=root, env=environment,
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
    verify.add_argument("--suite", choices=("bootstrap", "unit"), required=True)
    args = parser.parse_args()
    try:
        if args.action == "bootstrap":
            run_bootstrap(ROOT, skip_build=args.skip_build)
            return 0
        values = read_configuration(ROOT / ".env")
        if args.action == "check":
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "hmrec", "check")
        elif args.action == "down":
            compose(ROOT, values, "down")
        elif args.action == "status":
            compose(ROOT, values, "ps")
        elif args.action == "verify":
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest", "tests/unit", "-q")
            if args.suite == "bootstrap":
                subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests/host"), "-v"],
                               cwd=ROOT, check=True)
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
