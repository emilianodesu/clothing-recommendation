"""Anonymous public-image access and guards for the one keychain follow-up."""
import json
import hashlib
import os
from pathlib import Path

from ui_recovery_control import require, identity

PREVIOUS_RUN = 'ui-recovery-22fc4a3dcc96'
PREVIOUS_COMMIT = '9e9b9205aa86120cec902fba4a3829e58e939916'
REPORT_SHA = 'a82413d581d654b0c900fc6359f4e54a653407bd1afb21ffad11de8e2f7a3ebd'
PLUGIN_RUN = 'ui-recovery-926496b63278'
PLUGIN_COMMIT = '82fcb4bd4c3324e47d8c20c617a95e96aab96713'
PLUGIN_REPORT_SHA = '8d61f49575fd8b02fe6e6daad9035c42c0a95b4d872ebfce7caa97b639a5f4d2'
# Resolved from the public registry before preparing this continuation.
BUILDKIT = 'moby/buildkit@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea'
ANONYMOUS = {'auths': {'https://index.docker.io/v1/': {}}}


def environment(config, inherited=None):
    source = os.environ if inherited is None else inherited
    clean = {k: v for k, v in source.items()
             if not k.startswith(('DOCKER_', 'BUILDX_', 'BUILDKIT_'))}
    clean['DOCKER_CONFIG'] = str(config)
    return clean


def create_config(folder, plugin):
    require(plugin.is_file() and os.access(plugin, os.X_OK), 'Buildx executable unavailable')
    folder.mkdir(mode=0o700)
    target = folder / 'config.json'
    # An empty auths map lets Docker auto-detect osxkeychain. An empty Hub
    # entry explicitly selects anonymous file-store auth, with no secret.
    with target.open('x') as out: json.dump(ANONYMOUS, out)
    target.chmod(0o600)
    (folder / 'cli-plugins').mkdir(mode=0o700)
    (folder / 'cli-plugins/docker-buildx').symlink_to(plugin.resolve())


def selected_buildx(plugins):
    require(isinstance(plugins,list),'Docker plugin inventory unavailable')
    matches=[p for p in plugins if isinstance(p,dict) and p.get('Name')=='buildx']
    require(len(matches)==1 and not matches[0].get('Err'),'Expected one valid Docker Buildx plugin')
    raw=matches[0].get('Path')
    require(isinstance(raw,str) and bool(raw),'Docker did not report the Buildx executable')
    path=Path(raw)
    require(path.is_absolute() and path.is_file() and os.access(path,os.X_OK),'Docker Buildx executable unavailable')
    return path.resolve(strict=True)


def verify_plugin(folder, expected):
    path=folder/'cli-plugins/docker-buildx'
    require(path.is_file() and os.access(path,os.X_OK),'Private Buildx executable unavailable')
    require(hashlib.sha256(path.read_bytes()).hexdigest()==expected,'Selected Buildx executable changed')


def verify_config(folder):
    require(json.loads((folder / 'config.json').read_text()) == ANONYMOUS,
            'Private Docker config changed; inspect without using credentials')


def verify_endpoint(endpoint, home):
    require(endpoint == 'unix://' + str(home / '.colima/hm-project/docker.sock'),
            'Expected the local hm-project Unix socket')


def verify_attempt(state, published, bootstrap_log, build_log_exists, probe_log_exists):
    require(state.get('run_id') == PREVIOUS_RUN and state.get('procedure_commit') == PREVIOUS_COMMIT,
            'Unexpected prior attempt')
    require(all(state.get(k) == v for k, v in published.items()), 'Prior report and state differ')
    require(state.get('status') == 'failed' and state.get('stage') == 'complete'
            and state.get('failure_stage') == 'build_and_probe', 'Unexpected prior failure')
    require(all(state.get(k) is True for k in ('recovery_complete', 'auxiliaries_restored',
            'original_services_preserved', 'temporary_stopped', 'temporary_cleanup_complete')),
            'Prior recovery not complete')
    require(all(state.get(k) is False for k in ('build_started', 'probe_started',
            'image_verified', 'deployment_started')), 'Prior build or probe already started')
    require(not state.get('image_id') and not build_log_exists and not probe_log_exists,
            'Unexpected build/probe evidence; review first')
    require('keychain cannot be accessed' in bootstrap_log, 'Expected keychain bootstrap failure')


def verify_plugin_attempt(state, published, failure_log, unexpected_artifacts):
    require(state.get('run_id')==PLUGIN_RUN and state.get('procedure_commit')==PLUGIN_COMMIT,
            'Unexpected plugin mismatch attempt')
    require(all(state.get(k)==v for k,v in published.items()),'Plugin mismatch report and state differ')
    require(state.get('status')=='failed' and state.get('stage')=='complete'
            and state.get('failure_stage')=='public_registry_preflight', 'Unexpected plugin failure stage')
    require(state.get('public_followup') is True and state.get('maintenance_started') is False,
            'Prior plugin attempt started maintenance')
    require(all(state.get(k) is True for k in ('recovery_complete','auxiliaries_restored',
            'original_services_preserved','temporary_stopped','temporary_cleanup_complete')),
            'Prior plugin attempt did not verify preserved services')
    require(all(state.get(k) is False for k in ('build_started','probe_started','image_verified','deployment_started')),
            'Prior plugin attempt started build or probe')
    require(not unexpected_artifacts and not state.get('public_images') and not state.get('image_id')
            and not state.get('public_registry_checked'), 'Unexpected work after plugin mismatch')
    require('ValueError: Private Buildx version differs' in failure_log, 'Expected plugin version mismatch')


def verify_recovered_inventory(before, after, auxiliaries):
    require(set(before) == set(after), 'Unexpected container inventory')
    ids = {r['container_id'] for r in auxiliaries}
    require(len(ids) == 6, 'Expected six recovered auxiliaries')
    for cid, row in before.items():
        current = after[cid]
        if cid in ids:
            identity(row, current)
            require({k:v for k,v in row.items() if k!='started_at'} ==
                    {k:v for k,v in current.items() if k!='started_at'},
                    'Unexpected auxiliary change beyond the authorized start time')
            require(current['running'] and current['health'] == 'healthy', 'Auxiliary not recovered')
        else:
            require(row == current, 'Original service generation or historical container changed')
