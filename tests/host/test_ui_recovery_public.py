import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import mac24_ui_recovery as runner
import ui_recovery_public as public
from test_ui_recovery_control import inventory
from ui_recovery_control import select_targets


class PublicDocker(unittest.TestCase):
    def test_missing_ambiguous_or_invalid_plugin_never_falls_back_to_path(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin=Path(folder)/'docker-buildx';plugin.write_text('fixture');plugin.chmod(0o700)
            valid={'Name':'buildx','Path':str(plugin),'Version':'v1'}
            self.assertEqual(public.selected_buildx([valid]),plugin.resolve())
            for entries in (None,[],[valid,valid],[dict(valid,Err='invalid')],
                            [dict(valid,Path='relative')],[dict(valid,Path='')]):
                with self.subTest(entries=entries),self.assertRaises(ValueError):public.selected_buildx(entries)

    def test_changed_plugin_is_rejected_before_docker_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);plugin=root/'selected';plugin.write_text('first');plugin.chmod(0o700)
            digest=hashlib.sha256(plugin.read_bytes()).hexdigest()
            public.create_config(root/'docker-public',plugin)
            plugin.write_text('replaced')
            with patch.object(runner,'STATE',root),patch.object(runner,'run_command') as command:
                with self.assertRaisesRegex(ValueError,'executable changed'):
                    runner.build_docker({'public_followup':True,'buildx_plugin_sha256':digest},'buildx','version')
            command.assert_not_called()

    def test_version_mismatch_still_blocks_before_registry_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);plugin=root/'selected';plugin.write_text('fixture');plugin.chmod(0o700)
            def query(*args):
                if args[0]=='context':return 'unix://'+str(Path.home()/'.colima/hm-project/docker.sock')
                if args[-1]=='{{.ID}}':return 'engine-id'
                if args[-1]=='{{json .ClientInfo.Plugins}}':return json.dumps([{'Name':'buildx','Path':str(plugin)}])
                raise AssertionError(args)
            def command(args,**kwargs):
                if 'context' in args and 'create' in args:return ''
                if 'info' in args:return 'engine-id'
                if args[-2:]==['buildx','version']:return 'different-version'
                raise AssertionError('Unexpected registry access: '+str(args))
            state={'public_followup':True,'buildx':'expected-version'}
            with patch.object(runner,'STATE',root),patch.object(runner,'query',side_effect=query), \
                 patch.object(runner,'run_command',side_effect=command):
                with self.assertRaisesRegex(ValueError,'Private Buildx version differs'):
                    runner.prepare_public_registry(state)
            self.assertEqual(state['private_buildx'],'different-version')
            self.assertFalse(state['buildx_identity_verified'])
            self.assertNotIn('public_images',state)

    def test_preparation_uses_docker_selected_plugin_when_path_differs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); selected=root/'docker-selected'; alternate=root/'path-selected'
            for path in (selected,alternate):path.write_text('fixture');path.chmod(0o700)
            def query(*args):
                if args[0]=='context':return 'unix://'+str(Path.home()/'.colima/hm-project/docker.sock')
                if args[-1]=='{{.ID}}':return 'engine-id'
                if args[-1]=='{{json .ClientInfo.Plugins}}':
                    return json.dumps([{'Name':'buildx','Path':str(selected),'Version':'v0.38.1'}])
                raise AssertionError(args)
            state={'public_followup':True,'buildx':'github.com/docker/buildx v0.38.1 fixture'}
            with patch.object(runner,'STATE',root),patch.object(runner,'query',side_effect=query), \
                 patch.object(runner.shutil,'which',return_value=str(alternate)), \
                 patch.object(runner,'run_command',side_effect=RuntimeError('stop before context creation')):
                with self.assertRaises(RuntimeError):runner.prepare_public_registry(state)
            self.assertEqual((root/'docker-public/cli-plugins/docker-buildx').resolve(),selected.resolve())

    def test_private_config_has_anonymous_entry_and_no_personal_store(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); plugin = root / 'docker-buildx'
            plugin.write_text('fixture'); plugin.chmod(0o700)
            config = root / 'config'
            public.create_config(config, plugin)
            value = json.loads((config / 'config.json').read_text())
            self.assertEqual(value, {'auths': {'https://index.docker.io/v1/': {}}})
            self.assertEqual((config / 'cli-plugins/docker-buildx').resolve(), plugin.resolve())
            self.assertEqual(config.stat().st_mode & 0o077, 0)
            self.assertEqual((config / 'config.json').stat().st_mode & 0o077, 0)
            with self.assertRaises(FileExistsError): public.create_config(config, plugin)

    def test_environment_cannot_override_private_config_or_builder(self):
        original = {'PATH': '/bin', 'SSL_CERT_FILE': '/etc/ssl/cert.pem',
                    'DOCKER_AUTH_CONFIG': 'secret', 'DOCKER_CONFIG': 'personal',
                    'DOCKER_HOST': 'tcp://other', 'DOCKER_CONTEXT': 'other',
                    'BUILDX_CONFIG': 'personal', 'BUILDX_BUILDER': 'other',
                    'BUILDKIT_HOST': 'tcp://other'}
        clean = public.environment(Path('/private/config'), original)
        self.assertEqual(clean, {'PATH': '/bin', 'SSL_CERT_FILE': '/etc/ssl/cert.pem',
                                'DOCKER_CONFIG': '/private/config'})
        self.assertEqual(original['DOCKER_AUTH_CONFIG'], 'secret')

    def test_only_known_local_socket_is_accepted(self):
        home = Path('/Users/operator')
        public.verify_endpoint('unix:///Users/operator/.colima/hm-project/docker.sock', home)
        for host in ('tcp://host:2375', 'unix:///tmp/other.sock'):
            with self.assertRaises(ValueError): public.verify_endpoint(host, home)


