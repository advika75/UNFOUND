from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app import fetch_hybrid_candidates, run_match_products_rpc


@dataclass
class FakeResponse:
    data: Any


class FakeRpcRequest:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self) -> FakeResponse:
        return FakeResponse(self.rows)


class FakeSupabase:
    """Returns different canned rows for match_products vs lexical_search_products."""

    def __init__(self, vector_rows: list[dict[str, Any]], lexical_rows: list[dict[str, Any]]) -> None:
        self.vector_rows = vector_rows
        self.lexical_rows = lexical_rows
        self.calls: list[str] = []

    def rpc(self, function_name: str, payload: dict[str, Any]) -> FakeRpcRequest:
        self.calls.append(function_name)
        if function_name == "match_products":
            return FakeRpcRequest(self.vector_rows)
        if function_name == "lexical_search_products":
            return FakeRpcRequest(self.lexical_rows)
        raise AssertionError(f"unexpected rpc {function_name}")

    def table(self, name: str):
        raise AssertionError("hybrid path should not need direct table access in this test")


def _row(row_id: str, similarity: float, **overrides: Any) -> dict[str, Any]:
    row = {
        "id": row_id, "brand_id": "b1", "brand_name": "Brand", "category": "Women Tops",
        "item_name": f"Product {row_id}", "description": "", "image_url": "https://x/y.jpg",
        "product_url": "https://x/p", "price": 500.0, "source": "instagram", "niche_score": 0.8,
        "likes_count": 1, "comments_count": 0, "source_hashtag": "", "scraped_at": "",
        "brand_follower_count": 1000, "brand_niche_score": 0.7, "brand_profile_picture_url": "",
        "brand_instagram_profile_url": "", "category_id": 14, "similarity": similarity,
    }
    row.update(overrides)
    return row


def test_fetch_hybrid_candidates_calls_both_retrievers():
    supabase = FakeSupabase(vector_rows=[_row("v1", 0.9)], lexical_rows=[_row("l1", 2.5)])
    fetch_hybrid_candidates(supabase, [0.0] * 512, "top", category_id=None)
    assert set(supabase.calls) == {"match_products", "lexical_search_products"}


def test_fetch_hybrid_candidates_includes_ids_from_both_retrievers():
    supabase = FakeSupabase(vector_rows=[_row("v1", 0.9)], lexical_rows=[_row("l1", 2.5)])
    merged = fetch_hybrid_candidates(supabase, [0.0] * 512, "top", category_id=None)
    assert {p["id"] for p in merged} == {"v1", "l1"}


def test_fetch_hybrid_candidates_ranks_item_found_by_both_retrievers_first():
    # "shared" appears in both lists (rank 1 in each) -- RRF must score it above
    # an item found by only one retriever.
    supabase = FakeSupabase(
        vector_rows=[_row("shared", 0.9), _row("v_only", 0.5)],
        lexical_rows=[_row("shared", 2.5), _row("l_only", 1.0)],
    )
    merged = fetch_hybrid_candidates(supabase, [0.0] * 512, "top", category_id=None)
    assert merged[0]["id"] == "shared"


def test_fetch_hybrid_candidates_normalizes_similarity_to_zero_one_range():
    # Raw lexical ts_rank_cd scores (e.g. 2.5) must never leak through as the
    # merged "similarity" -- everything must land in [0, 1] after RRF + normalization.
    supabase = FakeSupabase(vector_rows=[_row("v1", 0.9)], lexical_rows=[_row("l1", 2.5), _row("l2", 1.0)])
    merged = fetch_hybrid_candidates(supabase, [0.0] * 512, "top", category_id=None)
    for product in merged:
        assert 0.0 <= product["similarity"] <= 1.0
        assert 0.0 <= product["similarity_score"] <= 1.0


def test_fetch_hybrid_candidates_single_result_normalizes_to_one():
    supabase = FakeSupabase(vector_rows=[_row("only", 0.9)], lexical_rows=[])
    merged = fetch_hybrid_candidates(supabase, [0.0] * 512, "top", category_id=None)
    assert merged[0]["similarity"] == 1.0


def test_fetch_hybrid_candidates_applies_prefusion_filters_to_each_retriever():
    supabase = FakeSupabase(
        vector_rows=[_row("cheap", 0.9, price=100.0), _row("pricey_v", 0.8, price=9000.0)],
        lexical_rows=[_row("pricey_l", 2.0, price=9000.0)],
    )
    merged = fetch_hybrid_candidates(supabase, [0.0] * 512, "top", category_id=None, max_price=1000.0)
    assert {p["id"] for p in merged} == {"cheap"}


def test_run_match_products_rpc_default_mode_never_touches_lexical_rpc():
    supabase = FakeSupabase(vector_rows=[_row("v1", 0.9)], lexical_rows=[_row("l1", 2.5)])
    run_match_products_rpc(supabase, [0.0] * 512, category_id=None, search_mode="text", query_text="top")
    assert supabase.calls == ["match_products"]


def test_run_match_products_rpc_hybrid_mode_calls_both_retrievers():
    supabase = FakeSupabase(vector_rows=[_row("v1", 0.9)], lexical_rows=[_row("l1", 2.5)])
    run_match_products_rpc(
        supabase, [0.0] * 512, category_id=None, search_mode="text",
        query_text="top", retrieval_mode="hybrid",
    )
    assert set(supabase.calls) == {"match_products", "lexical_search_products"}


def test_run_match_products_rpc_hybrid_mode_ignored_for_image_search():
    # Hybrid only applies to text search -- lexical has no image query representation.
    supabase = FakeSupabase(vector_rows=[_row("v1", 0.9)], lexical_rows=[_row("l1", 2.5)])
    run_match_products_rpc(
        supabase, [0.0] * 512, category_id=None, search_mode="image",
        query_text=None, retrieval_mode="hybrid",
    )
    assert supabase.calls == ["match_products"]
