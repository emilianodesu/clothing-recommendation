#!/usr/bin/env python3
"""New Linux deployment entry point. See docs/server-deployment.md; no historical campaigns."""
import argparse
import base64
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

from bootstrap import read_configuration
from project import compose, prepare_start_configuration, image_override
from server_support import admit, check_routes, inventory, run, write_private
import server_gateway as gateway

ROOT = Path(__file__).resolve().parents[1]
BUILDKIT = 'moby/buildkit@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea'


def source_commit(root):
    commit = run(['git', '-C', str(root), 'rev-parse', 'HEAD'])
    if run(['git', '-C', str(root), 'status', '--porcelain']):
        raise ValueError('Se requiere checkout fuente limpio y commit completo fijado')
    return commit


def guarded(root, phase, *, shared=False):
    snapshot = inventory(root)
    admit(snapshot, phase, shared=shared)
    if os.getuid() == 0:
        raise ValueError('Ejecutar como usuario de despliegue sin root; debe tener acceso autorizado a Docker')
    return snapshot


def build(root, *, shared=False):
    commit = source_commit(root)
    before = guarded(root, 'build', shared=shared)
    values = prepare_start_configuration(root)
    if (root/'.runtime/server-build.json').exists():
        raise ValueError('Esta preparación ya tiene resultado; conservarlo y usar otro checkout para actualización')
    builder = 'hm-server-'+uuid.uuid4().hex[:12]
    folder = root/'.runtime/server-build'/builder
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    print('Logs privados de este intento: '+str(folder), flush=True)
    mem_gib = 4 if shared else 6
    def logged(args, name):
        with (folder/name).open('w') as log:
            os.chmod(folder/name, 0o600)
            print('Construcción: '+name, flush=True)
            subprocess.run(args, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=7200)
    try:
        run(['docker', 'buildx', 'create', '--name', builder, '--driver', 'docker-container',
             '--driver-opt', f'image={BUILDKIT},memory={mem_gib}g,memory-swap={mem_gib}g,cpu-period=100000,cpu-quota=300000'])
        for kind in ('jobs', 'app'):
            logged(['docker', 'buildx', 'build', '--builder', builder, '--load', '--progress', 'plain',
                    '--secret', 'id=build_ca,src='+str(root/'.runtime/build-ca.pem'),
                    '-f', 'docker/'+kind+'.Dockerfile', '-t', 'hmrec-'+kind+':0.1.0', '.'], kind+'.log')
        from restore import verify_images
        images = verify_images(root)
        if source_commit(root) != commit:
            raise ValueError('El checkout cambió durante la construcción')
        compose(root, values, 'pull', 'postgres')
        run(['docker', 'pull', gateway.IMAGE], timeout=600)
        write_private(root/'.runtime/server-build.json', dict(source_commit=commit, images=images,
            docker=before['docker'], builder_image=BUILDKIT, builder_memory_bytes=mem_gib*2**30,
            gateway_image=gateway.IMAGE, build_logs=str(folder), created_at=datetime.now(timezone.utc).isoformat()))
        print('Imágenes construidas/verificadas. Ningún servicio iniciado.')
    finally:
        # Only this newly created builder; no application volumes or shared builders.
        subprocess.run(['docker', 'buildx', 'rm', '--keep-state', builder], stdout=subprocess.DEVNULL, check=False)


def restore(root, args, *, shared=False):
    source_commit(root)
    guarded(args.destination.parent, 'restore', shared=shared)
    check_routes(json.loads(run(['ip', '-j', '-4', 'route', 'show', 'table', 'all'])))
    receipt = json.loads((root/'.runtime/server-build.json').read_text())
    if receipt['source_commit'] != source_commit(root):
        raise ValueError('Construcción y checkout no coinciden')
    for tag, identity in receipt['images'].items():
        if run(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}']) != identity:
            raise ValueError('Imagen cambió después de construir; conservar ambos resultados')
    from restore import restore_into
    result = restore_into(root, args.archive, args.destination, args.sha256, args.port, pin_images=True)
    write_private(args.destination/'.runtime/server-origin.json', dict(source_commit=receipt['source_commit'],
        backup_sha256=args.sha256, images=receipt['images'], restored=result['status'], shared_engine=shared))
    print('Restauración terminada. Continuar usando --root '+str(args.destination))


