import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))

class FinalContinuation(unittest.TestCase):
    def test_old_worker_must_be_stopped_and_without_evaluation_markers(self):
        from final_continuation import unused_worker
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);saved=dict(image_id='sha256:'+'a'*64)
            current=dict(image_id=saved['image_id'],running=False)
            unused_worker(p,saved,current)
            with self.assertRaises(ValueError):unused_worker(p,saved,dict(current,running=True))
            for name in ('go.json','complete.json','evaluation','evaluation-result.json'):
                (p/name).touch()
                with self.assertRaises(ValueError):unused_worker(p,saved,current)
                (p/name).unlink()

    def test_continuation_receipt_is_exclusive_and_parent_immutable(self):
        from final_continuation import reserve_continuation
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);parent=p/'.final-test-once.json';original=b'{"run_id":"original"}';parent.write_bytes(original)
            digest=sha256(original).hexdigest()
            reserve_continuation(p,digest,{'run_id':'continuation'})
            self.assertEqual(parent.read_bytes(),original)
            with self.assertRaises(FileExistsError):reserve_continuation(p,digest,{'run_id':'second'})

    def test_changed_parent_prevents_continuation(self):
        from final_continuation import reserve_continuation
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'.final-test-once.json').write_text('{}')
            with self.assertRaises(ValueError):reserve_continuation(p,'0'*64,{})
            self.assertFalse((p/'.final-test-continuation.json').exists())

    def test_report_requires_both_parent_links_and_exact_startup_archive(self):
        from final_handoff import validate,empty_result
        base={k:'a'*64 for k in ('runner_tree_sha256','worker_source_sha256','image_sha256','dataset_manifest_sha256','source_sha256','selection_archive_sha256','baseline_archive_sha256','concurrency_archive_sha256','state_before_sha256','state_after_sha256')}
        base['source_commit']='b'*40;report=dict(source_commit='b'*40,status='failed')
        with self.assertRaises(ValueError):validate(report,empty_result(),dict(base,reservation_parent_sha256='c'*64),[])
        with self.assertRaises(ValueError):validate(report,empty_result(),dict(base,reservation_parent_sha256='c'*64,startup_archive_sha256='d'*64),[])
        validate(report,empty_result(),dict(base,reservation_parent_sha256='c'*64,startup_archive_sha256='0f9ebe74b916eb51a7ae82cde4bc847b83a610236093d64489b7f48c346e6493'),[])
