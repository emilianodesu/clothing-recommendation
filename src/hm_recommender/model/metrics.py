"""Binary ranking metrics shared by neural and rule-based evaluation."""
import numpy as np


def ranking_metrics(ranking, relevant):
    if not relevant:
        raise ValueError('Users without purchases must be counted as excluded')
    hits = np.asarray([value in relevant for value in ranking], dtype=np.float64)
    cut = hits[:12]
    ap = np.sum(cut * np.cumsum(cut) / np.arange(1, len(cut) + 1)) / min(12, len(relevant))
    ideal = np.sum(1 / np.log2(np.arange(2, min(12, len(relevant)) + 2)))
    dcg = np.sum(cut / np.log2(np.arange(2, len(cut) + 2)))
    return {'map_at_12': float(ap), 'ndcg_at_12': float(dcg / ideal), 'recall_at_200': float(hits[:200].sum() / len(relevant))}

