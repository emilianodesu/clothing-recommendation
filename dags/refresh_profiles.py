"""Catch up through one bounded receipt window, never reseed or advance event time."""
from datetime import datetime, timedelta, timezone
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator

with DAG('hm_refresh_profiles',start_date=datetime(2026,1,1,tzinfo=timezone.utc),schedule='*/5 * * * *',
         catchup=False,max_active_runs=1,is_paused_upon_creation=True,tags=['hm','profiles'],
         default_args={'retries':1,'retry_delay':timedelta(minutes=1)}) as dag:
    refresh=BashOperator(task_id='refresh',bash_command='/opt/hmrec/.venv/bin/hmrec pipeline refresh',
                         execution_timeout=timedelta(minutes=30),pool='default_pool',do_xcom_push=False)
