"""Vector (CLIP embedding) retriever, extracted as a standalone, filtered
candidate-fetching step so it has the exact same input/output shape as
backend.search.lexical.lexical_search -- both return a filtered, ranked
list[dict] (the backend.app.format_product shape), which is all
backend.search.fusion.rrf_fuse needs to treat them as interchangeable.

Filters (price/niche_score/followers/brand/gender) are applied INSIDE this
function, on this retriever's own top-N candidates, before the caller does
anything else with the result (fusion included). This matters once a second
retriever enters the picture: filtering AFTER fusing two ranked lists can
zero out a fused top-K that had plenty of filter-passing candidates further
down either individual list -- pre-filtering each retriever's own candidate
pool avoids that failure mode entirely.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 100


def vector_retrieve(
    supabase: Any,
    query_embedding: list[float],
    *,
    category_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    match_threshold: float = 0.0,
    min_price: float | None = None,
    max_price: float | None = None,
    min_niche_score: float | None = None,
    min_followers: int | None = None,
    brand: str | None = None,
    gender: str | None = None,
) -> list[dict[str, Any]]:
    from backend.app import apply_product_filters, format_product

    response = supabase.rpc(
        "match_products",
        {
            "query_embedding": query_embedding,
            "match_threshold": match_threshold,
            "match_count": limit,
            "filter_category_id": category_id,
        },
    ).execute()

    products = [format_product(row) for row in response.data or []]
    return apply_product_filters(
        products,
        min_price=min_price,
        max_price=max_price,
        min_niche_score=min_niche_score,
        min_followers=min_followers,
        brand=brand,
        gender=gender,
    )
