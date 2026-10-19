"""Application database connection and transactional, repeatable migrations."""
import hashlib
import os
from pathlib import Path

import psycopg


def connect(service: str = "app"):
    if service not in ("app", "airflow", "mlflow"):
        raise ValueError("Unknown database service")
    return psycopg.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=5432,
        dbname="hm_" + service, user="hm_" + service,
        password=os.environ["HM_" + service.upper() + "_PASSWORD"], connect_timeout=5)


def migrate(directory: Path) -> list[str]:
    applied = []
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise ValueError("No migration files found")
    with connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(784219)")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        previous = dict(connection.execute("SELECT name, sha256 FROM schema_migrations").fetchall())
        for file in files:
            body = file.read_bytes()
            checksum = hashlib.sha256(body).hexdigest()
            if file.name in previous:
                if previous[file.name] != checksum:
                    raise ValueError("Applied migration was modified: " + file.name)
                continue
            connection.execute(body.decode())
            connection.execute("INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)", (file.name, checksum))
            applied.append(file.name)
    return applied
