"""Contracts for a UI-only change and a bounded recovery window."""
import copy
from ui_recovery_control import require


def compose_command(root,values,override,*args):
    command=['docker','--context','colima-hm-project','compose','--project-directory',str(root),
             '--env-file',str(root/'.env'),'--file',str(root/'compose.yaml')]
    if override:command+=['--file',str(override)]
    return command+['--project-name',values['COMPOSE_PROJECT_NAME'],*args]


def apply_command(root,values,override):
    return compose_command(root,values,override,'up','-d','--no-deps','--no-build','--pull','never',
                           '--wait','--wait-timeout','90','ui')


def verify_override(base,resolved,image):
    expected=copy.deepcopy(base);expected['services']['ui']['image']=image
    require(resolved==expected,'Override changes more than the UI image')


def ui_contract(raw,environment_keys):
    config=raw['Config'];host=raw['HostConfig']
    env=dict(item.split('=',1) for item in config.get('Env',[]) if '=' in item)
    return dict(command=config.get('Cmd'),entrypoint=config.get('Entrypoint'),user=config['User'],
        environment={key:env.get(key) for key in environment_keys},healthcheck=config.get('Healthcheck'),
        host={key:host.get(key) for key in ('Memory','NanoCpus','ReadonlyRootfs','PortBindings','Binds','Tmpfs','RestartPolicy')},
        networks=sorted(raw['NetworkSettings']['Networks']),
        mounts=sorted([[m.get('Type'),m.get('Source'),m.get('Destination'),m.get('RW')] for m in raw.get('Mounts',[])]))


def verify_inventory(before,after,auxiliaries,old_ui,new_ui,temporary_ids=()):
    allowed=set(temporary_ids)
    require(set(after)==(set(before)-{old_ui})|{new_ui}|allowed,'Unexpected final container inventory')
    aux={row['container_id'] for row in auxiliaries}
    for cid,row in before.items():
        if cid==old_ui:continue
        current=after[cid]
        if cid in aux:
            require({k:v for k,v in row.items() if k!='started_at'}==
                    {k:v for k,v in current.items() if k!='started_at'},'Auxiliary identity or final health changed')
        else:require(current==row,'A preserved service/container changed')
    require(after[new_ui]['running'] and after[new_ui]['health']=='healthy','UI not healthy')
    require(all(after[cid]['running'] and after[cid]['health']=='healthy' for cid in aux),'Final auxiliary health failed')
    require(all(not after[cid]['running'] for cid in allowed),'Temporary container still running')


def summary(state):
    keys=('protocol','run_id','procedure_commit','created_at','status','stage','source_run_id',
          'source_report_sha256','source_state_sha256','historical_image','candidate_image','candidate_ui_sha256',
          'image_verified','build_started','probe_started','builder_limits','probe_limits','probe_checks',
          'regression','regression_limits','engine','planned_build_bytes','vm_disk_free_bytes','public_registry_checked',
          'buildx','private_buildx','buildx_plugin_sha256','buildx_identity_verified','buildkit_image',
          'maintenance_started','deployment_started','deployment_verified','rollback_started','rollback_verified',
          'ui_outcome','ui_http_health','ui_api_read_verified','ui_live_render','state_preserved','ready_preserved',
          'original_api_postgres_preserved','restoration','auxiliaries_restored','final_joint_health_verified',
          'temporary_stopped','temporary_cleanup_complete','cleanup_errors','recovery_complete',
          'signals_during_recovery','error_type','failure_stage','recovery_error_type','rollback_error_type','preflight_only','visible_browser_verified')
    return {key:state[key] for key in keys if key in state}
