"""Run from the host: real containers, isolated project, synthetic records only."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import project as project_runner


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigurationTests(unittest.TestCase):
    def test_repeated_configuration_preserves_private_values_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = load_bootstrap()
            module.prepare_configuration(root)
            original = (root / ".env").read_bytes()
            (root / "data/keep.txt").write_text("existing input")
            module.prepare_configuration(root)
            self.assertEqual((root / ".env").read_bytes(), original)
            self.assertEqual((root / "data/keep.txt").read_text(), "existing input")
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)

    def test_incomplete_existing_configuration_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b"COMPOSE_PROJECT_NAME=my-existing-project\n"
            (root / ".env").write_bytes(original)
            with self.assertRaises(ValueError):
                load_bootstrap().prepare_configuration(root)
            self.assertEqual((root / ".env").read_bytes(), original)


class ComposeIsolationTests(unittest.TestCase):
    def test_every_test_helper_ignores_inherited_compose_configuration(self):
        # Intercept process creation: a failing regression must never reach Docker.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = load_bootstrap().prepare_configuration(root)
            instance = BootstrapIntegrationTests()
            result = subprocess.CompletedProcess([], 0, "cross-database access rejected\n", "")
            inherited = {
                "COMPOSE_PROJECT_NAME": "unrelated-installation",
                "COMPOSE_FILE": "/unrelated/compose.yaml",
                "COMPOSE_PROFILES": "unrelated",
                "HM_APP_PASSWORD": "unrelated-password",
                "DATA_DIR": "/unrelated/data",
            }
            with mock.patch.dict(os.environ, inherited), \
                 mock.patch.object(BootstrapIntegrationTests, "root", root, create=True), \
                 mock.patch.object(BootstrapIntegrationTests, "temp", mock.Mock(), create=True), \
                 mock.patch.object(instance, "project"), \
                 mock.patch("subprocess.run", return_value=result) as run:
                instance.sql("SELECT 1")
                instance.test_database_roles_are_isolated()
                BootstrapIntegrationTests.tearDownClass()
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                command = call.args[0]
                with self.subTest(command=command[-3:]):
                    self.assertIn("--project-name", command)
                    self.assertEqual(command[command.index("--project-name") + 1],
                                     values["COMPOSE_PROJECT_NAME"])
                    environment = call.kwargs["env"]
                    self.assertEqual(environment["HM_APP_PASSWORD"], values["HM_APP_PASSWORD"])
                    self.assertEqual(environment["DATA_DIR"], values["DATA_DIR"])
                    self.assertNotIn("COMPOSE_FILE", environment)
                    self.assertNotIn("COMPOSE_PROFILES", environment)


@unittest.skipUnless(os.environ.get("HMREC_DOCKER_TESTS") == "1", "host Docker integration only")
class DataJobResourceIntegrationTests(unittest.TestCase):
    def test_nondefault_budget_runs_and_exports_with_real_docker(self):
        """Use the already-built jobs image; only Git identity/build are fixtures."""
        import data_job
        from handoff import local_environment, read_report
        location = ROOT / '.runtime/tests'
        location.mkdir(parents=True, exist_ok=True)
        profile = os.environ.get('HMREC_TEST_COLIMA_PROFILE', 'default')
        env = local_environment(profile)
        actual_capture = data_job.capture
        containers = []
        with tempfile.TemporaryDirectory(prefix='resource fixture ', dir=location) as directory:
            root = Path(directory)
            load_bootstrap().prepare_configuration(root)
            (root / 'configs/resources').mkdir(parents=True)
            shutil.copy2(ROOT / 'configs/demo.yaml', root / 'configs/demo.yaml')
            shutil.copy2(ROOT / 'uv.lock', root / 'uv.lock')
            (root / 'configs/resources/small.json').write_text(json.dumps({
                'schema_version': 1, 'colima': {'memory_gib': 4, 'cpus': 2},
                'job': {'memory_gib': 2, 'cpus': 1, 'threads': 1}}))

            def capture(environment, *command, **kwargs):
                # This temporary directory is a synthetic fixture, not a code checkout.
                if command[:2] == ('git', 'rev-parse'): return 'f' * 40
                if command[:2] == ('git', 'status'): return ''
                output = actual_capture(environment, *command, **kwargs)
                if command[:2] == ('docker', 'create'): containers.append(output)
                return output

            try:
                with mock.patch.object(data_job, 'ROOT', root), \
                     mock.patch.object(data_job, 'capture', side_effect=capture), \
                     mock.patch.object(data_job, 'compose'):
                    run_id = data_job.start('resource-fixture', profile, 'synthetic', resource_profile='small')
                    deadline = time.monotonic() + 90
                    while True:
                        _, run, _, state, result, status = data_job.inspect_run(run_id)
                        if status != 'running': break
                        if time.monotonic() > deadline: self.fail('Synthetic resource job did not finish in 90 seconds')
                        time.sleep(0.5)
                    self.assertEqual(status, 'passed', result)
                    self.assertEqual(run['effective_limits']['memory_bytes'], 2 * 2**30)
                    self.assertEqual(run['effective_limits']['nano_cpus'], 1000000000)
                    self.assertEqual(run['effective_limits']['polars_threads'], 1)
                    report = read_report(data_job.export(run_id))
                    self.assertEqual(report['status'], 'passed')
                    self.assertEqual(report['resources']['name'], 'small')
                    self.assertEqual(report['effective_limits']['memory_bytes'], 2 * 2**30)
            finally:
                for container in containers:
                    subprocess.run(['docker', 'rm', '--force', container], env=env, check=True,
                                   stdout=subprocess.DEVNULL)


@unittest.skipUnless(os.environ.get("HMREC_DOCKER_TESTS") == "1", "host Docker integration only")
class BootstrapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Under the project so Colima's existing shared mount includes the copy.
        location = ROOT / ".runtime/tests"
        location.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="another project with spaces ", dir=location)
        cls.root = Path(cls.temp.name)
        for name in ("scripts", "docker", "src", "configs", "migrations", "tests"):
            if (ROOT / name).exists():
                shutil.copytree(ROOT / name, cls.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("compose.yaml", ".env.example", ".python-version", "pyproject.toml", "uv.lock", ".dockerignore"):
            shutil.copy2(ROOT / name, cls.root / name)

    @classmethod
    def tearDownClass(cls):
        if (cls.root / ".env").exists():
            # Only this freshly created test project's volumes are disposable.
            cls.docker("down", "--volumes")
        cls.temp.cleanup()

    @classmethod
    def docker(cls, *args):
        values = project_runner.read_configuration(cls.root / ".env")
        if values["COMPOSE_PROJECT_NAME"] != project_runner.project_name(cls.root):
            raise ValueError("Test configuration does not belong to its temporary directory")
        return project_runner.compose(cls.root, values, *args, capture=True)

    def project(self, *args):
        result = subprocess.run([sys.executable, str(self.root / "scripts/project.py"), *args],
            cwd=tempfile.gettempdir(), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def sql(self, statement):
        return self.docker("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "hm_app",
                           "-At", "-v", "ON_ERROR_STOP=1", "-c", statement).strip()

    def test_bootstrap_preserves_secrets_and_data(self):
        self.project("bootstrap", "--skip-build")
        original = (self.root / ".env").read_bytes()
        self.sql("CREATE TABLE bootstrap_test (value text PRIMARY KEY); INSERT INTO bootstrap_test VALUES ('survives');")
        self.project("bootstrap", "--skip-build")
        self.assertEqual((self.root / ".env").read_bytes(), original)
        self.assertEqual(self.sql("SELECT value FROM bootstrap_test"), "survives")
        self.project("down")
        self.project("bootstrap", "--skip-build")
        self.assertEqual(self.sql("SELECT value FROM bootstrap_test"), "survives")

    def test_bootstrap_path_with_spaces(self):
        self.project("bootstrap", "--skip-build")
        self.assertIn(" ", str(self.root))
        self.project("check")

    def test_bootstrap_repairs_partial_database_provisioning(self):
        self.project("bootstrap", "--skip-build")
        original = (self.root / ".env").read_bytes()
        self.sql("CREATE TABLE recovery_test (value text); INSERT INTO recovery_test VALUES ('preserved');")
        # Only the isolated synthetic installation is modified. The existing
        # PG_VERSION ensures Docker's first-initialization scripts will not rerun.
        self.sql("DROP DATABASE hm_mlflow")
        self.sql("DROP ROLE hm_mlflow")
        self.sql("REVOKE CONNECT ON DATABASE hm_airflow FROM hm_airflow")
        self.sql("GRANT CONNECT ON DATABASE hm_app TO PUBLIC")
        self.project("down")
        self.project("bootstrap", "--skip-build")
        self.assertEqual((self.root / ".env").read_bytes(), original)
        self.assertEqual(self.sql("SELECT value FROM recovery_test"), "preserved")
        self.assertEqual(self.sql("SELECT has_database_privilege('hm_airflow', 'hm_app', 'CONNECT')"), "f")
        self.assertEqual(self.sql("SELECT has_database_privilege('hm_airflow', 'hm_airflow', 'CONNECT')"), "t")
        self.project("check")

    def test_database_roles_are_isolated(self):
        self.project("bootstrap", "--skip-build")
        script = """
import os, psycopg
try:
    psycopg.connect(host='postgres', dbname='hm_mlflow', user='hm_app',
        password=os.environ['HM_APP_PASSWORD'], connect_timeout=5)
except psycopg.OperationalError as error:
    if 'permission denied for database' not in str(error):
        raise
    print('cross-database access rejected')
else:
    raise SystemExit('app role unexpectedly connected to MLflow database')
"""
        output = self.docker("run", "--rm", "--no-deps", "-T", "jobs", "python", "-c", script)
        self.assertIn("cross-database access rejected", output)


if __name__ == "__main__":
    unittest.main()
