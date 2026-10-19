#!/usr/bin/env bash
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname postgres -v ON_ERROR_STOP=1 \
  --set=app_password="$HM_APP_PASSWORD" \
  --set=airflow_password="$HM_AIRFLOW_PASSWORD" \
  --set=mlflow_password="$HM_MLFLOW_PASSWORD" <<'SQL'
CREATE ROLE hm_app LOGIN PASSWORD :'app_password';
CREATE ROLE hm_airflow LOGIN PASSWORD :'airflow_password';
CREATE ROLE hm_mlflow LOGIN PASSWORD :'mlflow_password';
CREATE DATABASE hm_app OWNER hm_app;
CREATE DATABASE hm_airflow OWNER hm_airflow;
CREATE DATABASE hm_mlflow OWNER hm_mlflow;
REVOKE CONNECT ON DATABASE hm_app FROM PUBLIC;
REVOKE CONNECT ON DATABASE hm_airflow FROM PUBLIC;
REVOKE CONNECT ON DATABASE hm_mlflow FROM PUBLIC;
GRANT CONNECT ON DATABASE hm_app TO hm_app;
GRANT CONNECT ON DATABASE hm_airflow TO hm_airflow;
GRANT CONNECT ON DATABASE hm_mlflow TO hm_mlflow;
SQL
