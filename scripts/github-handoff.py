#!/usr/bin/env python3
"""Exchange completed bootstrap reports through private GitHub Releases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from handoff import MAX_TOTAL, NAME, read_report, summary

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'emilianodesu/clothing-recommendation'
MARKER = '<!-- hmrec-bootstrap-handoff-v1 -->'
TRAIN_MARKER = '<!-- hmrec-training-handoff-v1 -->'
RELEASE_MARKER = '<!-- hmrec-release-handoff-v1 -->'
SERVING_MARKER = '<!-- hmrec-serving-handoff-v1 -->'
EVENTS_MARKER = '<!-- hmrec-events-handoff-v1 -->'
QUALITY_MARKER = '<!-- hmrec-quality-handoff-v1 -->'
DIAGNOSTIC_MARKER = '<!-- hmrec-diagnostic-handoff-v1 -->'
DATA_MARKER = '<!-- hmrec-data-handoff-v1 -->'
STEPS = ('setup-docker', 'doctor-before', 'docker-smoke', 'bootstrap', 'check', 'verify', 'doctor')
ALLOWED = {'summary.md', 'environment.json', 'commit.txt', 'preflight.log'} | {name + '.log' for name in STEPS}


DATA_ALLOWED = {'summary.md', 'environment.json', 'commit.txt', 'preflight.log', 'job.log', 'build.log', 'result.json', 'config.resolved.json'}

def marker_for(run_id):
    if run_id.startswith('events-'): return EVENTS_MARKER
    if run_id.startswith('serving-'): return SERVING_MARKER
    return RELEASE_MARKER if run_id.startswith('release-') else QUALITY_MARKER if run_id.startswith('quality-') else DIAGNOSTIC_MARKER if run_id.startswith('diagnose-') else TRAIN_MARKER if run_id.startswith('train-') else DATA_MARKER if run_id.startswith('data-') else MARKER

def tag_for(run_id):
    if not isinstance(run_id, str) or not NAME.fullmatch(run_id) or not run_id.startswith(('bootstrap-', 'data-', 'train-', 'diagnose-', 'quality-', 'release-', 'serving-', 'events-')):
        raise ValueError('Run ID inválido; usa el identificador bootstrap-... o data-... del reporte')
    return 'handoff-' + run_id


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_archive(path, run_id=None):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TOTAL:
        raise ValueError('Archivo de reporte inválido o mayor de 50 MiB')
    report = read_report(path)
    tag_for(report['run_id'])
    if run_id is not None and report['run_id'] != run_id:
        raise ValueError('El paquete no corresponde a la ejecución solicitada')
    allowed = DATA_ALLOWED if report['kind'] in ('data-handoff', 'training-handoff', 'diagnostic-handoff', 'quality-handoff', 'release-handoff', 'serving-handoff', 'events-handoff') else ALLOWED
    if report['kind'] == 'training-handoff':
        allowed = allowed | {'tracking.log'}
    if report['run_id'].startswith('data-') != (report['kind'] == 'data-handoff'):
        raise ValueError('Tipo y nombre de reporte incompatibles')
    if report['run_id'].startswith('train-') != (report['kind'] == 'training-handoff'):
        raise ValueError('Tipo y nombre de entrenamiento incompatibles')
    if report['run_id'].startswith('release-') != (report['kind']=='release-handoff'):
        raise ValueError('Release prefix and report kind differ')
    if report['run_id'].startswith('serving-') != (report['kind']=='serving-handoff'):
        raise ValueError('Serving prefix and report kind differ')
    if report['run_id'].startswith('events-') != (report['kind']=='events-handoff'):
        raise ValueError('Events prefix and report kind differ')
    if report['run_id'].startswith('quality-') != (report['kind'] == 'quality-handoff'):
        raise ValueError('Quality prefix and report kind differ')
    if report['run_id'].startswith('diagnose-') != (report['kind'] == 'diagnostic-handoff'):
        raise ValueError('Tipo y nombre de diagnóstico incompatibles')
    if any(item['path'] not in allowed for item in report['files']):
        raise ValueError('El inventario contiene archivos ajenos al reporte de infraestructura')
    return report


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Repositorio inválido; usar propietario/repositorio')
        self.repo = repository
        self.environment = dict(os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1', GH_PAGER='cat')
        self.environment.pop('GH_DEBUG', None)
        self.environment.pop('GH_FORCE_TTY', None)

    def command(self, *args, missing_ok=False):
        result = subprocess.run(['gh', *args], cwd=ROOT, env=self.environment,
                                text=True, capture_output=True, timeout=600)
        if result.returncode:
            if missing_ok and 'HTTP 404' in result.stderr:
                return None
            raise RuntimeError('GitHub: ' + result.stderr.strip())
        return result.stdout

    def api(self, endpoint, missing_ok=False):
        output = self.command('api', '--hostname', 'github.com', endpoint, missing_ok=missing_ok)
        return json.loads(output) if output is not None else None

    def repository(self):
        return self.api('repos/' + self.repo)

    def lookup(self, tag):
        release = self.api('repos/' + self.repo + '/releases/tags/' + tag, missing_ok=True)
        if release is not None:
            return release
        # The tag endpoint excludes drafts; the authenticated list includes them.
        matches = [item for item in self.releases() if item.get('tag_name') == tag]
        if len(matches) > 1:
            raise ValueError('Hay varias Releases para esa ejecución; revisar sin sobrescribir')
        return matches[0] if matches else None

    def create(self, tag, archive, target, notes):
        self.command('release', 'create', tag, str(archive), '--repo', 'github.com/' + self.repo,
                     '--target', target, '--title', 'Reporte ' + tag[len('handoff-'):],
                     '--notes-file', str(notes), '--prerelease', '--latest=false')

    def upload(self, tag, archive):
        self.command('release', 'upload', tag, str(archive), '--repo', 'github.com/' + self.repo)

    def publish(self, tag):
        self.command('release', 'edit', tag, '--repo', 'github.com/' + self.repo,
                     '--draft=false', '--prerelease', '--latest=false')

    def download(self, tag, name, directory):
        self.command('release', 'download', tag, '--repo', 'github.com/' + self.repo,
                     '--pattern', name, '--dir', str(directory))

    def releases(self):
        pages = json.loads(self.command('api', '--hostname', 'github.com', '--paginate', '--slurp',
                                      'repos/' + self.repo + '/releases?per_page=100'))
        return [item for page in pages for item in page]


def private_repository(client, write=False):
    repository = client.repository()
    if repository.get('private') is not True:
        raise ValueError('Se requiere un repositorio privado para intercambiar reportes')
    if write and repository.get('permissions', {}).get('push') is not True:
        raise ValueError('La cuenta de GitHub necesita permiso de escritura en este repositorio')
    return repository


def checked_release(client, run_id):
    tag = tag_for(run_id)
    release = client.lookup(tag)
    if release is not None and (release.get('tag_name') != tag or marker_for(run_id) not in (release.get('body') or '')):
        raise ValueError('La release existente no pertenece a este protocolo; no se modifica')
    return release


def retrieve(client, release, run_id, directory):
    name = run_id + '.zip'
    assets = release.get('assets', [])
    if len(assets) != 1 or assets[0].get('name') != name or assets[0].get('state') != 'uploaded':
        raise ValueError('La release no contiene exactamente el reporte esperado y terminado')
    asset = assets[0]
    if type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_TOTAL:
        raise ValueError('Tamaño de reporte remoto inválido')
    client.download(tag_for(run_id), name, directory)
    path = directory / name
    if not path.is_file() or path.stat().st_size != asset['size']:
        raise ValueError('La descarga está incompleta')
    validate_archive(path, run_id)
    if asset.get('digest') and asset['digest'] != 'sha256:' + digest(path):
        raise ValueError('La huella de GitHub no coincide con la descarga')
    path.chmod(0o600)
    return path


def upload(client, archive):
    # Validate a closed local report before any network operation.
    report = validate_archive(archive)
    repository = private_repository(client, write=True)
    run_id = report['run_id']
    tag = tag_for(run_id)
    with tempfile.TemporaryDirectory(prefix='hmrec-upload-') as temporary:
        directory = Path(temporary)
        snapshot = directory / (run_id + '.zip')
        shutil.copyfile(archive, snapshot)
        snapshot.chmod(0o600)
        report = validate_archive(snapshot, run_id)
        release = checked_release(client, run_id)
        if release is None:
            notes = directory / 'notes.md'
            notes.write_text(marker_for(run_id) + '\n\n' + summary(report))
            client.create(tag, snapshot, report['source_commit'] or repository['default_branch'], notes)
            release = checked_release(client, run_id)
        elif release.get('draft') and not release.get('assets'):
            client.upload(tag, snapshot)
            release = checked_release(client, run_id)
        if release is None:
            raise ValueError('No se pudo confirmar la release; conserva el ZIP y reintenta')
        received = directory / 'received'
        received.mkdir()
        remote = retrieve(client, release, run_id, received)
        if digest(remote) != digest(snapshot):
            raise ValueError('Ya existe otro contenido para ese run_id; no se sobrescribe')
        if release.get('draft'):
            client.publish(tag)
            release = checked_release(client, run_id)
            if release is None or release.get('draft'):
                raise ValueError('No se pudo confirmar la publicación; conserva el ZIP y reintenta')
        return release['html_url']


def download(client, run_id, destination):
    tag_for(run_id)
    private_repository(client)
    release = checked_release(client, run_id)
    if release is None:
        raise ValueError('No hay reporte publicado para ese run_id')
    if release.get('draft'):
        raise ValueError('La publicación está incompleta; reintentar upload desde el equipo emisor')
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (run_id + '.zip')
    with tempfile.TemporaryDirectory(prefix='.download-', dir=destination) as temporary:
        received = retrieve(client, release, run_id, Path(temporary))
        if target.exists() or target.is_symlink():
            if (target.is_symlink() or not target.is_file()
                    or target.stat().st_size != received.stat().st_size
                    or digest(target) != digest(received)):
                raise ValueError('Ya existe otro archivo local; no se sobrescribe')
        else:
            # Same filesystem, atomic publication with no overwrite on a race.
            os.link(received, target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=REPOSITORY, help='Repositorio privado propietario/nombre')
    sub = parser.add_subparsers(dest='action', required=True)
    send = sub.add_parser('upload', help='Subir un ZIP terminado y verificar su descarga')
    send.add_argument('archive', type=Path)
    sub.add_parser('list', help='Listar reportes disponibles en GitHub')
    receive = sub.add_parser('download', help='Descargar y verificar un run_id explícito')
    receive.add_argument('run_id')
    receive.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/incoming')
    args = parser.parse_args()
    try:
        client = GitHub(args.repo)
        if args.action == 'upload':
            print('Reporte disponible: ' + upload(client, args.archive.resolve()))
        elif args.action == 'download':
            target = download(client, args.run_id, args.output_dir.resolve())
            print('Paquete verificado: ' + str(target))
            print(summary(read_report(target)))
        else:
            private_repository(client)
            reports = []
            for release in client.releases():
                tag = release.get('tag_name', '')
                if tag.startswith(('handoff-bootstrap-', 'handoff-data-', 'handoff-train-', 'handoff-diagnose-', 'handoff-quality-', 'handoff-release-', 'handoff-serving-')) and marker_for(tag[len('handoff-'):]) in (release.get('body') or ''):
                    run_id = tag[len('handoff-'):]
                    tag_for(run_id)
                    reports.append({'run_id': run_id, 'draft': release['draft'], 'url': release['html_url']})
            print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print('Intercambio fallido: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
