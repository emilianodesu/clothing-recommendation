#!/usr/bin/env python3
"""One synthetic portability suite in a different macOS account and private engine."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import pwd
import subprocess
import sys
import uuid
from bootstrap import prepare_configuration
from clock_check import checkout_commit, runner_digest
from clock_guard import AwakeGuard
from handoff import local_environment, now, package_report, read_report, write_json
from project import compose

PROFILE = 'hm-portability-user'
ROOT = Path(__file__).resolve().parents[1]


def check_identity(root, home, uid, original_uid, owner):
    if uid == 0 or uid == original_uid or owner != uid:
        raise ValueError('Se requiere otra cuenta sin sudo y un clon propio')
    try:
        relative = root.resolve().relative_to(home.resolve())
    except ValueError:
        raise ValueError('El clon debe estar dentro del home propio') from None
    if not relative.parts or ' ' not in str(relative):
        raise ValueError('Usar una ruta propia que incluya espacios')


def check_socket(endpoint, home):
    expected = 'unix://' + str(home / '.colima' / PROFILE / 'docker.sock')
    if endpoint != expected:
        raise ValueError('Contexto ajeno a la cuenta nueva')
    try:
        Path(endpoint[7:]).resolve().relative_to(home.resolve())
    except ValueError:
        raise ValueError('El socket resuelve fuera del home propio') from None


def check_engine(endpoint, home, engine, occupied):
    check_socket(endpoint, home)
    if occupied:
        raise ValueError('Se requiere socket propio y motor sin contenedores ni volúmenes previos')
    if (engine['Architecture'] not in ('arm64', 'aarch64') or engine['NCPU'] != 3
            or not 5.5 * 2**30 <= engine['MemTotal'] <= 6 * 2**30):
        raise ValueError('Se requiere ARM64, 3 CPU y VM de 6 GiB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-commit', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Ejecutar en mac24 Apple Silicon')
    uid = os.getuid()
    home = Path(pwd.getpwuid(uid).pw_dir).resolve()
    original_uid = pwd.getpwnam('emilianodesu').pw_uid
    check_identity(ROOT, home, uid, original_uid, ROOT.stat().st_uid)
    if os.geteuid() != uid or Path.home().resolve() != home:
        raise ValueError('Iniciar sesión real en la cuenta nueva, sin sudo ni HOME sustituido')
    if Path(os.environ.get('DOCKER_CONFIG', str(home / '.docker'))).resolve() != home / '.docker':
        raise ValueError('No reutilizar configuración Docker de otra cuenta')
    checkout_commit(ROOT, args.expected_commit)
    if (ROOT / '.env').exists() or (ROOT / '.runtime/os-user-once.json').exists():
        raise ValueError('Existe preparación/intento; conservar y revisar, no repetir')
    environment = local_environment(PROFILE)
    environment['SSL_CERT_FILE'] = '/etc/ssl/cert.pem'
    os.environ.clear()
    os.environ.update(environment)
    def capture(*command):
        return subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
    if 'AC Power' not in capture('pmset', '-g', 'batt'):
        raise ValueError('Conectar AC y mantener tapa abierta')
    endpoint = capture('docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
    # Reject cross-account/remote sockets BEFORE contacting the engine.
    check_socket(endpoint, home)
    engine = json.loads(capture('docker', 'info', '--format', '{{json .}}'))
    occupied = bool(capture('docker', 'ps', '-aq') or capture('docker', 'volume', 'ls', '-q'))
    check_engine(endpoint, home, engine, occupied)
    rid = 'bootstrap-mac24-os-user-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    logs = ROOT / '.runtime' / rid
    logs.mkdir(parents=True)
    with (ROOT / '.runtime/os-user-once.json').open('x') as f:
        json.dump({'commit': args.expected_commit, 'run_id': rid}, f)
    folder = ROOT / 'artifacts/handoff' / rid
    folder.mkdir(parents=True)
    report = dict(schema_version=1, kind='bootstrap-handoff', run_id=rid,
        machine_id='mac24-os-user', context='colima-' + PROFILE, source_commit=args.expected_commit,
        source_clean=True, started_at=now(), finished_at=None, status='failed', steps=[], warnings=[])
    write_json(folder / 'environment.json', dict(scope='T7 other OS user, synthetic suite only',
        different_os_uid=True, own_home_and_socket=True, fresh_configuration=True,
        path_with_spaces=True, engine_initially_empty=True, host_os=platform.system(),
        host_architecture=platform.machine(), macos_version=platform.mac_ver()[0],
        engine_cpus=engine['NCPU'], engine_memory_bytes=engine['MemTotal'],
        runner_tree_sha256=runner_digest(ROOT), training_performed=False, test_evaluated=False,
        t7_closed=False))
    (folder / 'commit.txt').write_text(args.expected_commit + '\n')
    print('Logs privados: ' + str(logs), flush=True)
    def verify(log):
        with log.open('w') as stream:
            subprocess.run([sys.executable, 'scripts/project.py', 'verify', '--suite', 'portability', '--existing-services'], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=2400)

    code = 1
    try:
        values = prepare_configuration(ROOT)
        with AwakeGuard():
            for name, operation in [
                ('build', lambda log: compose(ROOT, values, 'build', 'jobs', log_path=log, timeout=2400)),
                ('postgres', lambda log: compose(ROOT, values, 'up', '-d', '--wait', 'postgres', log_path=log, timeout=300)),
                ('verify-portability', verify),
            ]:
                print('T7 otro usuario: ' + name, flush=True)
                step = dict(name=name, log=name + '.log', status='failed', exit_code=1)
                report['steps'].append(step)
                try:
                    operation(logs / step['log'])
                    step.update(status='passed', exit_code=0)
                finally:
                    (folder / step['log']).write_text(name + '\nstatus: ' + step['status'] + '\n')
            checkout_commit(ROOT, args.expected_commit)
            code = 0
    except Exception as error:
        print('Detenido: ' + type(error).__name__ + '; conservar logs y no repetir.', flush=True)
    report.update(status='passed' if code == 0 else 'failed', finished_at=now())
    archive = package_report(folder, report)
    read_report(archive)
    print('Reporte cerrado: ' + str(archive), flush=True)
    subprocess.run([sys.executable, 'scripts/github-handoff.py', 'upload', str(archive)], cwd=ROOT, check=True)
    print('Estado: ' + report['status'] + '; requiere revisión principal. No iniciar T8.', flush=True)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('DETENIDO: ' + type(error).__name__ + '. Conserva la instalación y logs; no reintentes.', file=sys.stderr)
        raise SystemExit(1)