class FollowupGuard(unittest.TestCase):
    def test_plugin_followup_requires_exact_unstarted_preserved_attempt(self):
        root=Path(__file__).resolve().parents[2]
        published=json.loads((root/'evidence/ui-recovery-public-mac24.json').read_text())
        state=dict(published);log='ValueError: Private Buildx version differs'
        public.verify_plugin_attempt(state,published,log,False)
        for key,value in [('run_id','other'),('maintenance_started',True),('build_started',True),
                          ('recovery_complete',False),('public_images',['unexpected']),('image_id','image')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                public.verify_plugin_attempt(dict(state,**{key:value}),published,log,False)
        for log,exists in [('',False),('ValueError: Private Buildx version differs',True)]:
            with self.assertRaises(ValueError):public.verify_plugin_attempt(state,published,log,exists)

    def test_plugin_followup_preserves_entire_inventory_and_evidence_chain(self):
        source=Path(__file__).resolve().parents[2]/'evidence/ui-recovery-public-mac24.json'
        published=json.loads(source.read_text());rows=inventory()
        antecedent={k:published[k] for k in ('prior_run_id','prior_state_sha256')}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);previous=root/'.runtime/ui-recovery-public-v1';previous.mkdir(parents=True)
            (root/'evidence').mkdir();(root/'evidence/ui-recovery-public-mac24.json').write_bytes(source.read_bytes())
            state=dict(published,before=rows,builder_container='b',probe_name='p')
            (previous/'state.json').write_text(json.dumps(state))
            (previous/'failure.log').write_text('ValueError: Private Buildx version differs')
            with patch.object(runner,'ROOT',root),patch.object(runner,'named',return_value=None):
                result=runner.plugin_followup_inventory(rows,antecedent)
                self.assertEqual(result['plugin_source_run_id'],published['run_id'])
                changed=copy.deepcopy(rows);changed['original-mlflow']['started_at']='different'
                with self.assertRaises(ValueError):runner.plugin_followup_inventory(changed,antecedent)
                with self.assertRaises(ValueError):runner.plugin_followup_inventory(rows,dict(antecedent,prior_state_sha256='wrong'))
                (previous/'builder-create.log').touch()
                with self.assertRaises(ValueError):runner.plugin_followup_inventory(rows,antecedent)

    def source(self):
        root = Path(__file__).resolve().parents[2]
        published = json.loads((root / 'evidence/ui-recovery-maintenance-mac24.json').read_text())
        return published, dict(published)

    def test_only_exact_failed_restored_attempt_is_admitted(self):
        published, state = self.source()
        public.verify_attempt(state, published, 'keychain cannot be accessed', False, False)
        for key, value in [('build_started', True), ('probe_started', True),
                           ('recovery_complete', False), ('run_id', 'other')]:
            changed = dict(state, **{key: value})
            with self.assertRaises(ValueError):
                public.verify_attempt(changed, published, 'keychain cannot be accessed', False, False)
        for log, build, probe in [('', False, False), ('keychain cannot be accessed', True, False),
                                  ('keychain cannot be accessed', False, True)]:
            with self.assertRaises(ValueError): public.verify_attempt(state, published, log, build, probe)

    def test_only_six_auxiliaries_may_have_new_generation(self):
        before = inventory(); aux = select_targets(before, 'original', 'restored-postgres')
        after = copy.deepcopy(before)
        for row in aux: after[row['container_id']]['started_at'] = 'after recovery'
        public.verify_recovered_inventory(before, after, aux)
        changed = copy.deepcopy(after); changed['original-api']['started_at'] = 'unexpected'
        with self.assertRaises(ValueError): public.verify_recovered_inventory(before, changed, aux)
        changed = copy.deepcopy(after); changed['restored-api']['image_id'] = 'replacement'
        with self.assertRaises(ValueError): public.verify_recovered_inventory(before, changed, aux)
        changed = copy.deepcopy(after); changed['original-mlflow']['running'] = False
        with self.assertRaises(ValueError): public.verify_recovered_inventory(before, changed, aux)
        changed = copy.deepcopy(after); changed['original-mlflow']['restart_count'] = 31
        with self.assertRaises(ValueError): public.verify_recovered_inventory(before, changed, aux)
        changed = copy.deepcopy(after); changed['job'] = dict(after['original-api'])
        with self.assertRaises(ValueError): public.verify_recovered_inventory(before, changed, aux)


