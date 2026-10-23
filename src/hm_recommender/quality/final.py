"""Final H/P evaluation component. The operational one-shot launcher is separate.

Never invoked by quality-v1 or by the service. All per-buyer files are private.
"""
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from hm_recommender.contracts import Contract
from hm_recommender.data.io import atomic_json
from hm_recommender.quality.contracts import FoldManifest, RankingArtifact, FoldEvaluation, QualityConfig
from hm_recommender.quality.temporal import build_snapshot, read_labels
from hm_recommender.quality.baselines import rank_baseline
from hm_recommender.quality.evaluate import evaluate_ranking


class FinalWindow(Contract):
    fold_id: Literal['TEST'] = 'TEST'
    cutoff: date = date(2020,9,16)
    label_end: date = date(2020,9,23)

    @model_validator(mode='after')
    def fixed_window(self):
        if self.cutoff!=date(2020,9,16) or self.label_end!=date(2020,9,23):
            raise ValueError('Final window is fixed')
        return self


class FinalSnapshot(FoldManifest):
    fold: FinalWindow


class FinalRanking(RankingArtifact):
    fold: FinalWindow
    method: Literal['P','H']


class FinalEvaluation(FoldEvaluation):
    fold: FinalWindow
    method: Literal['P','H']


def evaluate_final(dataset, output_dir):
    """Evaluate H and P exactly once in this directory; failures remain reserved.

    This directory guard is not a campaign-wide authorization or ledger. The
    operational launcher must enforce those before calling this component.
    """
    root=Path(output_dir)
    root.mkdir(parents=True,exist_ok=False)
    window=FinalWindow()
    atomic_json(root/'reservation.json',dict(protocol='final-hp-v1',selected_method='H',window=window.model_dump(mode='json')))
    snapshot=build_snapshot(dataset,window,root/'snapshot',QualityConfig(),artifact_type=FinalSnapshot)
    labels=read_labels(dataset,window)
    customers=labels.select('customer_id').unique().sort('customer_id').collect(engine='streaming')['customer_id'].to_list()
    if not customers: raise ValueError('No buyers in final window')
    evaluations={}
    for method in ('H','P'):
        ranking=rank_baseline(snapshot,customers,method,root/method/'ranking',artifact_type=FinalRanking)
        evaluation=evaluate_ranking(ranking,labels,snapshot,root/method/'evaluation',artifact_type=FinalEvaluation)
        # Public summary contains aggregates only; manifests and row tables stay private.
        evaluations[method]=evaluation.model_dump(mode='json',exclude={'files','identity'})
    result=dict(protocol='final-hp-v1',selected_method='H',window=window.model_dump(mode='json'),
        snapshot_counts=snapshot.counts,evaluations=evaluations,
        metric_deltas_h_minus_p={m:evaluations['H']['metrics'][m]-evaluations['P']['metrics'][m] for m in evaluations['H']['metrics']})
    atomic_json(root/'summary.json',result)
    return result
