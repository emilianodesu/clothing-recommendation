"""Private local configuration. Host dependency: Python standard library only."""
import hashlib
import os
from pathlib import Path
import re
import secrets
import shlex
import ssl

PASSWORD_KEYS = ("POSTGRES_PASSWORD", "HM_APP_PASSWORD", "HM_AIRFLOW_PASSWORD", "HM_MLFLOW_PASSWORD")
REQUIRED = (*PASSWORD_KEYS, "COMPOSE_PROJECT_NAME", "HMREC_UID", "HMREC_GID", "DATA_DIR", "ARTIFACTS_DIR",
            "API_PORT", "UI_PORT", "MLFLOW_PORT", "AIRFLOW_PORT")


def project_name(root: Path) -> str:
    return "hmrec_" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def read_configuration(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise ValueError("Formato inválido en .env; usar CLAVE=valor.")
        key = key.strip()
        if key in values:
            raise ValueError("Variable duplicada en .env: " + key)
        raw = raw.strip()
        if raw.startswith(("'", '"')):
            parts = shlex.split(raw, comments=True)
            if len(parts) != 1:
                raise ValueError("Valor inválido en .env: " + key)
            raw = parts[0]
        values[key] = raw
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise ValueError("Configuración existente incompleta; no se modifica. Faltan: " + ", ".join(missing))
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", values["COMPOSE_PROJECT_NAME"]):
        raise ValueError("COMPOSE_PROJECT_NAME inválido.")
    for key in PASSWORD_KEYS:
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", values[key]) or values[key] == "GENERATED_BY_BOOTSTRAP":
            raise ValueError("Usar una contraseña generada de 24–128 caracteres alfanuméricos para " + key)
    for key in ("HMREC_UID", "HMREC_GID"):
        if not values[key].isdigit() or int(values[key]) < 1:
            raise ValueError(key + " debe identificar un usuario/grupo sin privilegios.")
    for key in ("API_PORT", "UI_PORT", "MLFLOW_PORT", "AIRFLOW_PORT"):
        if not values[key].isdigit() or not 1024 <= int(values[key]) <= 65535:
            raise ValueError(key + " debe ser un puerto entre 1024 y 65535.")
    return values


def prepare_configuration(project_root: Path) -> dict:
    root = project_root.resolve()
    path = root / ".env"
    if path.is_symlink():
        raise ValueError(".env debe ser un archivo local, no un enlace simbólico.")
    if not path.exists():
        values = {"COMPOSE_PROJECT_NAME": project_name(root),
                  "HMREC_UID": str(os.getuid() or 10001), "HMREC_GID": str(os.getgid() or 10001),
                  "DATA_DIR": "./data", "ARTIFACTS_DIR": "./artifacts", "API_PORT": "8000",
                  "UI_PORT": "8501", "MLFLOW_PORT": "5000", "AIRFLOW_PORT": "8080"}
        values.update({key: secrets.token_hex(24) for key in PASSWORD_KEYS})
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w") as stream:
                stream.write("# Configuración privada generada localmente. No versionar.\n")
                stream.writelines(key + "=" + value + "\n" for key, value in values.items())
    values = read_configuration(path)
    os.chmod(path, 0o600)
    for key in ("DATA_DIR", "ARTIFACTS_DIR"):
        directory = Path(values[key]).expanduser()
        if not directory.is_absolute():
            directory = root / directory
        directory.mkdir(parents=True, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise ValueError("No se puede escribir en " + key)
    for name in ("backups", ".runtime"):
        (root / name).mkdir(exist_ok=True)
    # Only existing host trust, mounted as a build secret and never baked into images.
    certificate_path = root / ".runtime/build-ca.pem"
    certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not certificates:
        raise ValueError("No hay certificados CA confiados en Python; configurar SSL_CERT_FILE en el host.")
    descriptor = os.open(certificate_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates))
    return values


def bootstrap(project_root: Path) -> None:
    # Import lazily: configuration tests do not require Docker.
    from project import run_bootstrap
    run_bootstrap(project_root, skip_build=False)
