#!/usr/bin/env bash
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname postgres -v ON_ERROR_STOP=1 \
  --set=app_password="$HM_APP_PASSWORD" \
  --set=airflow_password="$HM_AIRFLOW_PASSWORD" \
  --set=mlflow_password="$HM_MLFLOW_PASSWORD" <<'SQL'
-- One session lock also protects overlapping bootstrap invocations.
SELECT pg_advisory_lock(784218);
SELECT 'CREATE ROLE hm_app LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hm_app') \gexec
SELECT 'CREATE ROLE hm_airflow LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hm_airflow') \gexec
SELECT 'CREATE ROLE hm_mlflow LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hm_mlflow') \gexec
ALTER ROLE hm_app LOGIN PASSWORD :'app_password';
ALTER ROLE hm_airflow LOGIN PASSWORD :'airflow_password';
ALTER ROLE hm_mlflow LOGIN PASSWORD :'mlflow_password';
SELECT 'CREATE DATABASE hm_app OWNER hm_app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hm_app') \gexec
SELECT 'CREATE DATABASE hm_airflow OWNER hm_airflow' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hm_airflow') \gexec
SELECT 'CREATE DATABASE hm_mlflow OWNER hm_mlflow' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hm_mlflow') \gexec
ALTER DATABASE hm_app OWNER TO hm_app;
ALTER DATABASE hm_airflow OWNER TO hm_airflow;
ALTER DATABASE hm_mlflow OWNER TO hm_mlflow;
REVOKE CONNECT ON DATABASE hm_app FROM PUBLIC;
REVOKE CONNECT ON DATABASE hm_airflow FROM PUBLIC;
REVOKE CONNECT ON DATABASE hm_mlflow FROM PUBLIC;
GRANT CONNECT ON DATABASE hm_app TO hm_app;
GRANT CONNECT ON DATABASE hm_airflow TO hm_airflow;
GRANT CONNECT ON DATABASE hm_mlflow TO hm_mlflow;
SELECT pg_advisory_unlock(784218);
SQL