def http(url, headers=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers=headers or {})
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read(1024*1024)


def local_check(root, *, with_ui=True):
    values = read_configuration(root/'.env')
    if not image_override(root):
        raise ValueError('Falta lock de imágenes de la instalación servidor')
    from restore import source_inventory
    source_inventory(root)  # Verify copied source has not drifted.
    lock = json.loads((root/'.runtime/server-images.json').read_text())['services']
    base = 'http://127.0.0.1:'+values['API_PORT']
    status, _, body = http(base+'/health/ready')
    ready = json.loads(body)
    if status != 200 or ready.get('status') != 'ready':
        raise ValueError('API sin versión activa lista')
    expected = hashlib.sha256()
    for path in sorted((root/'src/hm_recommender').rglob('*.py')):
        expected.update(str(path.relative_to(root/'src/hm_recommender')).encode()+b'\0'+path.read_bytes()+b'\0')
    status, _, body = http(base+'/health/live')
    if status != 200 or json.loads(body).get('source_tree_sha256') != expected.hexdigest():
        raise ValueError('Código API no coincide con fuentes inventariadas')
    services = ('api', 'postgres', 'ui') if with_ui else ('api', 'postgres')
    identities = {}
    for service in services:
        cid = compose(root, values, 'ps', '-q', service, capture=True).strip()
        if not cid:
            raise ValueError('Servicio ausente: '+service)
        inspected = json.loads(run(['docker', 'inspect', cid]))[0]
        if inspected['State'].get('Health', {}).get('Status') != 'healthy':
            raise ValueError('Servicio no saludable: '+service)
        if service in lock and inspected['Image'] != lock[service]['image']:
            raise ValueError('Imagen efectiva distinta al lock: '+service)
        for bindings in inspected['NetworkSettings']['Ports'].values():
            if any(binding['HostIp'] not in ('127.0.0.1', '::1') for binding in bindings or []):
                raise ValueError('Servicio interno publicado fuera de localhost: '+service)
        identities[service] = {'id': inspected['Id'], 'image': inspected['Image']}
    if with_ui and http('http://127.0.0.1:'+values['UI_PORT']+'/_stcore/health')[0] != 200:
        raise ValueError('UI no disponible')
    status, _, body = http(base+'/streams')
    if status != 200 or not json.loads(body).get('items'):
        raise ValueError('No hay streams para la interfaz; revisar respaldo antes de publicar')
    result = dict(status='passed', ready=ready, services=identities,
                  checked_at=datetime.now(timezone.utc).isoformat(), public_https_checked=False)
    write_private(root/'.runtime/server/local-check.json', result)
    return result


