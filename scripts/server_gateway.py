"""Team-only Caddy gateway. Credentials never enter process arguments or Git."""
import getpass
import json
from pathlib import Path
import re
from server_support import run, write_private

IMAGE = 'caddy:2.11.7-alpine@sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f'


def validate_domain(domain):
    domain = domain.lower()
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(domain) > 253 or not re.fullmatch(label+r'(?:\.'+label+r')*\.[a-z]{2,63}', domain):
        raise ValueError('Usar un nombre DNS público, sin protocolo, ruta, puerto ni wildcard')
    if domain.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Dominio público requerido')
    return domain


def hash_password(password):
    if len(password) < 16 or len(password.encode()) > 72 or '\n' in password or '\r' in password:
        raise ValueError('Usar contraseña de al menos 16 caracteres y máximo 72 bytes, sin saltos')
    digest = run(['docker', 'run', '--rm', '-i', '--network', 'none', '--memory', '256m', '--cpus', '1',
                  IMAGE, 'caddy', 'hash-password', '--algorithm', 'bcrypt', '--bcrypt-cost', '14'],
                 input=password+'\n', timeout=60)
    if not re.fullmatch(r'\$2[aby]\$14\$[./A-Za-z0-9]{53}', digest):
        raise ValueError('Respuesta bcrypt inválida')
    return digest


def caddyfile(domain, users, *, tunnel=False):
    domain = validate_domain(domain)
    if not users:
        raise ValueError('Se requiere al menos un usuario del equipo')
    entries = []
    for user, digest in sorted(users.items()):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', user) or not re.fullmatch(r'\$2[aby]\$14\$[./A-Za-z0-9]{53}', digest):
            raise ValueError('Usuario o hash inválido')
        entries.append('            '+user+' '+digest)
    site = 'http://'+domain if tunnel else domain
    return '''{
    admin localhost:2019
}
'''+site+''' {
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
        X-Robots-Tag "noindex, nofollow"
        -Server
    }
    route {
        basic_auth {
'''+ '\n'.join(entries)+'''
        }
        reverse_proxy ui:8501 {
            header_up -Authorization
        }
    }
}
'''


def configure(root, domain, user, *, remove=False, password=None, tunnel_network=None):
    folder = root/'.runtime/server'
    path = folder/'gateway.json'
    state = json.loads(path.read_text()) if path.exists() else {'domain': validate_domain(domain), 'users': {}}
    if validate_domain(domain) != state['domain']:
        raise ValueError('Cambio de dominio requiere plan explícito y conservar configuración anterior')
    if tunnel_network is not None:
        if tunnel_network and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}', tunnel_network):
            raise ValueError('Red de túnel inválida')
        if tunnel_network:
            state['tunnel_network'] = tunnel_network
        else:
            state.pop('tunnel_network', None)
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', user):
        raise ValueError('Usuario: minúsculas, dígitos, guión; iniciar con letra, máximo 32')
    if remove:
        if user not in state['users']:
            raise ValueError('Usuario no existe')
        del state['users'][user]
    else:
        if password is None:
            password = getpass.getpass('Contraseña web (no se guarda en claro): ')
            if getpass.getpass('Repetir contraseña: ') != password:
                raise ValueError('Contraseñas distintas')
        state['users'][user] = hash_password(password)
    rendered = caddyfile(state['domain'], state['users'], tunnel=bool(state.get('tunnel_network')))
    # Mount directory, not individual file, so atomic updates become visible on reload.
    write_private(folder/'Caddyfile', rendered)
    write_private(path, state)
    return state


def compose(root, values, *args):
    import os
    env = dict(os.environ)
    for key in ('COMPOSE_FILE', 'COMPOSE_PROFILES'):
        env.pop(key, None)
    env.update(values)
    # Compose -p rewrites COMPOSE_PROJECT_NAME during interpolation. Keep the
    # application's network identity separate from this gateway's project.
    env['HMREC_APP_PROJECT'] = values['COMPOSE_PROJECT_NAME']
    import subprocess
    files = ['-f', str(root/'deploy/compose.gateway.yaml')]
    state_path = root/'.runtime/server/gateway.json'
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        if state.get('tunnel_network'):
            env.setdefault('GATEWAY_BIND_IP', '127.0.0.1')
            env.setdefault('GATEWAY_HTTP_PORT', '18080')
            env.setdefault('GATEWAY_HTTPS_PORT', '18443')
            override = root/'.runtime/server/gateway.override.json'
            write_private(override, {
                'services': {
                    'gateway': {
                        'networks': {
                            'application': {},
                            'tunnel': {'aliases': ['hm-gateway']},
                        },
                    }
                },
                'networks': {
                    'tunnel': {'external': True, 'name': state['tunnel_network']}
                },
            })
            files += ['-f', str(override)]
    command = ['docker', 'compose', '--project-directory', str(root), '--env-file', str(root/'.env'),
               '-p', values['COMPOSE_PROJECT_NAME']+'_gateway', *files, *args]
    subprocess.run(command, env=env, check=True)
