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


def compose(root, values, *args, capture=False, log_path=None, timeout=None):
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
        text=True, capture_output=capture, check=True, timeout=timeout)
    return result.stdout if capture else ""


def verification_postgres(root, values, *, existing_only=False):
    if existing_only:
        cid = compose(root, values, "ps", "--status", "running", "-q", "postgres", capture=True, timeout=30).strip()
        if not cid:
            raise ValueError("PostgreSQL debe estar activo; no se arranca ni recrea en este modo.")
    else:
        compose(root, values, "up", "-d", "--wait", "postgres")


def prepare_start_configuration(root):
    if not (root / ".env").exists():
        existing = subprocess.run(["docker", "volume", "ls", "--filter",
            "label=com.docker.compose.project=" + project_name(root), "--format", "{{.Name}}"],
            check=True, text=True, capture_output=True)
        if existing.stdout.strip():
            raise ValueError("Existen volúmenes del proyecto pero falta .env. Recuperar la configuración privada del respaldo.")
    return prepare_configuration(root)


def run_bootstrap(root, skip_build=False):
    root = Path(root).resolve()
    subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True,
                   stdout=subprocess.DEVNULL)
    values = prepare_start_configuration(root)
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
    for command in ('backup','restore'):
        item=sub.add_parser(command);item.add_argument('arguments',nargs=argparse.REMAINDER)
    check_pipeline=sub.add_parser('pipeline-check')
    check_pipeline.add_argument('--machine-id',required=True)
    check_pipeline.add_argument('--expected-commit',required=True)
    check_pipeline.add_argument('--source-run-id')
    pipeline=sub.add_parser('pipeline',help='T6: explicit Airflow lifecycle; preserve existing services')
    pipeline.add_argument('pipeline_args',nargs=argparse.REMAINDER)
    clock = sub.add_parser('clock-check', help='T5: diagnóstico de relojes/energía en lectura; no ejecuta Docker')
    clock.add_argument('--machine-id', required=True)
    clock.add_argument('--expected-commit', required=True)
    clock.add_argument('--source-report', required=True)
    setup = sub.add_parser("bootstrap", help="Preparar y comprobar la infraestructura base")
    setup.add_argument("--skip-build", action="store_true", help="Usar imágenes construidas previamente")
    sub.add_parser("check", help="Comprobar dependencias y servicios reales")
    sub.add_parser("down", help="Detener conservando volúmenes")
    sub.add_parser("status", help="Mostrar los contenedores de este proyecto")
    verify = sub.add_parser("verify", help="Ejecutar pruebas de la entrega disponible")
    verify.add_argument("--suite", choices=("bootstrap", "unit", "data", "model", "diagnostics", "quality", "releases", "serving", "events", "pipeline", "portability"), required=True)
    verify.add_argument("--existing-services", action="store_true", help="Exigir PostgreSQL activo sin arrancarlo ni recrearlo (serving/events)")
    demo=sub.add_parser('demo',help='T5: preparar demo sintética, interfaz y reporte cerrado')
    demo.add_argument('--skip-build',action='store_true')
    demo.add_argument('--machine-id',default='local')
    demo_mode=demo.add_mutually_exclusive_group()
    demo_mode.add_argument('--memory-diagnostics',action='store_true')
    demo_mode.add_argument('--operational',action='store_true')
    events_check=sub.add_parser('events-check',help='T5 sobre un T4-A existente; sin entrenamiento')
    events_check.add_argument('--machine-id',required=True)
    events_check.add_argument('--expected-commit',required=True)
    events_check.add_argument('--source-run-id',required=True)
    events_mode=events_check.add_mutually_exclusive_group()
    events_mode.add_argument('--memory-diagnostics',action='store_true')
    events_mode.add_argument('--operational',action='store_true')
    up = sub.add_parser('up', help='Arrancar el perfil de servicio local')
    up.add_argument('--profile', choices=('serve',), required=True)
    up.add_argument('--skip-build', action='store_true')
    prepared = sub.add_parser('prepare-release', help='Verificar y copiar un paquete local, sin activarlo')
    prepared.add_argument('--directory', required=True, help='Ruta relativa dentro de ARTIFACTS_DIR')
    active = sub.add_parser('activate-release', help='Activar una versión ya preparada')
    active.add_argument('--release-id', required=True)
    sub.add_parser('rollback-release', help='Volver a la versión anterior')
    serving_check = sub.add_parser('serving-check', help='T4-B: HTTP, reinicio, rollback y reporte cerrado')
    serving_check.add_argument('--machine-id', required=True)
    serving_check.add_argument('--expected-commit', required=True)
    serving_check.add_argument('--reference-run-id', required=True)
    serving_check.add_argument('--candidate-run-id', required=True)
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
    release = sub.add_parser("release-job", help="T4-A: paquete H y paridad offline")
    release.add_argument("job_args", nargs=argparse.REMAINDER)
    quality = sub.add_parser("quality-job", help="Q1–Q4: evaluación temporal, ranking y selección reproducible")
    quality.add_argument("job_args", nargs=argparse.REMAINDER)
    diagnostic = sub.add_parser("diagnose-job", help="Diagnóstico T3 en lectura, sobre un checkpoint existente")
    diagnostic.add_argument("job_args", nargs=argparse.REMAINDER)
    tracking = sub.add_parser("tracking-up", help="Arrancar MLflow local; --port se conserva en .env")
    tracking.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.action=='pipeline-check':
        from pipeline_check import main as check_pipeline_main
        return check_pipeline_main(['--machine-id',args.machine_id,'--expected-commit',args.expected_commit]+(['--source-run-id',args.source_run_id] if args.source_run_id else []))
    if args.action=='pipeline':
        from pipeline_control import main as pipeline_main
        return pipeline_main(args.pipeline_args)
    try:
        if args.action == 'clock-check':
            from clock_check import main as check_clock
            return check_clock(['--machine-id',args.machine_id,'--expected-commit',args.expected_commit,
                                '--source-report',args.source_report])
        if args.action=='events-check':
            if args.operational:
                from operational_check import run
                import re
                if not re.fullmatch('[a-z][a-z0-9_-]{0,31}',args.machine_id) or not re.fullmatch('[a-f0-9]{40}',args.expected_commit):
                    parser.error('Machine alias and full expected SHA required')
                return run(ROOT,args.machine_id,args.expected_commit,args.source_run_id)
            from events_check import main as check_events
            return check_events(['--machine-id',args.machine_id,'--expected-commit',args.expected_commit,'--source-run-id',args.source_run_id]+(['--memory-diagnostics'] if args.memory_diagnostics else []))
        if args.action == 'serving-check':
            from serving_check import main as check_serving
            return check_serving(['--machine-id', args.machine_id,
                '--expected-commit', args.expected_commit,
                '--reference-run-id', args.reference_run_id,
                '--candidate-run-id', args.candidate_run_id])
        if args.action == "release-job":
            from release_job import main as release_main
            return release_main(args.job_args)
        if args.action == "quality-job":
            from quality_job import main as quality_main
            return quality_main(args.job_args)
        if args.action == "diagnose-job":
            from diagnose_job import main as diagnose_job_main
            return diagnose_job_main(args.job_args)
        if args.action == "train-job":
            from train_job import main as train_job_main
            return train_job_main(args.job_args)
        if args.action == "data-job":
            from data_job import main as data_job_main
            return data_job_main(args.job_args)
        if args.action in ('backup','restore'):
            return __import__(args.action).main(args.arguments)
        elif args.action == "bootstrap":
            run_bootstrap(ROOT, skip_build=args.skip_build)
            return 0
        if args.action=='demo':
            import re
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',args.machine_id):raise ValueError('Invalid machine alias')
            if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():raise ValueError('Demo requires a clean committed checkout')
            values=prepare_start_configuration(ROOT)
        else:values = read_configuration(ROOT / ".env")
        if args.action in ('up','demo'):
            artifacts = Path(values['ARTIFACTS_DIR'])
            if not artifacts.is_absolute(): artifacts = ROOT/artifacts
            (artifacts/'serving').mkdir(exist_ok=True)
            if not args.skip_build: compose(ROOT, values, 'build', 'app-check', 'jobs')
            compose(ROOT, values, 'up', '-d', '--wait', '--wait-timeout', '120', 'postgres')
            compose(ROOT, values, 'exec', '-T', 'postgres', 'bash', '/docker-entrypoint-initdb.d/10-databases.sh')
            compose(ROOT, values, 'run', '--rm', '--no-deps', '-T', 'jobs', 'hmrec', 'db', 'migrate')
            compose(ROOT, values, '--profile', 'serve', 'up', '-d', '--wait', '--wait-timeout', '300', 'api','ui')
            print('API iniciada: http://127.0.0.1:'+values['API_PORT']+'; readiness requiere una versión activa.')
            print('Interfaz: http://127.0.0.1:'+values['UI_PORT'])
            if args.action=='demo':
                from events_check import run as run_events
                commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
                if args.operational:
                    from operational_check import run as run_operational
                    return run_operational(ROOT,args.machine_id,commit)
                return run_events(ROOT,args.machine_id,commit,**({'memory_diagnostics':True} if args.memory_diagnostics else {}))
        elif args.action in ('prepare-release','activate-release','rollback-release'):
            extra = ['prepare','--directory',args.directory] if args.action=='prepare-release' else (
                ['activate','--release-id',args.release_id] if args.action=='activate-release' else ['rollback'])
            compose(ROOT, values, 'exec', '-T', 'api', 'hmrec', 'serving', *extra)
        elif args.action == "tracking-up":
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
            compose(ROOT, values, "--profile", "*", "down")
        elif args.action == "status":
            compose(ROOT, values, "ps")
        elif args.action == "verify":
            if args.existing_services and args.suite not in ("serving", "events", "pipeline", "portability"):
                raise ValueError("--existing-services sólo aplica a serving/events")
            compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest", "tests/unit", "-q")
            if args.suite == "data":
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_ingest.py", "tests/integration/test_data_pipeline.py", "-q")
            if args.suite == "releases":
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_release_parity.py", "tests/integration/test_quality_folds.py",
                        "tests/integration/test_recent_history.py", "-q")
            if args.suite in ('serving','events','pipeline'):
                verification_postgres(ROOT, values, existing_only=args.existing_services)
                compose(ROOT, values, 'run', '--rm', '--no-deps', '-T', '-e', 'HMREC_SERVING_TESTS=1', 'jobs',
                    'python', '-m', 'pytest', 'tests/integration/test_recommendations.py',
                    'tests/integration/test_release_activation.py', 'tests/integration/test_serving_http.py', '-q')
                subprocess.run([sys.executable, str(ROOT/'tests/integration/test_serving_publication.py'), '-v'],
                    cwd=ROOT, env=dict(os.environ, HMREC_DOCKER_TESTS='1'), check=True)
            if args.suite in ('events','pipeline'):
                compose(ROOT,values,'run','--rm','--no-deps','-T','-e','HMREC_SERVING_TESTS=1','jobs',
                    'python','-m','pytest','tests/integration/test_events.py','tests/integration/test_demo.py','tests/integration/test_ui.py','-q')
            if args.suite=='portability':
                verification_postgres(ROOT,values,existing_only=args.existing_services)
                compose(ROOT,values,'run','--rm','--no-deps','-T','-e','HMREC_SERVING_TESTS=1','jobs','python','-m','pytest','tests/integration/test_release_transfer.py','tests/integration/test_backup_restore.py','-q')
            if args.suite=='pipeline':
                compose(ROOT,values,'run','--rm','--no-deps','-T','-e','HMREC_SERVING_TESTS=1','jobs',
                    'python','-m','pytest','tests/integration/test_pipeline.py','-q')
                compose(ROOT,values,'run','--rm','--no-deps','-T','--entrypoint','/opt/airflow-env/bin/python','airflow-scheduler',
                    '-c',"import sys;sys.path.insert(0,'/opt/airflow/dags');import build_release,refresh_profiles;assert len(build_release.dag.tasks)==2;assert refresh_profiles.dag.catchup is False;print('DAG imports passed')")
            if args.suite == "quality":
                compose(ROOT, values, "run", "--rm", "--no-deps", "-T", "jobs", "python", "-m", "pytest",
                        "tests/integration/test_quality_folds.py", "tests/integration/test_quality_worker.py", "tests/integration/test_graph_quality.py", "tests/integration/test_ranker_quality.py", "tests/integration/test_ranker_worker.py", "tests/integration/test_quality_campaign.py", "-q")
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
            if args.suite in ("bootstrap", "data", "model", "diagnostics", "quality", "releases", "serving", "events", "pipeline", "portability"):
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
