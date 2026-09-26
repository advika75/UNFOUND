"""Reciprocal Rank Fusion for combining multiple ranked retriever outputs.

Pure function, no DB/network dependency: takes ranked id lists (already
retrieved by whichever retrievers, e.g. vector + lexical) and returns a
single fused ranking. Standard RRF formula (Cormack et al.):

    score(doc) = sum over lists L containing doc of weight_L / (k + rank_L(doc))

where rank_L(doc) is the 1-indexed position of doc in list L. A doc absent
from a list contributes nothing from that list -- this is what lets RRF
combine lists of very different sizes/sources without needing comparable
raw scores (cosine similarity vs. ts_rank_cd are not on the same scale;
RRF only uses rank position, sidestepping that entirely).
"""

from __future__ import annotations

DEFAULT_RRF_K = 60


def rrf_fuse(
    ranked_lists: list[list[str]],
    k: int = DEFAULT_RRF_K,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked id lists into one, sorted by descending RRF score.

    Ties are broken by first-seen order across the input lists (stable sort
    over dict insertion order) -- deterministic, and documented here because
    it's easy to assume ties are arbitrary.
    """
    if not ranked_lists:
        raise ValueError("ranked_lists must be non-empty")
    if weights is not None and len(weights) != len(ranked_lists):
        raise ValueError(f"weights length ({len(weights)}) must match ranked_lists length ({len(ranked_lists)})")
    if k < 0:
        raise ValueError("k must be non-negative")

    effective_weights = weights if weights is not None else [1.0] * len(ranked_lists)

    scores: dict[str, float] = {}
    for weight, ranked_list in zip(effective_weights, ranked_lists):
        for rank, item_id in enumerate(ranked_list, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + rank)

    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
