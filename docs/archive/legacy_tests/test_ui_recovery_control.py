import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import ui_recovery_control as c
G=2**30


def inventory():
    out={}
    for scope,names in [('original',('api','postgres','ui','mlflow','airflow-api','airflow-dag-processor','airflow-scheduler')),('restored',('api','postgres'))]:
        for name in names:
            cid=scope+'-'+name
            out[cid]=dict(container_id=cid,project=scope,service=name,image_id='image',
                memory_bytes=G,nano_cpus=1000000000,started_at='first',restart_count=0,running=True,health='healthy')
    return out


class RecoveryControl(unittest.TestCase):
    def test_targets_exclude_original_serving_and_reject_foreign_project(self):
        rows=inventory();aux=c.select_targets(rows,'original','restored-postgres')
        self.assertEqual({r['container_id'] for r in aux},{'restored-api','restored-postgres','original-mlflow','original-airflow-api','original-airflow-dag-processor','original-airflow-scheduler'})
        rows['restored-api']['project']='foreign'
        with self.assertRaises(ValueError):c.select_targets(rows,'original','restored-postgres')

    def test_missing_unhealthy_or_extra_active_container_blocks_maintenance(self):
        rows=inventory();rows.pop('original-api')
        with self.assertRaises(ValueError):c.select_targets(rows,'original','restored-postgres')
        rows=inventory();rows['original-ui']['health']='unhealthy'
        with self.assertRaises(ValueError):c.select_targets(rows,'original','restored-postgres')
        rows=inventory();rows['job']=dict(rows['original-api'],container_id='job',service='job')
        with self.assertRaises(ValueError):c.select_targets(rows,'original','restored-postgres')

    def test_admission_counts_running_limits_and_margin(self):
        rows=inventory();self.assertEqual(c.admit(rows,12*G,2*G),12*G)
        with self.assertRaises(ValueError):c.admit(rows,12*G-1,2*G)
        rows['original-api']['memory_bytes']=0
        with self.assertRaises(ValueError):c.admit(rows,100*G,2*G)
        rows['original-api']['running']=False
        self.assertEqual(c.admit(rows,11*G,2*G),11*G)

    def test_recovery_continues_other_services_after_one_failure(self):
        rows=list(inventory().values());aux=[r for r in rows if r['container_id'] not in ('original-api','original-postgres','original-ui')]
        actual={r['container_id']:dict(r,running=False) for r in rows};order=[]
        def start(cid):
            order.append(cid)
            if cid=='original-mlflow':raise RuntimeError('failure')
            actual[cid]['running']=True
        result=c.restored_services(aux,lambda cid:actual[cid],start,lambda cid:actual[cid])
        self.assertEqual(order,['restored-postgres','restored-api','original-mlflow','original-airflow-api','original-airflow-dag-processor','original-airflow-scheduler'])
        self.assertEqual([r['role'] for r in result if not r['restored']],['mlflow'])
        self.assertTrue(actual['original-airflow-scheduler']['running'])

    def test_recovery_refuses_replaced_image(self):
        row=inventory()['original-mlflow'];actual=dict(row,image_id='changed',running=False)
        started=[]
        result=c.restored_services([row],lambda cid:actual,started.append,lambda cid:actual)
        self.assertEqual(len(result),1);self.assertFalse(result[0]['restored']);self.assertEqual(started,[])
