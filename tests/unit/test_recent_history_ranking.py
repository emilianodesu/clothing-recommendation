import numpy as np
import polars as pl

from hm_recommender.model.recent_history import recent_histories, compose_recent


def test_recent_history_excludes_future_and_old_events_and_resolves_ties():
    # A future purchase, an old purchase and an unknown item cannot enter recommendations.
    events = [('buyer', 'a', '2020-08-11'), ('buyer', 'b', '2020-08-12'),
              ('buyer', 'a', '2020-09-09'), ('buyer', 'x', '2020-09-08'),
              ('buyer', 'c', '2020-09-08'), ('buyer', 'c', '2020-09-07'),
              ('buyer', 'd', '2020-09-08'), ('buyer', 'e', '2020-09-08')]
    transactions = pl.DataFrame(events, schema=['customer_id','article_id','t_dat'], orient='row').with_columns(pl.col('t_dat').str.to_date()).lazy()
    catalog = pl.DataFrame({'article_id':['a','b','c','d','e']})
    histories = recent_histories(transactions, catalog, ['buyer','absent'])
    assert histories[0].tolist() == [2,3,4,1]  # latest date, then frequency, then article ID
    assert histories[1].tolist() == []


def test_recent_prefix_deduplicates_and_empty_history_uses_popularity():
    popular = np.array([0,1,2,3,4])
    model = np.array([4,3,2,1,0])
    assert compose_recent(np.array([2,2,1]), popular, k=5).tolist() == [2,1,0,3,4]
    assert compose_recent(np.array([2,1]), popular, model=model, k=5).tolist() == [2,1,4,3,0]
    assert compose_recent(np.array([], dtype=int), popular, model=model, k=5).tolist() == [0,1,2,3,4]
    assert compose_recent(np.array([4,3,2]), popular, model=model, k=2).tolist() == [4,3]
