#!/usr/bin/env python3
"""Prepare a Mac, verify the bootstrap and package evidence for the other Mac."""
import argparse
import datetime
import json
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import uuid

from handoff import local_environment, now, package_report, run_steps, validate_context, write_json

ROOT = Path(__file__).resolve().parents[1]


def capture(root, environment, *command):
    return subprocess.run(command, cwd=root, env=environment, check=True,
                          text=True, capture_output=True, timeout=30).stdout.strip()


def run(root, machine_id, profile, skip_setup, expected_commit):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', machine_id):
        raise ValueError('Usar un alias como mac24, sin espacios ni datos personales')
    if expected_commit and not re.fullmatch(r'[a-f0-9]{40}', expected_commit):
        raise ValueError('--expected-commit debe ser un SHA Git completo')
    environment = local_environment(profile)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_id = 'bootstrap-' + machine_id + '-' + stamp + '-' + uuid.uuid4().hex[:8]
    destination = root / 'artifacts/handoff' / run_id
    destination.mkdir(parents=True, mode=0o700)
    report = {'schema_version': 1, 'kind': 'bootstrap-handoff', 'run_id': run_id,
              'machine_id': machine_id, 'context': environment['DOCKER_CONTEXT'],
              'started_at': now(), 'finished_at': None, 'status': 'failed',
              'source_commit': None, 'source_clean': False, 'steps': [], 'warnings': []}
    write_json(destination / 'environment.json', {
        'machine_id': machine_id, 'host_os': platform.system(), 'host_architecture': platform.machine(),
        'macos_version': platform.mac_ver()[0], 'host_python': platform.python_version(),
        'colima_profile': profile, 'docker_setup_skipped': skip_setup,
        'requested_colima_resources': None if skip_setup else {'cpus': 4, 'memory_gib': 8, 'disk_gib': 100},
    })
    write_json(destination / 'manifest.json', report)
    code = 1
    print('Ejecución: ' + run_id, flush=True)
    try:
        commit = capture(root, environment, 'git', 'rev-parse', 'HEAD')
        report['source_commit'] = commit
        (destination / 'commit.txt').write_text(commit + '\n')
        report['source_clean'] = not bool(capture(root, environment, 'git', 'status', '--porcelain'))
        if not report['source_clean']:
            raise ValueError('El clon tiene cambios sin guardar en Git. Conservarlos y usar un clon limpio; no se descartan archivos.')
        if expected_commit and commit != expected_commit:
            raise ValueError('El commit actual no coincide con --expected-commit')
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise ValueError('Esta guía requiere una Mac Apple Silicon con Python ejecutado de forma nativa')
        if not skip_setup:
            for executable in ('brew', 'ruby'):
                if not shutil.which(executable):
                    raise ValueError('Falta ' + executable + '; consultar docs/second-mac.md')
            code = run_steps(root, destination, report, [('setup-docker', [sys.executable,
                str(root / 'scripts/setup-docker-macos.py'), '--profile', profile,
                '--cpus', '4', '--memory', '8', '--disk', '100'])], environment)
        else:
            code = 0
        if code == 0:
            endpoint = capture(root, environment, 'docker', 'context', 'inspect',
                               '--format', '{{.Endpoints.docker.Host}}')
            # Check the endpoint before contacting an existing engine.
            if not endpoint.startswith('unix://'):
                raise ValueError('Se requiere un contexto Docker local con socket Unix')
            architecture = capture(root, environment, 'docker', 'info', '--format', '{{.Architecture}}')
            validate_context(endpoint, architecture)
            project = str(root / 'scripts/project.py')
            doctor = str(root / 'scripts/doctor.py')
            code = run_steps(root, destination, report, [
                ('doctor-before', [sys.executable, doctor, '--json']),
                ('docker-smoke', ['bash', str(root / 'scripts/docker-smoke.sh')]),
                ('bootstrap', [sys.executable, project, 'bootstrap']),
                ('check', [sys.executable, project, 'check']),
                ('verify', [sys.executable, project, 'verify', '--suite', 'bootstrap']),
                ('doctor', [sys.executable, doctor, '--application', '--json']),
            ], environment)
        if code == 0:
            doctor_report = json.loads((destination / 'doctor.log').read_text())
            report['warnings'] = doctor_report.get('warnings', [])
            if doctor_report.get('status') not in ('passed', 'warning'):
                raise ValueError('El diagnóstico final no confirma un entorno válido')
            end_commit = capture(root, environment, 'git', 'rev-parse', 'HEAD')
            dirty = capture(root, environment, 'git', 'status', '--porcelain')
            if end_commit != commit or dirty:
                report['source_clean'] = False
                raise ValueError('El código cambió durante la ejecución; repetir desde un commit limpio')
    except KeyboardInterrupt:
        code = 130
        report['status'] = 'interrupted'
        (destination / 'preflight.log').write_text('Ejecución interrumpida; revisar contenedores antes de reintentar.\n')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        code = 1
        report['status'] = 'failed'
        message = str(error)
        (destination / 'preflight.log').write_text(message + '\n')
        print('ERROR: ' + message, file=sys.stderr)
    report['finished_at'] = now()
    archive = package_report(destination, report)
    print('Estado: ' + report['status'], flush=True)
    print('Paquete para devolver: ' + str(archive), flush=True)
    print('Copiar ese ZIP a artifacts/incoming/ del otro clon; no se envía automáticamente.', flush=True)
    return code, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine-id', default='mac24', help='Alias sin datos personales')
    parser.add_argument('--profile', default='hm-project', help='Perfil Colima local')
    parser.add_argument('--skip-docker-setup', action='store_true', help='Usar un perfil Colima ya preparado')
    parser.add_argument('--expected-commit', help='Rechazar una revisión Git diferente del SHA completo recibido')
    args = parser.parse_args()
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        code, _ = run(ROOT, args.machine_id, args.profile, args.skip_docker_setup, args.expected_commit)
        return code if 0 <= code <= 255 else 1
    except (OSError, ValueError) as error:
        print('No se pudo completar el paquete: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
