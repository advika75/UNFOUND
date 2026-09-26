"""Lexical (full-text + trigram) retriever. Same input/output shape as
backend.search.vector.vector_retrieve -- see that module's docstring for
why pre-fusion filtering happens inside each retriever independently.

Calls the public.lexical_search_products Postgres function (see
Niche_brand/supabase/lexical_search_function_migration.sql), which runs
websearch_to_tsquery + ts_rank_cd first and only backfills with
word_similarity-based trigram matches (misspellings, exact brand lookups)
when FTS returns fewer than fallback_threshold rows. That function already
returns the same column shape as match_products (including a "similarity"
column carrying the FTS/trigram score), so format_product() handles either
retriever's rows identically.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 100
DEFAULT_FALLBACK_THRESHOLD = 5


def lexical_search(
    supabase: Any,
    query_text: str,
    *,
    category_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    fallback_threshold: int = DEFAULT_FALLBACK_THRESHOLD,
    min_price: float | None = None,
    max_price: float | None = None,
    min_niche_score: float | None = None,
    min_followers: int | None = None,
    brand: str | None = None,
    gender: str | None = None,
) -> list[dict[str, Any]]:
    from backend.app import apply_product_filters, format_product

    response = supabase.rpc(
        "lexical_search_products",
        {
            "query_text": query_text,
            "filter_category_id": category_id,
            "match_count": limit,
            "fallback_threshold": fallback_threshold,
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
