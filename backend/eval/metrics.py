"""Pure information-retrieval metrics for the offline relevance harness.

No Supabase/network/search dependency anywhere in this module — every
function takes plain Python data (ranked id lists, grade dicts) and returns
a float, so all of it is unit-testable without a database or a running
search stack.

Relevance grades follow backend/eval/queryset.py's convention: a `relevant`
mapping is {product_id: grade} with grade in {1, 2}; a product id absent
from the mapping is irrelevant (grade 0).

Per-query metrics (call once per query, with that query's ranked result-id
list and its `relevant` mapping):
    recall_at_k, precision_at_k, ndcg_at_k, reciprocal_rank

Aggregate metrics (call once, with a list of per-query values already
computed across the whole query set):
    mrr, zero_result_rate
"""

from __future__ import annotations

import math


def _require_non_empty_relevant(relevant: dict[str, int]) -> None:
    if not relevant:
        raise ValueError("relevant must be non-empty")


def _require_positive_k(k: int) -> None:
    if k <= 0:
        raise ValueError("k must be a positive integer")


def recall_at_k(results: list[str], relevant: dict[str, int], k: int) -> float:
    """Fraction of all relevant products (any grade) found in the top k results."""
    _require_non_empty_relevant(relevant)
    _require_positive_k(k)
    top_k = results[:k]
    hits = sum(1 for product_id in top_k if product_id in relevant)
    return hits / len(relevant)


def precision_at_k(results: list[str], relevant: dict[str, int], k: int) -> float:
    """Fraction of the top k slots occupied by a relevant product (any grade).

    Divides by k, not by len(results): if the search returns fewer than k
    results, the missing slots count as non-relevant. This intentionally
    penalizes under-returning, distinct from zero_result_rate below which
    tracks the same failure mode as its own explicit signal.
    """
    _require_non_empty_relevant(relevant)
    _require_positive_k(k)
    top_k = results[:k]
    hits = sum(1 for product_id in top_k if product_id in relevant)
    return hits / k


def _dcg(gains: list[int]) -> float:
    """Standard discounted cumulative gain: sum(gain_i / log2(rank_i + 1))."""
    return sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))


def ndcg_at_k(results: list[str], relevant: dict[str, int], k: int) -> float:
    """Graded normalized DCG at k: DCG of the actual ranking over DCG of the ideal ranking.

    gain(product) = relevant.get(product, 0) (0 for anything not marked relevant).
    Ideal DCG (IDCG) is the DCG of the best possible ordering of the known
    relevant grades, truncated to k. Returns 0.0 if IDCG is 0 (should not
    happen given _require_non_empty_relevant, since grades are always >= 1).
    """
    _require_non_empty_relevant(relevant)
    _require_positive_k(k)
    gains = [relevant.get(product_id, 0) for product_id in results[:k]]
    dcg = _dcg(gains)
    ideal_gains = sorted(relevant.values(), reverse=True)[:k]
    idcg = _dcg(ideal_gains)
    if idcg == 0:
        return 0.0
    return dcg / idcg


def reciprocal_rank(results: list[str], relevant: dict[str, int]) -> float:
    """1 / (rank of the first relevant product, 1-indexed), or 0.0 if none appears."""
    _require_non_empty_relevant(relevant)
    for rank, product_id in enumerate(results, start=1):
        if product_id in relevant:
            return 1.0 / rank
    return 0.0


def mrr(reciprocal_ranks: list[float]) -> float:
    """Mean reciprocal rank across queries: the plain mean of per-query reciprocal_rank values."""
    if not reciprocal_ranks:
        raise ValueError("reciprocal_ranks must be non-empty")
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def zero_result_rate(result_lists: list[list[str]]) -> float:
    """Fraction of queries whose search returned zero results at all."""
    if not result_lists:
        raise ValueError("result_lists must be non-empty")
    zero_count = sum(1 for results in result_lists if len(results) == 0)
    return zero_count / len(result_lists)
