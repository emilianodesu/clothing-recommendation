#!/usr/bin/env python3
"""Prepare Colima and Docker plugins without stopping active workloads."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--cpus', type=int, default=4)
    parser.add_argument('--memory', type=int, default=8, help='GiB')
    parser.add_argument('--disk', type=int, default=100, help='GiB; never shrinks disks')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('Este instalador es para macOS. Consulta docs/setup.md para Linux.')
    if not shutil.which('brew'):
        raise RuntimeError('Instala Homebrew desde https://brew.sh y vuelve a ejecutar.')
    if not shutil.which('ruby'):
        raise RuntimeError('Se necesita Ruby con YAML para conservar la configuración de Colima; consulta docs/setup.md.')
    if not args.profile or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.profile):
        raise RuntimeError('El perfil sólo puede contener letras minúsculas, números, guiones y guiones bajos.')
    host_memory = int(run('sysctl', '-n', 'hw.memsize', capture=True)) / 2**30
    host_cpus = int(run('sysctl', '-n', 'hw.ncpu', capture=True))
    if not 1 <= args.cpus <= host_cpus or not 2 <= args.memory <= host_memory - 4 or args.disk < 20:
        raise RuntimeError('Recursos inválidos: deja al menos 4 GiB al host y no excedas sus CPU.')

    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1', HOMEBREW_NO_INSTALL_CLEANUP='1')
    # Existing packages are kept; installing this script does not upgrade Docker or Colima.
    for package in ('docker', 'colima', 'docker-compose', 'docker-buildx'):
        installed = subprocess.run(['brew', 'list', '--versions', package], env=env,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if installed.returncode:
            print('Instalando con Homebrew:', package, flush=True)
            subprocess.run(['brew', 'install', package], env=env, check=True)

    plugin_dir = Path(os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker'))) / 'cli-plugins'
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for plugin in ('compose', 'buildx'):
        if subprocess.run(['docker', plugin, 'version'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            continue
        destination = plugin_dir / ('docker-' + plugin)
        if destination.exists() or destination.is_symlink():
            raise RuntimeError('Hay un plugin existente que no funciona: ' + str(destination))
        prefix = run('brew', '--prefix', 'docker-' + plugin, capture=True).strip()
        target = Path(prefix) / 'bin' / ('docker-' + plugin)
        if not target.is_file():
            raise RuntimeError('No se encontró el plugin instalado: ' + str(target))
        destination.symlink_to(target)
        run('docker', plugin, 'version')

    context_env = dict(os.environ)
    context_env.pop('DOCKER_CONTEXT', None)
    context_env.pop('DOCKER_HOST', None)
    original_context = subprocess.run(['docker', 'context', 'show'], check=True,
        capture_output=True, text=True, env=context_env).stdout.strip()
    rows = run('colima', 'list', '--json', capture=True)
    profiles = [json.loads(line) for line in rows.splitlines() if line.strip()]
    current = next((p for p in profiles if p['name'] == args.profile), None)
    context = 'colima' if args.profile == 'default' else 'colima-' + args.profile
    if current and current.get('runtime') != 'docker':
        raise RuntimeError('El perfil existente usa otro runtime; elige un perfil nuevo.')
    project_root = Path(__file__).resolve().parents[1]
    colima_home = Path(os.environ.get('COLIMA_HOME', str(Path.home() / '.colima')))
    config = colima_home / args.profile / 'colima.yaml'
    settings = {}
    if config.exists():
        # Parse only relevant settings with macOS Ruby; never print the full private config.
        ruby = 'c = YAML.safe_load(File.read(ARGV[0])); puts JSON.generate({"mounts" => c["mounts"], "kubernetes_enabled" => c.fetch("kubernetes", {}).fetch("enabled", false)})'
        settings = json.loads(run('ruby', '-ryaml', '-rjson', '-e', ruby, str(config), capture=True))
    mounts = settings.get('mounts') or [{'location': str(Path.home()), 'writable': True}]
    covered = False
    for mount in mounts:
        location = Path(mount['location']).expanduser().resolve()
        mountpoint = Path(mount.get('mountPoint') or str(location)).expanduser().resolve()
        if mount.get('writable') and mountpoint == location and (location == project_root or location in project_root.parents):
            covered = True
    mount_flags = []
    needs_mount = not current or not covered
    if needs_mount:
        mounts = mounts + [{'location': str(project_root), 'writable': True}] if current else [{'location': str(project_root), 'writable': True}]
        for mount in mounts:
            if set(mount) - {'location', 'mountPoint', 'writable'}:
                raise RuntimeError('El perfil tiene opciones de montaje especiales; usa un perfil nuevo.')
            location = str(Path(mount['location']).expanduser())
            mountpoint = str(Path(mount['mountPoint']).expanduser()) if mount.get('mountPoint') else ''
            if any(c in location + mountpoint for c in ',:\n'):
                raise RuntimeError('Los montajes con coma, dos puntos o salto de línea requieren configuración manual.')
            value = location + (':' + mountpoint if mountpoint else '')
            if mount.get('writable'):
                value += ':w'
            mount_flags += ['--mount', value]
    cpus = max(args.cpus, current.get('cpus', 0) if current else 0)
    memory = max(args.memory, int(current['memory'] / 2**30) if current else 0)
    disk = max(args.disk, int(current['disk'] / 2**30) if current else 0)
    sufficient = current and current['cpus'] >= args.cpus and current['memory'] >= args.memory * 2**30 and current['disk'] >= args.disk * 2**30
    if current and current['status'] == 'Running' and sufficient and not needs_mount:
        print('Colima ya tiene los recursos solicitados; no se reinicia.')
    else:
        if settings.get('kubernetes_enabled'):
            raise RuntimeError('El perfil tiene Kubernetes habilitado; usa un perfil separado para evitar interrumpirlo.')
        if current and current['status'] == 'Running':
            running = run('docker', '--context', context, 'ps', '-q', capture=True).strip()
            if running:
                raise RuntimeError('Hay contenedores activos. Detén sus trabajos o elige otro perfil; no se reiniciará Colima.')
        if config.exists():
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = config.with_name('colima.yaml.before-hm-' + stamp)
            shutil.copy2(config, backup)
            backup.chmod(0o600)
        if current and current['status'] == 'Running':
            run('colima', '--profile', args.profile, 'stop')
        run('colima', '--profile', args.profile, 'start', '--runtime', 'docker',
            '--cpu', str(cpus), '--memory', str(memory), '--disk', str(disk), '--activate=false',
            *mount_flags, *([] if current else ['--template=false']))
        # Colima stop may reset the selected context even with --activate=false on start.
        subprocess.run(['docker', 'context', 'use', original_context], check=True, env=context_env)
    run('docker', '--context', context, 'info', '--format',
        'Engine={{.ServerVersion}} CPU={{.NCPU}} MemoryBytes={{.MemTotal}} Arch={{.Architecture}}')
    run('colima', '--profile', args.profile, 'ssh', '--', 'test', '-f',
        str(project_root / 'docker' / 'smoke' / 'fixture.txt'))
    print('Preparación terminada. Para usar este contexto en esta terminal:')
    print('export DOCKER_CONTEXT=' + context)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError, KeyError) as error:
        print('ERROR:', error, file=sys.stderr)
        sys.exit(1)