def start_ui(root, *, shared=False):
    guarded(root, 'ui', shared=shared)
    local_check(root, with_ui=False)
    values = read_configuration(root/'.env')
    compose(root, values, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', 'ui')
    return local_check(root)


def publish(root, *, shared=False):
    guarded(root, 'gateway', shared=shared)
    local_check(root)
    state = json.loads((root/'.runtime/server/gateway.json').read_text())
    if (root/'.runtime/server/Caddyfile').read_text() != gateway.caddyfile(state['domain'], state['users'], tunnel=bool(state.get('tunnel_network'))):
        raise ValueError('Caddyfile difiere del estado de usuarios; revisar configuración')
    values = read_configuration(root/'.env')
    # Validation is a bounded temporary container with no published ports.
    gateway.compose(root, values, 'run', '--rm', '--no-deps', '-T', 'gateway',
                    'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    gateway.compose(root, values, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120', 'gateway')
    # Applies atomic user updates even when the existing container was not recreated.
    gateway.compose(root, values, 'exec', '-T', 'gateway', 'caddy', 'reload',
                    '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    print('Gateway iniciado/recargado. Falta check-public y revisión desde otra red.')


def check_public(root, user, *, password=None):
    state = json.loads((root/'.runtime/server/gateway.json').read_text())
    domain = gateway.validate_domain(state['domain'])
    if user not in state['users']:
        raise ValueError('Usuario no configurado')
    if http('https://'+domain+'/')[0] != 401:
        raise ValueError('HTTPS debe rechazar al visitante sin credenciales')
    wrong = base64.b64encode((user+':invalid-'+uuid.uuid4().hex).encode()).decode()
    if http('https://'+domain+'/', {'Authorization': 'Basic '+wrong})[0] != 401:
        raise ValueError('Credencial incorrecta no rechazada')
    if password is None:
        password = getpass.getpass('Contraseña web para comprobación HTTPS: ')
    auth = {'Authorization': 'Basic '+base64.b64encode((user+':'+password).encode()).decode()}
    for path in ('/', '/_stcore/health'):
        if http('https://'+domain+path, auth)[0] != 200:
            raise ValueError('Acceso autenticado o salud UI falló')
    if http('https://'+domain+'/_stcore/stream')[0] != 401:
        raise ValueError('Ruta WebSocket no protegida')
    status, headers, _ = http('http://'+domain+'/')
    if status not in (301, 308) or headers.get('Location') != 'https://'+domain+'/':
        raise ValueError('Redirección HTTP→HTTPS no válida')
    result = dict(status='passed', domain=domain, tls_verified=True, unauthorized_status=401,
                  ui_http_status=200, websocket_auth_guard=True, browser_websocket_verified=False,
                  checked_at=datetime.now(timezone.utc).isoformat())
    write_private(root/'.runtime/server/public-check.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Checkout fuente o instalación restaurada')
    parser.add_argument('--shared-engine', action='store_true', help='Permitir coexistencia en motor Docker compartido con memoria disponible validada')
    sub = parser.add_subparsers(dest='action', required=True)
    for command in ('doctor', 'build', 'start-ui', 'check', 'publish', 'backup'):
        sub.add_parser(command)
    item = sub.add_parser('restore')
    item.add_argument('archive', type=Path);item.add_argument('destination', type=Path)
    item.add_argument('--sha256', required=True);item.add_argument('--port', type=int, default=8000)
    item = sub.add_parser('user')
    item.add_argument('--domain', required=True);item.add_argument('--name', required=True)
    item.add_argument('--remove', action='store_true')
    item.add_argument('--tunnel-network', default=None, help='Red Docker externa del túnel (ej. public-net)')
    item = sub.add_parser('check-public');item.add_argument('--user', required=True)
    args = parser.parse_args(argv);root = args.root.resolve()
    origin_file = root/'.runtime/server-origin.json'
    shared = args.shared_engine or (origin_file.is_file() and json.loads(origin_file.read_text()).get('shared_engine', False))
    try:
        if args.action == 'doctor':
            report = inventory(root)
            report['admission'] = {}
            report['admission_shared'] = {}
            for phase in ('build', 'restore', 'ui', 'gateway', 'backup'):
                try:
                    admit(report, phase);report['admission'][phase] = 'admitted'
                except ValueError as error:
                    report['admission'][phase] = str(error)
                try:
                    admit(report, phase, shared=True);report['admission_shared'][phase] = 'admitted'
                except ValueError as error:
                    report['admission_shared'][phase] = str(error)
            print(json.dumps(report, indent=2));return 0
        if args.action == 'build':build(root, shared=shared)
        elif args.action == 'restore':restore(root, args, shared=shared)
        elif args.action == 'start-ui':print(json.dumps(start_ui(root, shared=shared)))
        elif args.action == 'check':print(json.dumps(local_check(root)))
        elif args.action == 'user':
            guarded(root, 'gateway', shared=shared)
            gateway.configure(root, args.domain, args.name, remove=args.remove, tunnel_network=args.tunnel_network)
            print('Usuario configurado. Ejecutar publish para aplicar cambios al gateway.')
        elif args.action == 'publish':publish(root, shared=shared)
        elif args.action == 'check-public':print(json.dumps(check_public(root, args.user)))
        elif args.action == 'backup':
            guarded(root, 'backup', shared=shared)
            from backup import create
            create(root)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        print('DETENIDO: '+(str(error) if isinstance(error, ValueError) else type(error).__name__)+
              '. Conservar archivos/volúmenes; ver docs/server-deployment.md.', file=sys.stderr)
        return 1


if __name__ == '__main__':raise SystemExit(main())
