#!/usr/bin/env python3
"""Run T2 in a detached, resource-limited container and export a small GitHub report."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from bootstrap import read_configuration
from handoff import NAME, execute, local_environment, now, package_report, validate_context, write_json
from project import compose

ROOT = Path(__file__).resolve().parents[1]


def validate_run_id(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or not value.startswith('data-'):
        raise ValueError('Run ID de datos inválido')
    return value


def classify_result(state, result):
    if state.get('Running'):
        return 'running'
    if state.get('ExitCode') == 130 and not state.get('OOMKilled'):
        return 'interrupted'
    if state.get('OOMKilled') or state.get('ExitCode', 0) != 0:
        return 'failed'
    if result and result.get('status') == 'passed' and result.get('stage') == 'T2' and result.get('verification_exit_code') == 0:
        return 'passed'
    return 'interrupted'


def capture(environment, *command, timeout=60):
    return subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True,
                          check=True, timeout=timeout).stdout.strip()


def paths(values):
    result = {}
    for key in ('DATA_DIR', 'ARTIFACTS_DIR'):
        path = Path(values[key]).expanduser()
        result[key] = (path if path.is_absolute() else ROOT / path).resolve()
    return result


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_resources(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name):
        raise ValueError('Nombre de perfil de recursos inválido')
    directory = ROOT / 'configs/resources'
    path = directory / (name + '.json')
    if path.resolve().parent != directory.resolve():
        raise ValueError('El perfil de recursos debe estar en configs/resources')
    raw = path.read_bytes()
    config = json.loads(raw)
    if (not isinstance(config, dict) or set(config) != {'schema_version', 'colima', 'job'}
            or type(config['schema_version']) is not int or config['schema_version'] != 1):
        raise ValueError('Esquema de recursos inválido; se requiere schema_version 1')
    for section, keys in [('colima', {'memory_gib', 'cpus'}), ('job', {'memory_gib', 'cpus', 'threads'})]:
        values = config[section]
        if not isinstance(values, dict) or set(values) != keys:
            raise ValueError('Campos de recursos inválidos en ' + section)
        if any(type(value) is not int or value < 1 for value in values.values()):
            raise ValueError('Los recursos deben ser enteros positivos')
    vm, job = config['colima'], config['job']
    if vm['memory_gib'] < job['memory_gib'] + 1 or vm['cpus'] < job['cpus'] or job['threads'] > job['cpus']:
        raise ValueError('Recursos incompatibles: deja al menos 1 GiB en la VM y respeta CPU/hilos')
    return {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'config': config}


def requested_limits(resources):
    job = resources['config']['job']
    return {'memory_bytes': job['memory_gib'] * 2**30, 'cpus': job['cpus'],
            'polars_threads': job['threads'], 'omp_threads': job['threads'], 'mkl_threads': job['threads']}


def validate_capacity(limits, engine):
    # Docker's MemTotal excludes the VM kernel's overhead; compare job + margin,
    # not the nominal Colima allocation. Other workloads must be stopped separately.
    if engine['MemTotal'] < limits['memory_bytes'] + 2**30 or engine['NCPU'] < limits['cpus']:
        raise ValueError('Docker no tiene capacidad para estos recursos (job + 1 GiB de margen). '
                         'Prepara Colima según el perfil; consulta docs/data.md.')


def effective_limits(info):
    environment = dict(value.split('=', 1) for value in info['Config']['Env'] if '=' in value)
    host = info['HostConfig']
    return {'memory_bytes': host['Memory'], 'nano_cpus': host['NanoCpus'],
            'memory_swap_bytes': host['MemorySwap'],
            'polars_threads': int(environment.get('POLARS_MAX_THREADS', '0')),
            'omp_threads': int(environment.get('OMP_NUM_THREADS', '0')),
            'mkl_threads': int(environment.get('MKL_NUM_THREADS', '0'))}


def start(machine_id, profile, dataset, expected_commit=None, resource_profile='base', expected_resources=None):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', machine_id):
        raise ValueError('Usar un alias como mac24')
    resources = load_resources(resource_profile)
    if expected_resources is not None and resources != expected_resources:
        raise ValueError('El perfil de recursos cambió; resume requiere la misma configuración y huella')
    limits = requested_limits(resources)
    env = local_environment(profile)
    commit = capture(env, 'git', 'rev-parse', 'HEAD')
    if expected_commit and commit != expected_commit:
        raise ValueError('El commit no corresponde a la instrucción recibida')
    if capture(env, 'git', 'status', '--porcelain'):
        raise ValueError('El clon debe estar limpio; conserva cambios y haz commit antes de la corrida oficial')
    values = read_configuration(ROOT / '.env')
    dirs = paths(values)
    config = 'hm' if dataset == 'hm' else 'demo'
    run_id = 'data-' + machine_id + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    directory = dirs['ARTIFACTS_DIR'] / 'data-jobs' / run_id
    directory.mkdir(parents=True, mode=0o700)
    run = {'run_id':run_id, 'machine_id':machine_id, 'context':env['DOCKER_CONTEXT'], 'profile':profile,
           'source_commit':commit, 'source_clean':True, 'dataset':dataset, 'image_id':None,
           'config_sha256':fingerprint(ROOT / 'configs' / (config+'.yaml')),
           'uv_lock_sha256':fingerprint(ROOT / 'uv.lock'), 'started_at':now(), 'host_architecture':platform.machine(),
           'host_os':platform.system(), 'launch_status':'running',
           'resources':resources, 'limits':limits, 'ca_file':os.environ.get('SSL_CERT_FILE'),
           'scope':'T2 only; data and temporal features, no training'}
    launch_lock = (directory / '.launch.lock').open('a')
    fcntl.flock(launch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    write_json(directory / 'environment.json', run)
    (directory / 'commit.txt').write_text(commit + '\n')
    print(json.dumps({'run_id':run_id, 'status':'preparing'}), flush=True)
    try:
        for key, path in dirs.items():
            if not path.is_dir():raise ValueError('Falta directorio de datos/artefactos; ejecutar bootstrap')
            if ',' in str(path):raise ValueError('Docker --mount requiere una ruta sin comas')
        context = json.loads(capture(env, 'docker', 'context', 'inspect', env['DOCKER_CONTEXT']))[0]
        engine = json.loads(capture(env, 'docker', 'info', '--format', '{{json .}}'))
        validate_context(context['Endpoints']['docker']['Host'], engine['Architecture'])
        active = capture(env, 'docker', 'ps', '--filter', 'label=hmrec.data-project=' + values['COMPOSE_PROJECT_NAME'], '--format', '{{.ID}}')
        if active:
            raise ValueError('Ya hay un trabajo T2 activo en este proyecto')
        run.update(engine_version=engine['ServerVersion'], engine_memory_bytes=engine['MemTotal'], engine_cpus=engine['NCPU'])
        validate_capacity(limits, engine)
        # Build logs persist even when the image cannot be built.
        original = os.environ.copy()
        try:
            os.environ.clear(); os.environ.update(env)
            compose(ROOT, values, 'build', 'jobs', log_path=directory / 'build.log')
            run['build_exit_code'] = 0
        except subprocess.CalledProcessError as error:
            run['build_exit_code'] = error.returncode
            raise
        finally:
            os.environ.clear(); os.environ.update(original)
        if capture(env, 'git', 'rev-parse', 'HEAD') != commit or capture(env, 'git', 'status', '--porcelain'):
            raise ValueError('El código cambió durante la construcción; repetir desde un commit limpio')
        image = json.loads(capture(env, 'docker', 'image', 'inspect', 'hmrec-jobs:0.1.0'))[0]
        run.update(image_id=image['Id'], image_architecture=image['Architecture'])
        container = capture(env, 'docker', 'create', '--name', run_id.lower(), '--network', 'none',
            '--read-only', '--tmpfs', '/tmp', '--memory', str(resources['config']['job']['memory_gib']) + 'g',
            '--cpus', str(limits['cpus']),
            '--log-driver', 'json-file', '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2',
            '--label', 'hmrec.data-project=' + values['COMPOSE_PROJECT_NAME'], '--label', 'hmrec.run-id=' + run_id,
            '--user', values['HMREC_UID'] + ':' + values['HMREC_GID'],
            '--env', 'POLARS_MAX_THREADS=' + str(limits['polars_threads']),
            '--env', 'OMP_NUM_THREADS=' + str(limits['omp_threads']),
            '--env', 'MKL_NUM_THREADS=' + str(limits['mkl_threads']),
            '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONUNBUFFERED=1',
            '--env', 'PYTEST_ADDOPTS=-o cache_dir=/tmp/pytest-cache',
            '--mount', 'type=bind,source=' + str(dirs['DATA_DIR']) + ',target=/data',
            '--mount', 'type=bind,source=' + str(dirs['ARTIFACTS_DIR']) + ',target=/artifacts',
            image['Id'], 'python', '-m', 'hm_recommender.data.worker', '--config', '/opt/hmrec/configs/' + config + '.yaml',
            '--run-dir', '/artifacts/data-jobs/' + run_id)
        run['container_id'] = container
        info = json.loads(capture(env, 'docker', 'inspect', container))[0]
        applied = effective_limits(info)
        run['effective_limits'] = applied
        if (applied['memory_bytes'] != limits['memory_bytes'] or applied['nano_cpus'] != limits['cpus'] * 10**9
                or any(applied[key] != limits[key] for key in ('polars_threads', 'omp_threads', 'mkl_threads'))):
            raise ValueError('Los límites efectivos de Docker no coinciden con el perfil solicitado; no se inicia el job')
        write_json(directory / 'environment.json', run)
        capture(env, 'docker', 'start', container)
        run['launch_status'] = 'started'
    except (Exception, KeyboardInterrupt) as error:
        code = 130 if isinstance(error, KeyboardInterrupt) else getattr(error, 'returncode', 1)
        run.update(launch_status='interrupted' if code == 130 else 'failed', launch_exit_code=code)
        (directory / 'preflight.log').write_text(str(error) + '\n')
        print('Conserva ' + run_id + '; puedes consultar y exportar su diagnóstico.', flush=True)
        raise
    finally:
        try:
            write_json(directory / 'environment.json', run)
        finally:
            launch_lock.close()
    print(json.dumps({'run_id':run_id,'status':'started','source_commit':commit,'image_id':run['image_id']}, indent=2))
    return run_id


def inspect_run(run_id):
    validate_run_id(run_id)
    directory = paths(read_configuration(ROOT / '.env'))['ARTIFACTS_DIR'] / 'data-jobs' / run_id
    # The launcher owns this lock until its final metadata is published. Reading
    # under it avoids confusing a running build/create with an abandoned attempt.
    with (directory / '.launch.lock').open('a') as lock:
        active = False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
        run = json.loads((directory / 'environment.json').read_text())
        env = local_environment(run['profile'])
        if active:
            return directory, run, env, {'Running':True,'Status':'preparing'}, None, 'running'
    container = run.get('container_id')
    if not container:
        status = run.get('launch_status')
        if status not in ('failed','interrupted'):
            status = 'interrupted'
        state = {'Running':False, 'ExitCode':run.get('launch_exit_code',130), 'Status':'not-created'}
        return directory, run, env, state, None, status
    if not re.fullmatch('[a-f0-9]{64}', container):
        raise ValueError('Identificador de contenedor inválido')
    result = None
    try:
        info = json.loads(capture(env, 'docker', 'inspect', container))[0]
    except subprocess.CalledProcessError:
        # Distinguish an unavailable engine from a container that was removed.
        capture(env, 'docker', 'info', '--format', '{{.ServerVersion}}')
        state = {'Running':False, 'ExitCode':0, 'Status':'missing'}
        status = 'interrupted'
    else:
        if info['Config']['Labels'].get('hmrec.run-id') != run_id or info['Image'] != run['image_id']:
            raise ValueError('Identidad del contenedor incompatible')
        state = info['State']
        # Read the worker's final file only after observing container termination.
        result_path = directory / 'result.json'
        result = json.loads(result_path.read_text()) if result_path.exists() else None
        status = classify_result(state, result)
        if state.get('Status') == 'created' and run.get('launch_status') == 'failed':
            status = 'failed'
    return directory, run, env, state, result, status


def export(run_id):
    directory, run, env, state, result, status = inspect_run(run_id)
    if status == 'running':raise ValueError('Esperar a que termine; no se exportan paquetes abiertos')
    export_dir = directory.parent.parent / 'handoff' / run_id
    archive = export_dir.with_name(run_id + '.zip')
    if archive.exists():
        from handoff import read_report
        read_report(archive)
        return archive
    export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('environment.json', 'commit.txt', 'result.json', 'config.resolved.json', 'preflight.log', 'build.log'):
        if (directory / name).exists():shutil.copyfile(directory / name, export_dir / name)
    if state.get('Status') in ('missing','not-created'):
        (export_dir / 'job.log').write_text('Contenedor ausente o no creado: revisar diagnóstico de lanzamiento.\n')
    else:
        execute(['docker','logs',run['container_id']], ROOT, env, export_dir / 'job.log')
    code = 0 if status == 'passed' else (130 if status == 'interrupted' else state.get('ExitCode') or 1)
    steps = []
    if (export_dir / 'build.log').exists():
        build_code = run.get('build_exit_code',1)
        steps.append({'name':'build','log':'build.log','exit_code':build_code,
                      'status':'passed' if build_code == 0 else ('interrupted' if build_code == 130 else 'failed')})
    steps.append({'name':'data','log':'job.log','status':status,'exit_code':code})
    report = {'schema_version':1, 'kind':'data-handoff', 'run_id':run_id, 'machine_id':run['machine_id'],
        'context':run['context'], 'source_commit':run['source_commit'], 'source_clean':run['source_clean'],
        'started_at':run['started_at'], 'finished_at':now(), 'status':status, 'warnings':[],
        'steps':steps,
        'container_exit_code':state.get('ExitCode'), 'oom_killed':state.get('OOMKilled',False),
        'image_id':run['image_id'], 'result':result}
    for key in ('resources', 'limits', 'effective_limits'):
        if key in run:report[key] = run[key]
    return package_report(export_dir, report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    launch = commands.add_parser('start')
    launch.add_argument('--machine-id', default='mac24')
    launch.add_argument('--profile', default='hm-project')
    launch.add_argument('--dataset', choices=('hm','synthetic'), default='hm')
    launch.add_argument('--expected-commit')
    launch.add_argument('--resources', default='base', help='Perfil versionado en configs/resources (base o mac24)')
    for name in ('status','export','resume'):
        command = commands.add_parser(name)
        command.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'start':start(args.machine_id,args.profile,args.dataset,args.expected_commit,args.resources)
        elif args.action == 'export':print('Paquete para GitHub: ' + str(export(args.run_id)))
        else:
            directory, run, env, state, result, status = inspect_run(args.run_id)
            if args.action == 'resume':
                if status == 'running':raise ValueError('El trabajo sigue activo')
                resources = run.get('resources')
                start(run['machine_id'],run['profile'],run['dataset'],run['source_commit'],
                      resources['name'] if resources else 'base', expected_resources=resources)
            else:
                print(json.dumps({'run_id':args.run_id,'status':status,'container_exit_code':state.get('ExitCode'),
                    'oom_killed':state.get('OOMKilled',False), 'resources':run.get('resources'),
                    'limits':run.get('limits'), 'effective_limits':run.get('effective_limits'), 'result':result}, indent=2))
                return 0 if status in ('running','passed') else 1
        return 0
    except KeyboardInterrupt:
        print('Lanzamiento interrumpido; exportar el run_id anunciado.', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
