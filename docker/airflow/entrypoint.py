"""Compose-only Airflow configuration; secrets are never passed as CLI arguments."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

os.umask(0o077)
home=Path(os.environ['AIRFLOW_HOME']);home.mkdir(parents=True,exist_ok=True)
secret=os.environ['HM_AIRFLOW_PASSWORD'].encode()
def derive(purpose):return hmac.new(secret,purpose.encode(),hashlib.sha256).digest()
passwords=home/'simple_auth_manager_passwords.json'
if not passwords.exists():
    try:
        with passwords.open('x') as f:json.dump({'admin':derive('local-ui-password').hex()},f)
    except FileExistsError:pass
os.environ.update({
 'AIRFLOW__DATABASE__SQL_ALCHEMY_CONN':'postgresql+psycopg2://hm_airflow:'+os.environ['HM_AIRFLOW_PASSWORD']+'@postgres:5432/hm_airflow',
 'AIRFLOW__CORE__FERNET_KEY':base64.urlsafe_b64encode(derive('fernet')).decode(),
 'AIRFLOW__API_AUTH__JWT_SECRET':derive('execution-jwt').hex(),
 'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS':'admin:admin',
 'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE':str(passwords),
})
os.execv('/opt/airflow-env/bin/airflow',['airflow',*sys.argv[1:]])
