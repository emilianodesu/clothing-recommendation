import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import ui_deploy_control as c
from test_ui_recovery_control import inventory
from ui_recovery_control import select_targets


class Contracts(unittest.TestCase):
    def test_override_rejects_any_change_except_ui_image(self):
        base={'name':'project','services':{'ui':{'image':'old','ports':['127.0.0.1:8501:8501']},'api':{'image':'old'}}}
        candidate=copy.deepcopy(base);candidate['services']['ui']['image']='sha256:'+'a'*64
        c.verify_override(base,candidate,'sha256:'+'a'*64)
        for service,key,value in [('api','image','new'),('ui','ports',['0.0.0.0:8501:8501'])]:
            changed=copy.deepcopy(candidate);changed['services'][service][key]=value
            with self.assertRaises(ValueError):c.verify_override(base,changed,'sha256:'+'a'*64)

    def test_apply_targets_only_ui_without_build_pull_or_dependencies(self):
        cmd=c.apply_command(Path('/project'),{'COMPOSE_PROJECT_NAME':'p'},Path('/private/ui.json'))
        self.assertEqual(cmd,['docker','--context','colima-hm-project','compose','--project-directory','/project',
            '--env-file','/project/.env','--file','/project/compose.yaml','--file','/private/ui.json','--project-name','p',
            'up','-d','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','90','ui'])

    def test_contract_compares_service_settings_but_not_image_environment(self):
        raw={'Config':{'Cmd':['streamlit'],'User':'10001','Env':['HM_APP_PASSWORD=secret','PYTHON_VERSION=old'],'Healthcheck':{'Test':['CMD','check']}},
             'HostConfig':{'Memory':536870912,'NanoCpus':500000000,'ReadonlyRootfs':True,
                 'PortBindings':{'8501/tcp':[{'HostIp':'127.0.0.1','HostPort':'8501'}]},'Binds':None,'Tmpfs':{'/tmp':''},'RestartPolicy':{'Name':'unless-stopped'}},
             'NetworkSettings':{'Networks':{'backend':{'IPAddress':'one'}}},'Mounts':[{'Type':'tmpfs','Destination':'/tmp','RW':True}]}
        before=c.ui_contract(raw,['HM_APP_PASSWORD'])
        self.assertEqual(json.loads(json.dumps(before)),before,'Contract must survive private-state serialization')
        changed=copy.deepcopy(raw);changed['Config']['Env'][-1]='PYTHON_VERSION=new'
        changed['NetworkSettings']['Networks']['backend']['IPAddress']='two'
        self.assertEqual(before,c.ui_contract(changed,['HM_APP_PASSWORD']))
        changed['Config']['Env'][0]='HM_APP_PASSWORD=different'
        self.assertNotEqual(before,c.ui_contract(changed,['HM_APP_PASSWORD']))

    def test_final_inventory_requires_all_auxiliaries_healthy_and_originals_unchanged(self):
        before=inventory();aux=select_targets(before,'original','restored-postgres')
        after=copy.deepcopy(before);ui=after.pop('original-ui');ui.update(container_id='new-ui',image_id='new',started_at='new')
        after['new-ui']=ui
        for row in aux:after[row['container_id']]['started_at']='recovered'
        c.verify_inventory(before,after,aux,'original-ui','new-ui')
        for cid,key,value in [('original-api','started_at','changed'),('original-postgres','image_id','changed'),
                              ('original-mlflow','health','unhealthy'),('restored-api','restart_count',1)]:
            changed=copy.deepcopy(after);changed[cid][key]=value
            with self.assertRaises(ValueError):c.verify_inventory(before,changed,aux,'original-ui','new-ui')
        after['foreign']=dict(ui,container_id='foreign')
        with self.assertRaises(ValueError):c.verify_inventory(before,after,aux,'original-ui','new-ui')

    def test_public_summary_excludes_private_configuration_and_data(self):
        value=c.summary({'status':'passed','run_id':'run','ui_outcome':'candidate',
            'values':{'PASSWORD':'secret'},'before':{'customer':'private'},'ui_contract':{'environment':{'token':'secret'}},
            'candidate_image':'sha256:'+'a'*64})
        self.assertEqual(set(value),{'status','run_id','ui_outcome','candidate_image'})
