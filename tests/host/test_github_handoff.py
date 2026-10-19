"""Real ZIP/file workflows; only the remote release store is replaced in tests."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from handoff import package_report


class ReleaseStore:
    """Small in-memory boundary double; no GitHub writes during host tests."""
    def __init__(self):
        self.private = True
        self.releases = {}
        self.payloads = {}
        self.uploads = 0

    def repository(self):
        return {'private': self.private, 'permissions': {'push': True}, 'default_branch': 'main'}

    def lookup(self, tag):
        return self.releases.get(tag)

    def create(self, tag, archive, target, notes):
        self.releases[tag] = {'tag_name': tag, 'body': notes.read_text(), 'draft': False,
                              'html_url': 'https://github.com/example/private/releases/tag/' + tag,
                              'assets': []}
        self.upload(tag, archive)

    def upload(self, tag, archive):
        self.uploads += 1
        self.payloads[tag] = archive.read_bytes()
        self.releases[tag]['assets'] = [{'name': archive.name, 'size': archive.stat().st_size,
                                         'state': 'uploaded'}]

    def download(self, tag, name, directory):
        (directory / name).write_bytes(self.payloads[tag])

    def publish(self, tag):
        self.releases[tag]['draft'] = False


class GitHubHandoffTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'scripts/github-handoff.py'
        self.assertTrue(path.exists(), 'GitHub transport implementation is missing')
        spec = importlib.util.spec_from_file_location('github_handoff', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix='github handoff ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = ReleaseStore()
        run = self.root / 'bootstrap-mac24-fixture'
        run.mkdir()
        report = {'schema_version': 1, 'kind': 'bootstrap-handoff',
                  'run_id': run.name, 'machine_id': 'mac24', 'status': 'failed',
                  'source_commit': 'a' * 40, 'source_clean': True,
                  'context': 'colima-hm-project', 'steps': [], 'warnings': []}
        (run / 'preflight.log').write_text('Synthetic diagnostic, no secrets')
        self.archive = package_report(run, report)
        self.run_id = run.name

    def test_upload_download_preserves_bytes_and_failed_run_status(self):
        self.module.upload(self.client, self.archive)
        destination = self.module.download(self.client, self.run_id, self.root / 'incoming')
        self.assertEqual(destination.read_bytes(), self.archive.read_bytes())
        with zipfile.ZipFile(destination) as bundle:
            self.assertEqual(json.loads(bundle.read('manifest.json'))['status'], 'failed')

    def test_public_repository_is_rejected_before_upload(self):
        self.client.private = False
        with self.assertRaisesRegex(ValueError, 'privado'):
            self.module.upload(self.client, self.archive)
        self.assertEqual(self.client.releases, {})

    def test_upload_retry_does_not_overwrite_remote_archive(self):
        self.module.upload(self.client, self.archive)
        self.module.upload(self.client, self.archive)
        self.assertEqual(self.client.uploads, 1)

    def test_different_remote_bytes_are_not_replaced_on_retry(self):
        self.module.upload(self.client, self.archive)
        tag = next(iter(self.client.releases))
        self.client.payloads[tag] = b'corrupted remote content'
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            self.module.upload(self.client, self.archive)
        self.assertEqual(self.client.uploads, 1)

    def test_corrupt_download_never_publishes_final_file(self):
        self.module.upload(self.client, self.archive)
        tag = next(iter(self.client.releases))
        self.client.payloads[tag] = b'corrupted remote content'
        incoming = self.root / 'incoming'
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            self.module.download(self.client, self.run_id, incoming)
        self.assertEqual(list(incoming.glob('*.zip')), [])

    def test_existing_local_file_is_preserved_on_conflict(self):
        self.module.upload(self.client, self.archive)
        incoming = self.root / 'incoming'
        incoming.mkdir()
        destination = incoming / self.archive.name
        destination.write_bytes(b'keep this original file')
        with self.assertRaises(ValueError):
            self.module.download(self.client, self.run_id, incoming)
        self.assertEqual(destination.read_bytes(), b'keep this original file')

    def test_missing_asset_in_our_draft_is_recoverable(self):
        self.module.upload(self.client, self.archive)
        tag = next(iter(self.client.releases))
        self.client.releases[tag]['draft'] = True
        self.client.releases[tag]['assets'] = []
        self.module.upload(self.client, self.archive)
        self.assertEqual(self.client.uploads, 2)
        self.assertFalse(self.client.releases[tag]['draft'])

    def test_non_report_inventory_is_rejected_before_network(self):
        forbidden = self.root / 'forbidden.zip'
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(forbidden, 'w') as target:
            manifest = json.loads(source.read('manifest.json'))
            data = b'private data'
            manifest['files'].append({'path': 'dataset.txt', 'bytes': len(data),
                                      'sha256': hashlib.sha256(data).hexdigest()})
            for name in source.namelist():
                target.writestr(name, json.dumps(manifest) if name == 'manifest.json' else source.read(name))
            target.writestr('dataset.txt', data)
        with self.assertRaisesRegex(ValueError, 'reporte'):
            self.module.upload(self.client, forbidden)
        self.assertEqual(self.client.releases, {})

    def test_draft_is_found_when_tag_endpoint_returns_404(self):
        client = self.module.GitHub('example/private')
        draft = {'id': 19, 'tag_name': 'handoff-' + self.run_id, 'draft': True, 'assets': []}
        # GitHub's tag endpoint returns only published releases; listing includes drafts.
        with mock.patch.object(client, 'api', return_value=None), \
                mock.patch.object(client, 'releases', return_value=[draft]):
            self.assertEqual(client.lookup(draft['tag_name']), draft)


if __name__ == '__main__':
    unittest.main()
