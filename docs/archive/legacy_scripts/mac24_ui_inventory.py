#!/usr/bin/env python3
"""Read-only Docker inventory; never builds, starts, stops or deploys services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from bootstrap import read_configuration
from clock_check import checkout_commit
from handoff import now, write_json

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = 'colima-hm-project'
TEMPLATE = ('{"container_id":{{json .Id}},"image_id":{{json .Image}},'
    '"started_at":{{json .State.StartedAt}},"restart_count":{{.RestartCount}},'
    '"running":{{.State.Running}},'
    '"health":{{with index .State "Health"}}{{json .Status}}{{else}}"none"{{end}},'
    '"memory_bytes":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}')


def verify_stable(before, after):
    if before != after:
        raise ValueError('El inventario cambió durante la lectura; revisar antes de desplegar.')


def verify_services(rows, project):
    result = {}
    for name in ('api', 'postgres', 'ui'):
        matches = [r for r in rows.values() if r['project'] == project and r['service'] == name]
        if len(matches) != 1 or not matches[0]['running'] or matches[0]['health'] != 'healthy':
            raise ValueError('Se requiere exactamente un servicio saludable: ' + name)
        result[name] = matches[0]['container_id']
    return result


def docker(*args, optional=False):
    p = subprocess.run(['docker', '--context', CONTEXT, *args], cwd=ROOT,
        capture_output=True, text=True, timeout=45)
    if p.returncode:
        if optional:return None
        # Never echo stderr: a tool failure may contain private host information.
        raise ValueError('Falló una consulta Docker; conservar el reporte parcial.')
    return p.stdout.strip()


def snapshot():
    ids = docker('ps', '-aq', '--no-trunc').splitlines()
    return {cid: json.loads(docker('inspect', '--format', TEMPLATE, cid)) for cid in ids}


def run(expected):
    os.umask(0o077)
    checkout_commit(ROOT, expected)
    values = read_configuration(ROOT / '.env')
    project = values['COMPOSE_PROJECT_NAME']
    folder = ROOT / '.runtime' / ('ui-inventory-' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    report = dict(schema_version=1, protocol='ui-inventory-v1', source_commit=expected,
        created_at=now(), status='failed', context=CONTEXT, deployment_started=False,
        rollback_image_execution_verified=False, build_capacity_admitted=False)
    print('Reporte privado: ' + str(folder / 'report.json'), flush=True)
    try:
        report['stage'] = 'inventory_before'
        before = snapshot(); report['before'] = before
        services = verify_services(before, project)
        report['services'] = services
        report['stage'] = 'engine_and_image_metadata'
        report['engine'] = json.loads(docker('info', '--format',
            '{"memory_bytes":{{.MemTotal}},"cpus":{{.NCPU}},"architecture":{{json .Architecture}}}'))
        old_image = before[services['ui']]['image_id']
        image = docker('image', 'inspect', '--format', '{{.Id}}', old_image, optional=True)
        report['ui_image_metadata_available'] = image == old_image
        report['stage'] = 'ui_source'
        raw = docker('exec', services['ui'], 'sha256sum', '/opt/hmrec/src/hm_recommender/ui/app.py')
        source = raw.split()[0] if raw else ''
        if not re.fullmatch('[0-9a-f]{64}', source):raise ValueError('Huella UI inválida.')
        target = hashlib.sha256((ROOT / 'src/hm_recommender/ui/app.py').read_bytes()).hexdigest()
        report.update(running_ui_sha256=source, checkout_ui_sha256=target,
                      ui_matches_checkout=source == target)
        report['stage'] = 'inventory_after'
        after = snapshot(); report['after'] = after
        verify_services(after, project); verify_stable(before, after)
        report.update(status='passed', inventory_unchanged=True, stage='complete')
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        write_json(folder / 'report.json', report)
        # Allowlisted aggregate summary: no env, paths, tokens or customer data.
        summary = {key: report[key] for key in ('protocol', 'source_commit', 'status', 'stage',
            'deployment_started', 'rollback_image_execution_verified', 'build_capacity_admitted',
            'ui_image_metadata_available', 'running_ui_sha256', 'checkout_ui_sha256',
            'ui_matches_checkout', 'engine', 'inventory_unchanged', 'error_type') if key in report}
        write_json(folder / 'summary.json', summary)
        print(json.dumps(summary, sort_keys=True), flush=True)
    print('Inventario terminado; no se actualizó UI. Revisión principal pendiente.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-commit', required=True)
    try:run(parser.parse_args().expected_commit)
    except Exception as error:
        print('DETENIDO: ' + type(error).__name__ + '. No reconstruir ni reiniciar servicios.', file=sys.stderr)
        sys.exit(1)
