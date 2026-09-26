from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.search.lexical import lexical_search
from backend.search.vector import vector_retrieve


@dataclass
class FakeResponse:
    data: Any


class FakeRpcRequest:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self) -> FakeResponse:
        return FakeResponse(self.rows)


class FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, function_name: str, payload: dict[str, Any]) -> FakeRpcRequest:
        self.calls.append((function_name, payload))
        return FakeRpcRequest(self.rows)


def _row(row_id: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "id": row_id,
        "brand_id": "brand-1",
        "brand_name": "TestBrand",
        "category": "Women Tops",
        "item_name": f"Product {row_id}",
        "description": "",
        "image_url": "https://example.com/x.jpg",
        "product_url": "https://example.com/p",
        "price": 500.0,
        "source": "instagram",
        "niche_score": 0.8,
        "likes_count": 10,
        "comments_count": 1,
        "source_hashtag": "",
        "scraped_at": "",
        "brand_follower_count": 1000,
        "brand_niche_score": 0.7,
        "brand_profile_picture_url": "",
        "brand_instagram_profile_url": "",
        "category_id": 14,
        "similarity": 0.9,
    }
    row.update(overrides)
    return row


# --- vector_retrieve -----------------------------------------------------------

def test_vector_retrieve_calls_match_products_with_embedding_and_category():
    supabase = FakeSupabase([_row("p1")])
    embedding = [0.1] * 512
    vector_retrieve(supabase, embedding, category_id=14, limit=100)
    function_name, payload = supabase.calls[0]
    assert function_name == "match_products"
    assert payload["query_embedding"] == embedding
    assert payload["filter_category_id"] == 14
    assert payload["match_count"] == 100


def test_vector_retrieve_returns_formatted_products_in_rank_order():
    supabase = FakeSupabase([_row("p1"), _row("p2")])
    results = vector_retrieve(supabase, [0.0] * 512)
    assert [p["id"] for p in results] == ["p1", "p2"]
    assert results[0]["product_name"] == "Product p1"


def test_vector_retrieve_applies_prefusion_price_filter():
    supabase = FakeSupabase([_row("cheap", price=100.0), _row("pricey", price=5000.0)])
    results = vector_retrieve(supabase, [0.0] * 512, max_price=1000.0)
    assert [p["id"] for p in results] == ["cheap"]


def test_vector_retrieve_applies_prefusion_gender_filter():
    supabase = FakeSupabase([_row("hers", category="Women Tops"), _row("his", category="Men Shirts")])
    results = vector_retrieve(supabase, [0.0] * 512, gender="women")
    assert [p["id"] for p in results] == ["hers"]


# --- lexical_search -------------------------------------------------------------

def test_lexical_search_calls_lexical_search_products_with_query_and_category():
    supabase = FakeSupabase([_row("p1")])
    lexical_search(supabase, "black top", category_id=14, limit=100, fallback_threshold=5)
    function_name, payload = supabase.calls[0]
    assert function_name == "lexical_search_products"
    assert payload["query_text"] == "black top"
    assert payload["filter_category_id"] == 14
    assert payload["match_count"] == 100
    assert payload["fallback_threshold"] == 5


def test_lexical_search_returns_formatted_products_in_rank_order():
    supabase = FakeSupabase([_row("p1"), _row("p2")])
    results = lexical_search(supabase, "top")
    assert [p["id"] for p in results] == ["p1", "p2"]


def test_lexical_search_applies_prefusion_price_filter():
    supabase = FakeSupabase([_row("cheap", price=100.0), _row("pricey", price=5000.0)])
    results = lexical_search(supabase, "top", max_price=1000.0)
    assert [p["id"] for p in results] == ["cheap"]


def test_lexical_search_applies_prefusion_brand_filter():
    supabase = FakeSupabase([_row("mine", brand_name="BYUTIFY"), _row("other", brand_name="Kajrakh")])
    results = lexical_search(supabase, "kurti", brand="BYUTIFY")
    assert [p["id"] for p in results] == ["mine"]


def test_lexical_search_default_fallback_threshold_is_passed():
    supabase = FakeSupabase([])
    lexical_search(supabase, "top")
    _, payload = supabase.calls[0]
    assert payload["fallback_threshold"] == 5


# --- same shape, both retrievers -------------------------------------------------

def test_both_retrievers_produce_the_same_output_shape():
    rows = [_row("p1")]
    vector_result = vector_retrieve(FakeSupabase(rows), [0.0] * 512)
    lexical_result = lexical_search(FakeSupabase(rows), "top")
    assert set(vector_result[0].keys()) == set(lexical_result[0].keys())
