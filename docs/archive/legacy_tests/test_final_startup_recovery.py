import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class StartupRecovery(unittest.TestCase):
    def receipt(self):
        commit='fbcfca350fe865eec5c51220d22f0db2244029b6';rid='final-mac24-20261024T062636Z-a62a3010'
        saved=dict(source_commit=commit,run_id=rid,image_id='sha256:'+'a'*64)
        reservation=dict(source_commit=commit,run_id=rid,image_sha256='a'*64)
        report=dict(source_commit=commit,run_id=rid,status='failed',result=dict(test_started=False,evaluation=None,auxiliaries_restored=False))
        return saved,reservation,report

    def test_known_reserved_unstarted_attempt_can_be_recovered(self):
        from final_control import verify_startup_recovery
        verify_startup_recovery(*self.receipt())

    def test_other_attempt_or_started_test_cannot_use_exception(self):
        from final_control import verify_startup_recovery
        for field,value in [('run_id','different'),('source_commit','c'*40),('image_sha256','b'*64)]:
            saved,reservation,report=self.receipt();reservation[field]=value
            with self.assertRaises(ValueError):verify_startup_recovery(saved,reservation,report)
        saved,reservation,report=self.receipt();report['result']['test_started']=True
        with self.assertRaises(ValueError):verify_startup_recovery(saved,reservation,report)
