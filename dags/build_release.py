"""Manual H build; activation remains an explicit local configuration decision."""
from datetime import datetime, timedelta, timezone
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator

with DAG('hm_build_release',start_date=datetime(2026,1,1,tzinfo=timezone.utc),schedule=None,
         catchup=False,max_active_runs=1,default_args={'retries':1,'retry_delay':timedelta(minutes=1)},
         is_paused_upon_creation=True,tags=['hm','manual']) as dag:
    build=BashOperator(task_id='build',bash_command='/opt/hmrec/.venv/bin/hmrec pipeline build --config /artifacts/pipeline/build.json',
                       execution_timeout=timedelta(hours=2),pool='default_pool',do_xcom_push=False,append_env=True,env={'HMREC_ORCHESTRATOR_RUN_ID':'{{ run_id }}'})
    publish=BashOperator(task_id='publish',bash_command='/opt/hmrec/.venv/bin/hmrec pipeline publish --config /artifacts/pipeline/build.json',
                         execution_timeout=timedelta(minutes=10),pool='default_pool',do_xcom_push=False,append_env=True,env={'HMREC_ORCHESTRATOR_RUN_ID':'{{ run_id }}'})
    build >> publish
