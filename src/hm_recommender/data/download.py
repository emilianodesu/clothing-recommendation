"""Host stdlib downloader: isolated credentials, verified TLS, resumable source."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener

from hm_recommender.data.io import locked, sha256, atomic_json
from hm_recommender.data.validate import COMPETITION, HM_SOURCE

URL = 'https://www.kaggle.com/api/v1/competitions/data/download-all/' + COMPETITION


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise ValueError('Se rechaza una redirección sin TLS')
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
            result.remove_header('Authorization')
        return result


def credentials():
    """Used only to authenticate to Kaggle; never printed, serialized or passed to Docker."""
    directory = Path(os.environ.get('KAGGLE_CONFIG_DIR', str(Path.home() / '.kaggle')))
    token = os.environ.get('KAGGLE_API_TOKEN', '').strip()
    if not token and (directory / 'access_token').is_file():
        token = (directory / 'access_token').read_text().strip()
    if token:
        return 'Bearer ' + token
    username, key = os.environ.get('KAGGLE_USERNAME'), os.environ.get('KAGGLE_KEY')
    if not (username and key) and (directory / 'kaggle.json').is_file():
        saved = json.loads((directory / 'kaggle.json').read_text())
        username, key = saved.get('username'), saved.get('key')
    if username and key:
        return 'Basic ' + base64.b64encode((username + ':' + key).encode()).decode()
    raise ValueError('Configura una credencial Kaggle propia: ~/.kaggle/access_token o ~/.kaggle/kaggle.json')


def opener():
    context = ssl.create_default_context()
    extra = os.environ.get('HMREC_EXTRA_CA_FILE')
    if extra:
        context.load_verify_locations(cafile=extra)
    return build_opener(SafeRedirect(), HTTPSHandler(context=context))


def probe_access():
    request = Request(URL, headers={'Authorization':credentials(), 'Range':'bytes=0-1023', 'Accept-Encoding':'identity'})
    try:
        with opener().open(request, timeout=45) as response:
            prefix = response.read(1024)
            if response.status not in (200, 206) or not prefix.startswith(b'PK'):
                raise ValueError('La respuesta no es un ZIP; comprobar acceso y reglas de Kaggle')
            return {'status':'passed', 'http_status':response.status, 'zip_prefix':True, 'bytes_read':len(prefix)}
    except HTTPError as error:
        raise ValueError('Kaggle HTTP ' + str(error.code) + '; comprobar token y aceptación de reglas') from None
    except URLError:
        raise ValueError('No se pudo conectar con TLS a Kaggle; comprobar red y SSL_CERT_FILE') from None


def download_archive(destination: Path, expected=HM_SOURCE, *, fetch=None, authorization=None, attempts=5):
    destination = Path(destination)
    with locked(destination.parent):
        if destination.exists():
            if destination.stat().st_size != expected['bytes'] or sha256(destination) != expected['sha256']:
                raise ValueError('El destino existente no coincide; se conserva para diagnóstico')
            return destination
        partial = destination.with_suffix(destination.suffix + '.partial')
        if partial.is_symlink() or destination.is_symlink():
            raise ValueError('El destino de descarga debe ser un archivo local')
        if partial.exists() and partial.stat().st_size > expected['bytes']:
            raise ValueError('El temporal excede el tamaño esperado; conservarlo para diagnóstico')
        remaining = expected['bytes'] - (partial.stat().st_size if partial.exists() else 0)
        if shutil.disk_usage(destination.parent).free < remaining + 512 * 1024 * 1024:
            raise ValueError('Espacio insuficiente para completar la descarga')
        fetch = fetch or opener().open
        for attempt in range(attempts):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset == expected['bytes']:
                break
            request = Request(URL, headers={'Authorization':authorization or credentials(),
                'Range':f'bytes={offset}-', 'Accept-Encoding':'identity', 'User-Agent':'hmrec/0.1'})
            try:
                with fetch(request, timeout=90) as response:
                    if response.status == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range',''))
                        if not match or int(match[1]) != offset or int(match[3]) != expected['bytes']:
                            raise ValueError('El servidor devolvió un rango incompatible')
                        mode = 'ab'
                    elif response.status == 200:
                        mode, offset = 'wb', 0
                    else:
                        raise ValueError('Respuesta HTTP inesperada')
                    with partial.open(mode) as stream:
                        partial.chmod(0o600)
                        last_update = time.monotonic()
                        while block := response.read(4 * 1024 * 1024):
                            offset += len(block)
                            if offset > expected['bytes']:
                                raise ValueError('La descarga excede el tamaño esperado')
                            stream.write(block)
                            if time.monotonic() - last_update >= 5:
                                atomic_json(destination.with_suffix('.progress.json'), {'status':'downloading', 'bytes':offset, 'total_bytes':expected['bytes']})
                                last_update = time.monotonic()
                        stream.flush()
                        os.fsync(stream.fileno())
                if offset == expected['bytes']:
                    break
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                    raise ValueError('Kaggle HTTP ' + str(error.code) + '; temporal conservado') from None
            except (URLError, TimeoutError, ConnectionError):
                if attempt == attempts - 1:
                    raise ValueError('Descarga interrumpida por red/TLS; temporal conservado para reintentar') from None
            time.sleep(min(2 ** attempt, 16))
        if not partial.exists() or partial.stat().st_size != expected['bytes']:
            raise ValueError('Descarga incompleta; vuelve a ejecutar para reanudar')
        if sha256(partial) != expected['sha256']:
            raise ValueError('SHA-256 incorrecto; temporal conservado, no publicado')
        partial.replace(destination)
        atomic_json(destination.with_suffix('.progress.json'), {'status':'complete', 'bytes':expected['bytes'], 'sha256':expected['sha256']})
        return destination
