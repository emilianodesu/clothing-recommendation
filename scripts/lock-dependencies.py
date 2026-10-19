#!/usr/bin/env python3
"""Maintainer operation: regenerate both locks using pinned Linux Python and uv."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from bootstrap import prepare_configuration

ROOT = Path(__file__).resolve().parents[1]


def main():
    prepare_configuration(ROOT)
    subprocess.run(["docker", "build", "--file", str(ROOT / "docker/lock.Dockerfile"),
                    "--tag", "hmrec-lock:0.13.1", str(ROOT)], check=True)
    with tempfile.TemporaryDirectory(prefix="lock-", dir=ROOT / ".runtime") as directory:
        work = Path(directory)
        shutil.copy2(ROOT / "pyproject.toml", work / "pyproject.toml")
        if (ROOT / "uv.lock").exists():
            shutil.copy2(ROOT / "uv.lock", work / "uv.lock")
        for name in ("requirements.in", "constraints-3.12.txt"):
            shutil.copy2(ROOT / "docker/airflow" / name, work / name)
        command = ["docker", "run", "--rm", "--mount", f"type=bind,source={work},target=/work",
            "--mount", f"type=bind,source={ROOT / '.runtime/build-ca.pem'},target=/run/ca.pem,readonly",
            "--env", "SSL_CERT_FILE=/run/ca.pem", "hmrec-lock:0.13.1"]
        subprocess.run(command + ["lock", "--python", "/usr/local/bin/python"], check=True)
        subprocess.run(command + ["pip", "compile", "pyproject.toml", "requirements.in",
            "--group", "build", "--extra", "tracking", "--constraint", "constraints-3.12.txt",
            "--universal", "--python-version", "3.12", "--generate-hashes",
            "--output-file", "requirements.lock", "--quiet"], check=True)
        # Replace only after BOTH resolutions succeed.
        shutil.copy2(work / "uv.lock", ROOT / "uv.lock")
        shutil.copy2(work / "requirements.lock", ROOT / "docker/airflow/requirements.lock")
    print("Locks actualizados. Reconstruir imágenes y ejecutar verify antes de aceptar cambios.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("No se pudieron actualizar los locks: " + str(error), file=sys.stderr)
        sys.exit(1)
