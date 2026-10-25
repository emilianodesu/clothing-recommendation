"""Read-only server inventory, resource admission and private files (stdlib)."""
import ipaddress
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

GIB = 2**30


def run(args, *, input=None, timeout=60):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Never echo env, input, connection strings or arbitrary subprocess output.
        raise ValueError(f'{Path(args[0]).name}: comando falló (salida {result.returncode}); revisar el servicio localmente')
    return result.stdout.strip()


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('El archivo privado no puede ser enlace')
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.partial-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value if isinstance(value, str) else json.dumps(value, indent=2)+'\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inventory(root):
    result = dict(system=platform.system(), architecture=platform.machine(),
                  free_disk_bytes=shutil.disk_usage(root).free, docker=None,
                  docker_free_disk_bytes=None, local_engine=False,
                  compose=False, buildx=False, running=[], errors=[])
    if result['system'] == 'Linux':
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        result['host_memory_bytes'] = int(memory['MemTotal'].split()[0])*1024
        result['host_available_memory_bytes'] = int(memory['MemAvailable'].split()[0])*1024
    release = Path('/etc/os-release')
    if release.exists():
        result['os_release'] = dict(line.split('=', 1) for line in release.read_text().splitlines()
                                    if line.startswith(('ID=', 'VERSION_ID=', 'PRETTY_NAME=')))
    try:
        endpoint = os.environ.get('DOCKER_HOST')
        if not endpoint or os.environ.get('DOCKER_CONTEXT'):
            context = run(['docker', 'context', 'show'])
            endpoint = run(['docker', 'context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}'])
        result['local_engine'] = endpoint.startswith('unix://')
        info = json.loads(run(['docker', 'info', '--format', '{{json .}}']))
        result['docker'] = dict(memory_bytes=info['MemTotal'], cpus=info['NCPU'],
                                architecture=info['Architecture'], version=info['ServerVersion'])
        if result['system'] == 'Linux' and result['local_engine']:
            result['docker_root'] = info['DockerRootDir']
            try:
                result['docker_free_disk_bytes'] = shutil.disk_usage(info['DockerRootDir']).free
            except OSError:
                pass  # Admission fails closed; do not assume the checkout's disk is Docker's.
        result['compose'] = run(['docker', 'compose', 'version', '--short'])
        result['buildx'] = run(['docker', 'buildx', 'version'])
        ids = run(['docker', 'ps', '-q']).split()
        if ids:
            containers = json.loads(run(['docker', 'inspect', *ids]))
            result['running'] = [dict(id=c['Id'], memory_bytes=c['HostConfig']['Memory'],
                                      project=(c['Config'].get('Labels') or {}).get('com.docker.compose.project', ''),
                                      service=(c['Config'].get('Labels') or {}).get('com.docker.compose.service', '')) for c in containers]
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        result['errors'].append(type(error).__name__ + ': Docker/Compose no disponible o inventario incompleto')
    return result


def admit(snapshot, action, *, shared=False):
    if snapshot['system'] != 'Linux':
        raise ValueError('La ruta servidor requiere Linux; el diagnóstico es portable')
    engine = snapshot.get('docker')
    if not engine or not snapshot['local_engine'] or not snapshot['compose'] or snapshot.get('errors'):
        raise ValueError('Docker/Compose local e inventario completo requeridos')
    if engine['architecture'] not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
        raise ValueError('Se requiere Linux ARM64 o AMD64')
    if engine['cpus'] < 4:
        raise ValueError('Admisión conservadora: al menos 4 CPU en Docker')
    running = snapshot['running']
    if shared:
        own = [c for c in running if c.get('project', '').startswith('hmrec_')]
        if any(c['memory_bytes'] <= 0 for c in own):
            raise ValueError('Hay contenedores hmrec sin límite RAM; revisar antes de admitir carga')
        if action in ('build', 'restore') and own:
            raise ValueError('Build/restore requieren que no haya servicios hmrec activos')
        required = {'build': 4, 'restore': 4, 'ui': .5, 'gateway': .25, 'backup': 4}[action]
        available = snapshot.get('host_available_memory_bytes', 0)
        if (required+1)*GIB > available:
            raise ValueError('RAM disponible insuficiente en modo compartido')
    else:
        if any(c['memory_bytes'] <= 0 for c in running):
            raise ValueError('Hay contenedores sin límite RAM; revisar antes de admitir carga')
        if action in ('build', 'restore') and running:
            raise ValueError('Build/restore inicial requieren motor dedicado sin contenedores activos; no se detienen')
        required = {'build': 8, 'restore': 7, 'ui': .5, 'gateway': .25, 'backup': 5}[action]
        occupied = sum(c['memory_bytes'] for c in running)
        if occupied + (required+1)*GIB > engine['memory_bytes']:
            raise ValueError('RAM insuficiente: límites activos + fase + 1GiB de margen')
    disk = 40 if action in ('build', 'restore') else 5
    if snapshot['free_disk_bytes'] < disk*GIB:
        raise ValueError(f'Espacio de disco insuficiente: se requieren {disk}GiB libres')
    docker_disk = snapshot.get('docker_free_disk_bytes')
    if docker_disk is None or docker_disk < disk*GIB:
        raise ValueError(f'Disco Docker no comprobado o insuficiente: {disk}GiB libres requeridos en DockerRootDir')
    if action == 'build' and not snapshot['buildx']:
        raise ValueError('Buildx requerido')


def check_routes(routes):
    reserved = ipaddress.ip_network('10.253.0.0/16')
    for row in routes:
        network = row.get('dst', 'default')
        if network == 'default' or row.get('dev', '').startswith(('br-', 'docker')):
            continue
        try:
            net = ipaddress.ip_network(network, strict=False)
        except ValueError:
            continue
        if net.version == 4 and net.overlaps(reserved):
            raise ValueError('Ruta del host/VPN colisiona con 10.253.0.0/16; revisar red antes de restaurar')
