#!/usr/bin/env python3
"""Read-only Docker preflight. Does not install software or start containers."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Falló ' + ' '.join(args[:3]) + ': ' + result.stderr.strip())
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Emit a machine-readable report')
    parser.add_argument('--application', action='store_true', help='Also check local application configuration, paths and ports')
    args = parser.parse_args()
    report = {'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'host_os': platform.system(), 'host_architecture': platform.machine(),
              'checks': {}, 'warnings': [], 'errors': []}
    for label, cmd in [('docker_client', ('docker', '--version')),
                       ('compose', ('docker', 'compose', 'version')),
                       ('buildx', ('docker', 'buildx', 'version'))]:
        try:
            report['checks'][label] = command(*cmd)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            report['errors'].append(str(error))
    try:
        context = command('docker', 'context', 'show')
        report['checks']['context'] = context
        # Do not dump docker info or config.json: they may contain private settings.
        version, arch, cpus, memory = command('docker', 'info', '--format',
            '{{.ServerVersion}}\t{{.Architecture}}\t{{.NCPU}}\t{{.MemTotal}}').split('\t')
        report['checks']['engine'] = {'version': version, 'architecture': arch,
                                     'cpus': int(cpus), 'memory_gib': round(int(memory) / 2**30, 2)}
        if int(cpus) < 4 or int(memory) < 7 * 2**30:
            report['warnings'].append('Motor por debajo del perfil inicial de 4 CPU y 8 GiB configurados.')
        if shutil.which('colima') and (context == 'colima' or context.startswith('colima-')) and not os.environ.get('DOCKER_HOST'):
            profile = 'default' if context == 'colima' else context[len('colima-'):]
            raw = command('colima', '--profile', profile, 'ssh', '--', 'df', '-Pk', '/var/lib/docker')
            fields = raw.splitlines()[-1].split()
            report['checks']['docker_disk_free_gib'] = round(int(fields[3]) / 2**20, 2)
            if int(fields[3]) < 20 * 2**20:
                report['warnings'].append('Quedan menos de 20 GiB en el disco de Docker.')
            fixture = Path(__file__).resolve().parents[1] / 'docker' / 'smoke' / 'fixture.txt'
            command('colima', '--profile', profile, 'ssh', '--', 'test', '-f', str(fixture))
            report['checks']['project_mount_visible'] = True
    except (OSError, RuntimeError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        report['errors'].append(str(error))
    free = shutil.disk_usage(Path(__file__).resolve().parents[1]).free / 2**30
    report['checks']['workspace_disk_free_gib'] = round(free, 2)
    if free < 100:
        report['warnings'].append('Menos de 100 GiB libres en el proyecto; revisar capacidad para H&M completo.')
    if args.application:
        from bootstrap import read_configuration
        root = Path(__file__).resolve().parents[1]
        try:
            values = read_configuration(root / '.env')
            report['checks']['private_env_mode'] = oct((root / '.env').stat().st_mode & 0o777)
            if (root / '.env').stat().st_mode & 0o077:
                report['errors'].append('.env debe tener permisos 0600; ejecutar bootstrap para corregirlos.')
            directories = {}
            for key in ('DATA_DIR', 'ARTIFACTS_DIR'):
                directory = Path(values[key]).expanduser()
                if not directory.is_absolute():
                    directory = root / directory
                directories[key] = directory.is_dir() and os.access(directory, os.W_OK)
                if not directories[key]:
                    report['errors'].append(key + ' no existe o no permite escritura.')
            report['checks']['application_directories_writable'] = directories
            ports = {key: int(values[key]) for key in ('API_PORT', 'UI_PORT', 'MLFLOW_PORT', 'AIRFLOW_PORT')}
            if len(set(ports.values())) != len(ports):
                report['errors'].append('Hay puertos repetidos en .env.')
            port_checks = {}
            for key, port in ports.items():
                with socket.socket() as probe:
                    try:
                        probe.bind(('127.0.0.1', port))
                        port_checks[key] = {'port': port, 'status': 'available'}
                    except OSError:
                        port_checks[key] = {'port': port, 'status': 'occupied'}
                        report['warnings'].append(key + ' está ocupado; comprobar si pertenece al proyecto antes de arrancar ese servicio.')
            report['checks']['application_ports'] = port_checks
        except (OSError, ValueError) as error:
            report['errors'].append('Configuración de aplicación: ' + str(error))
    report['status'] = 'failed' if report['errors'] else ('warning' if report['warnings'] else 'passed')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report['checks'].items():
            print(key + ':', value)
        for warning in report['warnings']:
            print('AVISO:', warning)
        for error in report['errors']:
            print('ERROR:', error)
        print('Diagnóstico:', report['status'])
        print('Este diagnóstico no valida el modelo ni la capacidad de entrenamiento.')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
