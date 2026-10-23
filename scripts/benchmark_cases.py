"""Executed inside jobs: write a private deterministic cohort, never stdout rows."""
import json
import os
from pathlib import Path
import random
import sys
from hm_recommender.api.recommend import load_state
from hm_recommender.api.schemas import RecommendationRequest


def main():
    directory,output=map(Path,sys.argv[1:3])
    from hm_recommender.api.runtime import source_digest
    if source_digest()!=sys.argv[3]:raise ValueError('Jobs source differs')
    os.umask(0o077)
    state=load_state(directory);rng=random.Random(42)
    groups={'history':rng.sample(sorted(state.histories),32),
            'known_fallback':rng.sample(sorted(state.customers-set(state.histories)),32),
            'unknown':[f'benchmark-unknown-{i}' for i in range(32)]}
    if any(c in state.customers for c in groups['unknown']):raise ValueError('Unknown cohort collision')
    cases=[]
    for i in range(32):
        for group,customers in groups.items():
            request=RecommendationRequest(customer_id=customers[i],k=12)
            response=state.recommend(request).model_dump(mode='json');response.pop('request_id')
            if response['fallback']!=(group!='history'):raise ValueError('Unexpected segment')
            cases.append(dict(group=group,request=request.model_dump(mode='json'),response=response))
    payload=dict(cases=cases,release_id=state.release_id,data_as_of=str(state.data_as_of),
        population=dict(customers=len(state.customers),catalog=len(state.catalog),history_customers=len(state.histories)))
    with output.open('x') as f:json.dump(payload,f)


if __name__=='__main__':main()
