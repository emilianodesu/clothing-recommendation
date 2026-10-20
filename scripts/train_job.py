#!/usr/bin/env python3
"""T3 uses the same bounded launcher and immutable GitHub attempt reports as T2."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from data_job import TRAINING_PROFILES, capture, export, inspect_run, start


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    launch = commands.add_parser('start')
    launch.add_argument('--machine-id', default='mac24')
    launch.add_argument('--profile', default='hm-project')
    launch.add_argument('--resources', default='mac24')
    launch.add_argument('--config', choices=TRAINING_PROFILES, required=True)
    launch.add_argument('--expected-commit', required=True)
    for name in ('status', 'stop', 'resume', 'export'):
        command = commands.add_parser(name)
        command.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'start':
            start(args.machine_id, args.profile, 'synthetic' if args.config.startswith('demo') else 'hm',
                  args.expected_commit, args.resources, training_config=args.config)
        else:
            if not args.run_id.startswith('train-'):
                raise ValueError('Se requiere un run_id de entrenamiento')
            directory, run, env, state, result, status = inspect_run(args.run_id)
            if args.action == 'export':
                print('Paquete para GitHub: ' + str(export(args.run_id)))
            elif args.action == 'stop':
                if status != 'running' or state.get('Status') == 'preparing':
                    raise ValueError('No hay worker activo para detener')
                capture(env, 'docker', 'stop', '--time', '120', run['container_id'], timeout=150)
                print('Detenido; consultar status y exportar antes de resume.')
            elif args.action == 'resume':
                if status in ('running', 'passed'):
                    raise ValueError('Sólo se reanudan intentos interrumpidos o fallidos')
                experiment = directory.parent.parent / 'training' / run['experiment_id']
                if not (experiment / 'latest.json').exists():
                    raise ValueError('No hay checkpoint durable; exportar diagnóstico e iniciar otra corrida')
                # An old attempt cannot fork a checkpoint already used by a later attempt.
                for other in directory.parent.glob('train-*/environment.json'):
                    metadata = json.loads(other.read_text())
                    if metadata.get('experiment_id') == run['experiment_id'] and metadata.get('attempt', 0) > run['attempt']:
                        raise ValueError('Ya existe un intento posterior; usa su run_id')
                export(args.run_id)  # freeze evidence before the checkpoint advances
                start(run['machine_id'], run['profile'], run['dataset'], run['source_commit'],
                      run['resources']['name'], expected_resources=run['resources'],
                      training_config=run['training_config'], resume_run=run)
            else:
                progress_path = directory.parent.parent / 'training' / run['experiment_id'] / 'progress.json'
                progress = json.loads(progress_path.read_text()) if progress_path.exists() else None
                print(json.dumps({'run_id': args.run_id, 'experiment_id': run['experiment_id'], 'attempt': run['attempt'],
                    'status': status, 'exit_code': state.get('ExitCode'), 'oom_killed': state.get('OOMKilled', False),
                    'limits': run.get('effective_limits'), 'progress': progress, 'result': result}, indent=2))
                return 0 if status in ('running', 'passed') else 1
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
