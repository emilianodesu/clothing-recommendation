"""Server admission and public gateway boundaries without a Docker dependency."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import server_support as s
import server_gateway as g


class Admission(unittest.TestCase):
    def snapshot(self):
        return dict(system='Linux', architecture='x86_64', local_engine=True,
                    docker=dict(memory_bytes=16*2**30, cpus=4, architecture='x86_64'),
                    free_disk_bytes=100*2**30, docker_free_disk_bytes=100*2**30,
                    compose=True, buildx=True, running=[])

    def test_build_requires_dedicated_engine(self):
        snap=self.snapshot();snap['running']=[dict(memory_bytes=2**30)]
        with self.assertRaisesRegex(ValueError, 'dedicado'):s.admit(snap,'build')

    def test_backup_accounts_for_services_and_job(self):
        snap=self.snapshot();snap['docker']['memory_bytes']=8*2**30
        snap['running']=[dict(memory_bytes=int(5.25*2**30))]
        with self.assertRaisesRegex(ValueError, 'RAM'):s.admit(snap,'backup')
        snap['docker']['memory_bytes']=16*2**30
        s.admit(snap,'backup')

    def test_unknown_limits_or_remote_engine_do_not_pass(self):
        for change in (dict(local_engine=False),dict(running=[dict(memory_bytes=0)]),dict(docker=None)):
            snap=self.snapshot();snap.update(change)
            with self.assertRaises(ValueError):s.admit(snap,'gateway')

    def test_restore_rejects_low_disk(self):
        snap=self.snapshot();snap['free_disk_bytes']=2**30
        with self.assertRaisesRegex(ValueError,'disco'):s.admit(snap,'restore')

    def test_build_checks_docker_storage_separately(self):
        snap=self.snapshot();snap['docker_free_disk_bytes']=2**30
        with self.assertRaisesRegex(ValueError,'Docker'):s.admit(snap,'build')
        snap['docker_free_disk_bytes']=None
        with self.assertRaisesRegex(ValueError,'Docker'):s.admit(snap,'build')

    def test_vpn_supernet_conflicts_but_default_route_does_not(self):
        s.check_routes([dict(dst='default',dev='eth0'),dict(dst='10.253.1.0/24',dev='br-ab')])
        with self.assertRaisesRegex(ValueError,'10.253'):s.check_routes([dict(dst='10.0.0.0/8',dev='tun0')])

    def test_doctor_handles_containers_without_labels(self):
        replies=['local','unix:///docker.sock',json.dumps(dict(MemTotal=16*2**30,NCPU=4,Architecture='arm64',ServerVersion='test')),
                 'v2.0','buildx','id',json.dumps([dict(Id='id',HostConfig=dict(Memory=0),Config=dict(Labels=None))])]
        with tempfile.TemporaryDirectory() as tmp,patch.object(s.platform,'system',return_value='Darwin'),patch.object(s,'run',side_effect=replies):
            report=s.inventory(Path(tmp))
        self.assertEqual(report['running'][0]['project'],'')
        self.assertEqual(report['running'][0]['memory_bytes'],0)


class Gateway(unittest.TestCase):
    def test_rejects_injection_and_nonpublic_names(self):
        for name in ('https://example.com','example.com {\nrespond x','localhost','127.0.0.1','*.example.com','-bad.example.com'):
            with self.subTest(name=name),self.assertRaises(ValueError):g.validate_domain(name)
        self.assertEqual(g.validate_domain('Rec.EXAMPLE.com'),'rec.example.com')

    def test_hash_goes_via_stdin_not_argv(self):
        with patch.object(g,'run',return_value='$2a$14$'+'A'*53) as call:
            digest=g.hash_password('correct horse battery staple')
        self.assertNotIn('correct horse', ' '.join(call.call_args.args[0]))
        self.assertEqual(call.call_args.kwargs['input'],'correct horse battery staple\n')
        self.assertTrue(digest.startswith('$2a$14$'))

    def test_all_paths_authenticated_and_no_api_proxy(self):
        config=g.caddyfile('team.example.com',{'ana':'$2a$14$'+'A'*53})
        self.assertIn('basic_auth',config)
        self.assertIn('reverse_proxy ui:8501',config)
        self.assertIn('header_up -Authorization',config)
        self.assertNotIn('api:8000',config)
        self.assertLess(config.index('basic_auth'),config.index('reverse_proxy'))
        with self.assertRaises(ValueError):g.caddyfile('team.example.com',{})
        with self.assertRaises(ValueError):g.caddyfile('team.example.com',{'bad name':'hash'})

    def test_private_atomic_files_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'with spaces'/'state.json'
            s.write_private(path,{'hash':'example'})
            self.assertEqual(json.loads(path.read_text()),{'hash':'example'})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertFalse(list(path.parent.glob('*.partial*')))


if __name__=='__main__':unittest.main()
