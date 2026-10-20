"""Versioned contracts for the fixed quality-v1 campaign."""
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
import hashlib
import json

from pydantic import Field, PrivateAttr, model_validator
from hm_recommender.contracts import Contract, ArtifactFile

CUTOFFS = {'V1':date(2020,8,26), 'V2':date(2020,9,2), 'V3':date(2020,9,9)}
METRICS = ('map_at_12','ndcg_at_12','recall_at_200')
SEGMENTS = ('recent28d','history29_90d','older_history','no_history')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()


class QualityConfig(Contract):
    protocol: Literal['quality-v1'] = 'quality-v1'
    seed: Literal[42] = 42
    history_days: Literal[28] = 28
    popularity_days: Literal[7] = 7
    history_extended_days: Literal[90] = 90
    candidates: Literal[200] = 200
    pilot_users: Literal[2000] = 2000
    folds: dict = Field(default_factory=lambda:{k:str(v) for k,v in CUTOFFS.items()})
    graph: dict = Field(default_factory=lambda:dict(window_days=90,basket_days=7,basket_items=50,degree=50,seeds=50,half_life_days=14,rrf_constant=60))
    pagerank: dict = Field(default_factory=lambda:dict(sample_users=5000,nodes=512,alpha=.85,iterations=100,tolerance=1e-6,permutations=10))
    ranker: dict = Field(default_factory=lambda:dict(weeks=4,negatives=20,objective='lambdarank',label_gain=[0,1],metric='None',evaluator='project_ndcg_at_12',lambdarank_truncation_level=15,num_boost_round=300,learning_rate=.05,num_leaves=31,min_data_in_leaf=100,max_bin=127,lambda_l2=1,feature_fraction=1,bagging_fraction=1,bagging_freq=0,seed=42,num_threads=2,deterministic=True,force_col_wise=True,early_stopping_rounds=30))
    selection: dict = Field(default_factory=lambda:dict(bootstrap_resamples=2000,family_alpha=.05,contrasts=3,min_map_gain=.02,min_positive_folds=2,max_fold_loss=.02,max_auxiliary_loss=.01,max_segment_loss=.05,min_segment_users=1000,tie_map=.0001))
    budget: dict = Field(default_factory=lambda:dict(job_seconds=7200,full_seconds=84600,summary_seconds=1800,pilot_seconds=7200,start_free_bytes=20*2**30,min_free_bytes=5*2**30))


def load_config(path):
    config=QualityConfig.model_validate_json(Path(path).read_text())
    if config.model_dump()!=QualityConfig().model_dump():
        raise ValueError('quality-v1 configuration is fixed; use a new protocol for changes')
    return config


class FoldSpec(Contract):
    fold_id: str
    role: Literal['evaluation','ranker_train'] = 'evaluation'
    parent_fold: str | None = None
    cutoff: date
    label_end: date

    @model_validator(mode='after')
    def authorized_window(self):
        parent=self.parent_fold if self.role=='ranker_train' else self.fold_id
        if parent not in CUTOFFS or self.label_end-self.cutoff!=timedelta(days=7):
            raise ValueError('Unauthorized quality fold')
        if self.role=='evaluation':
            if self.parent_fold is not None or self.cutoff!=CUTOFFS[parent]:
                raise ValueError('Evaluation cutoff mismatch; test is reserved')
        else:
            weeks=(CUTOFFS[parent]-self.cutoff).days
            if weeks not in (7,14,21,28) or self.fold_id!=f'{parent}-train-{weeks//7}':
                raise ValueError('Training window must precede its parent fold')
        return self

    @classmethod
    def evaluation(cls, name):
        if name not in CUTOFFS: raise ValueError('Unknown evaluation fold; test is reserved')
        return cls(fold_id=name,cutoff=CUTOFFS[name],label_end=CUTOFFS[name]+timedelta(days=7))

    @classmethod
    def training(cls, parent, weeks):
        if parent not in CUTOFFS or weeks not in (1,2,3,4): raise ValueError('Invalid training window')
        cutoff=CUTOFFS[parent]-timedelta(days=7*weeks)
        return cls(fold_id=f'{parent}-train-{weeks}',role='ranker_train',parent_fold=parent,cutoff=cutoff,label_end=cutoff+timedelta(days=7))


class QualityFile(ArtifactFile):
    size_bytes: int = Field(ge=0)


class LocalArtifact(Contract):
    _root: Path | None = PrivateAttr(default=None)
    schema_version: Literal[1] = 1
    identity: dict
    files: dict[str,QualityFile]


class FoldManifest(LocalArtifact):
    fold: FoldSpec
    counts: dict[str,int]


class RankingArtifact(LocalArtifact):
    fold: FoldSpec
    method: Literal['P','H','G','PPR','RH','RG']
    customers: int


class FoldEvaluation(LocalArtifact):
    fold: FoldSpec
    method: Literal['P','H','G','PPR','RH','RG']
    population: dict
    metrics: dict[str,float]
    segments: dict
    denominators: dict
    exposure: dict
    targets: dict
