#!/usr/bin/env python3
"""Read-only T3 diagnostics over a closed, successful training attempt."""
import argparse
import json
import subprocess
import sys
from zipfile import ZipFile

from data_job import capture, export, fingerprint, inspect_run, start, validate_run_id
from handoff import read_report
from pathlib import Path

# This shared contract uses the standard library only; no scientific packages on the host.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hm_recommender.model.comparison_contract import validate_comparison_sources


def source_for(artifacts, run_id, lock_sha):
    validate_run_id(run_id)
    if not run_id.startswith('train-'):
        raise ValueError('Se requiere el run_id original de entrenamiento')
    archive = artifacts / 'handoff' / (run_id + '.zip')
    report = read_report(archive)
    if report['run_id'] != run_id or report['kind'] != 'training-handoff' or report['status'] != 'passed' or not report['source_clean']:
        raise ValueError('Se requiere un reporte T3 cerrado y exitoso')
    with ZipFile(archive) as bundle:
        result = json.loads(bundle.read('result.json'))
        environment = json.loads(bundle.read('environment.json'))
    if result != report['result'] or result.get('verification_exit_code') != 0:
        raise ValueError('Resultado de origen contradictorio')
    if (environment['run_id'] != run_id or environment['source_commit'] != report['source_commit']
            or environment['uv_lock_sha256'] != lock_sha):
        raise ValueError('Identidad o dependencias del origen cambiaron')
    experiment = environment['experiment_id']
    validate_run_id(experiment)
    if not experiment.startswith('train-'):
        raise ValueError('Experimento de origen inválido')
    training = result['training']
    if training['status'] != 'passed' or result['evaluation']['test_evaluated'] is not False:
        raise ValueError('Origen incompleto o test ya evaluado')
    return {'run_id': run_id, 'experiment_id': experiment, 'source_commit': report['source_commit'],
        'package_id': report['package_id'], 'archive_sha256': fingerprint(archive),
        'uv_lock_sha256': lock_sha, 'config': training['config'],
        'checkpoint': result['last_durable_progress'], 'rows_consumed': training['rows_consumed'],
        'input_identity': training['input_identity'], 'evaluation': result['evaluation']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    launch = commands.add_parser('start')
    launch.add_argument('--machine-id', default='mac24')
    launch.add_argument('--profile', default='hm-project')
    launch.add_argument('--resources', default='mac24')
    launch.add_argument('--source-run-id', required=True)
    launch.add_argument('--reference-run-id', help='Comparar tres épocas con un entrenamiento cerrado de referencia')
    launch.add_argument('--expected-commit', required=True)
    for name in ('status', 'stop', 'export'):
        command = commands.add_parser(name)
        command.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'start':
            start(args.machine_id, args.profile, 'hm', args.expected_commit, args.resources, diagnostic_run=args.source_run_id, reference_run=args.reference_run_id)
        else:
            if not args.run_id.startswith('diagnose-'):
                raise ValueError('Se requiere run_id de diagnóstico')
            directory, run, env, state, result, status = inspect_run(args.run_id)
            if args.action == 'export':
                print('Paquete para GitHub: ' + str(export(args.run_id)))
            elif args.action == 'stop':
                if status != 'running' or state.get('Status') == 'preparing':
                    raise ValueError('No hay worker activo para detener')
                capture(env, 'docker', 'stop', '--time', '30', run['container_id'])
                print('Detenido; exportar y, si hace falta, iniciar otro diagnóstico.')
            else:
                print(json.dumps({'run_id': args.run_id, 'source_run_id': run['diagnostic_source']['run_id'],
                    'status': status, 'exit_code': state.get('ExitCode'), 'oom_killed': state.get('OOMKilled', False),
                    'limits': run.get('effective_limits'), 'isolation': run.get('isolation'), 'result': result}, indent=2))
                return 0 if status in ('running', 'passed') else 1
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
