import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from hm_recommender.data.download import download_archive, SafeRedirect


class Response(io.BytesIO):
    def __init__(self, data, status, headers):
        super().__init__(data)
        self.status, self.headers = status, headers


class DownloadTests(unittest.TestCase):
    def test_resume_checks_range_and_preserves_complete_file(self):
        payload = b'complete-source'
        expected = {'bytes':len(payload), 'sha256':hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'source.zip'
            target.with_suffix('.zip.partial').write_bytes(payload[:3])
            calls = []
            def fetch(request, timeout):
                calls.append(request.get_header('Range'))
                return Response(payload[3:], 206, {'Content-Range':f'bytes 3-{len(payload)-1}/{len(payload)}'})
            download_archive(target, expected, fetch=fetch, authorization='Bearer synthetic')
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(calls, ['bytes=3-'])
            download_archive(target, expected, fetch=lambda *a, **k: self.fail('Existing complete source uses no network'))

    def test_ignored_range_restarts_without_appending(self):
        payload = b'complete-source'
        expected = {'bytes':len(payload), 'sha256':hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'source.zip'
            target.with_suffix('.zip.partial').write_bytes(b'bad')
            download_archive(target, expected, authorization='Bearer synthetic',
                             fetch=lambda *a, **k: Response(payload, 200, {}))
            self.assertEqual(target.read_bytes(), payload)

    def test_wrong_content_is_never_published(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'source.zip'
            with self.assertRaises(ValueError):
                download_archive(target, {'bytes':3, 'sha256':'0'*64}, authorization='Bearer synthetic',
                                 fetch=lambda *a, **k: Response(b'bad', 200, {}))
            self.assertFalse(target.exists())

    def test_redirect_does_not_forward_token(self):
        from urllib.request import Request
        original = Request('https://www.kaggle.com/download', headers={'Authorization':'Bearer synthetic', 'Range':'bytes=3-'})
        result = SafeRedirect().redirect_request(original, None, 302, '', {}, 'https://storage.googleapis.com/file')
        self.assertIsNone(result.get_header('Authorization'))
        self.assertEqual(result.get_header('Range'), 'bytes=3-')
        with self.assertRaises(ValueError):
            SafeRedirect().redirect_request(original, None, 302, '', {}, 'http://storage.googleapis.com/file')
