"""Frozen history-then-popularity rule; ordered inputs, no framework dependency."""
from itertools import chain


def unique_prefix(streams, *, limit, eligible=None):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Candidate limit must be an integer in 1..200')
    result=[]; seen=set()
    for article in chain.from_iterable(streams):
        if article not in seen and (eligible is None or article in eligible):
            seen.add(article);result.append(article)
            if len(result)==limit:break
    return result


def rank_history(history: list[str], popularity: list[str], eligible: set[str], *, limit: int=200) -> list[str]:
    return unique_prefix((history,popularity),eligible=eligible,limit=limit)
