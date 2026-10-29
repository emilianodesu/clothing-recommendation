import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import mac24_t8_final as f

class FinalPreparationFollowup(unittest.TestCase):
    def test_only_synthetic_tmpfs_has_ingestion_headroom(self):
        args=f.container_args('synthetic','image',synthetic=True)
        self.assertIn('size=1g',args[args.index('--tmpfs')+1])
        normal=f.container_args('real','image')
        self.assertIn('size=512m',normal[normal.index('--tmpfs')+1])
        self.assertEqual(args[args.index('--memory')+1],normal[normal.index('--memory')+1])

    def test_existing_real_markers_block_followup(self):
        from final_control import unstarted_preparation
        for marker in ('config.json','go.json','ready.json','complete.json','evaluation-result.json','evaluation'):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as d:
                root=Path(d);unstarted_preparation(root)
                (root/marker).touch()
                with self.assertRaises(ValueError):unstarted_preparation(root)

    def test_global_reservation_still_blocks_followup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.final-test-once.json').write_text('{}')
            with patch.object(f,'STATE',root/'new'),patch.object(f,'FOLLOWUP',True):
                with self.assertRaises(ValueError):f.no_attempt({'art':root})

    def test_followup_accepts_only_known_unstarted_preparation(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);art=root/'artifacts';old=root/'old-state';old.mkdir()
            (art/'handoff').mkdir(parents=True);(art/'handoff'/f.PREPARATION_RUN).mkdir()
            (art/'handoff'/(f.PREPARATION_RUN+'.zip')).touch()
            private=art/'final-private'/f.PREPARATION_RUN;private.mkdir(parents=True)
            commit='c92321ed4082c36291123ef5f23859781aa5f790'
            (old/'state.json').write_text(json.dumps(dict(run_id=f.PREPARATION_RUN,source_commit=commit,aux=[],private=str(private))))
            (old/'recovery.json').write_text('[]')
            receipt=dict(source_commit=commit,status='failed',result=dict(test_started=False,evaluation=None,auxiliaries_restored=True,source_preserved=True))
            ctx=dict(art=art,aux=[])
            with patch.object(f,'STATE',root/'new-state'),patch.object(f,'ORIGINAL_STATE',old),patch.object(f,'FOLLOWUP',True),patch.object(f,'sha',return_value=f.PREPARATION_SHA),patch.object(f,'read_report',return_value=receipt):
                f.no_attempt(ctx)
                (art/'handoff'/'final-mac24-unrecognized.partial').touch()
                with self.assertRaises(ValueError):f.no_attempt(ctx)