class RegistryBeforeMaintenance(unittest.TestCase):
    def test_restore_only_after_registry_failure_never_starts_changed_service(self):
        import contextlib
        import io
        from test_ui_recovery_runner import Guard
        for flag,subfolder,changed in [(flag,subfolder,changed) for flag,subfolder in
                [('--public-registry-followup','ui-recovery-public-v1'),('--buildx-followup','ui-recovery-buildx-v1')]
                for changed in (False,True)]:
            with self.subTest(flag=flag,changed=changed), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); folder=root/'.runtime'/subfolder
                folder.mkdir(parents=True)
                rows=inventory(); after=copy.deepcopy(rows)
                if changed:after['original-mlflow']['running']=False
                state=dict(procedure_commit='a'*40,runner_files={},status='failed',before=rows,
                           ready={},maintenance_started=False,recovery_complete=False)
                (folder/'state.json').write_text(json.dumps(state))
                args=['runner',flag,'--restore-only','--expected-commit','a'*40]
                with patch.object(runner,'ROOT',root),patch.object(runner,'STATE',folder), \
                     patch.object(runner,'read_configuration',return_value={'ARTIFACTS_DIR':directory}), \
                     patch.object(runner,'AwakeGuard',return_value=contextlib.nullcontext(Guard())), \
                     patch.object(runner.signal,'signal'),patch.object(sys,'argv',args), \
                     patch.object(runner,'snapshot',return_value=after),patch.object(runner,'ready',return_value={}), \
                     patch.object(runner,'recover') as recover,patch.object(runner,'docker') as docker, \
                     contextlib.redirect_stdout(io.StringIO()):
                    if changed:
                        with self.assertRaises(ValueError):runner.main()
                    else:self.assertEqual(runner.main(),0)
                    recover.assert_not_called();docker.assert_not_called()

    def test_public_registry_failure_never_stops_or_starts_services(self):
        import contextlib
        import io
        from test_ui_recovery_runner import Guard
        rows = inventory(); state = dict(before=rows, ready={}, public_followup=True,
            image_verified=False, recovery_complete=False, auxiliaries_restored=False,
            build_started=False, probe_started=False)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, 'STATE', Path(directory)), \
                 patch.object(runner, 'prepare_public_registry', side_effect=ValueError('registry unavailable')), \
                 patch.object(runner, 'snapshot', return_value=rows), \
                 patch.object(runner, 'ready', return_value={}), \
                 patch.object(runner, 'docker') as docker, \
                 patch.object(runner, 'recover') as recover, \
                 contextlib.redirect_stdout(io.StringIO()):
                runner.execute_prepared(state, {}, Guard())
        docker.assert_not_called(); recover.assert_not_called()
        self.assertEqual(state['failure_stage'], 'public_registry_preflight')
        self.assertFalse(state['maintenance_started'])
        self.assertTrue(state['original_services_preserved'])
        self.assertEqual(state['status'], 'failed')
