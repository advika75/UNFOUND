from dataclasses import dataclass
from typing import Any

from backend.app import extract_query_attributes, run_match_products_rpc
from backend.search.rerank import SIGNALS


@dataclass
class FakeResponse:
    data: Any


class FakeRpc:
    def __init__(self, rows):
        self.rows = rows

    def execute(self):
        return FakeResponse(self.rows)


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def rpc(self, name, payload):
        if name == "exact_type_products":
            return FakeRpc([])
        return FakeRpc(self.rows)

    def table(self, name):  # hydration is best-effort in production; failing here just skips it
        raise RuntimeError("no table access in unit test")


def _row(rid, name, category, similarity, **extra):
    row = {"id": rid, "brand_id": "b", "brand_name": "Brand", "category": category, "item_name": name,
           "description": "", "image_url": "", "product_url": "", "price": 500.0, "source": "x",
           "niche_score": 0.5, "likes_count": 0, "comments_count": 0, "source_hashtag": "", "scraped_at": "",
           "brand_follower_count": 1, "brand_niche_score": 0.5, "brand_profile_picture_url": "",
           "brand_instagram_profile_url": "", "category_id": 1, "similarity": similarity}
    row.update(extra)
    return row


ROWS = [
    _row("ring", "Silver ring", "Jewellery", 0.95),
    _row("skirt", "Blue skirt", "Women Bottoms", 0.90),
    _row("top_a", "Black cotton top", "Women Tops", 0.60),
    _row("top_b", "White party top", "Women Tops", 0.55),
    _row("top_c", "Red crop top", "Women Tops", 0.50),
]


def _run(mode, **kwargs):
    return run_match_products_rpc(
        FakeSupabase(ROWS), [0.0] * 512, category_id=None, search_mode="text",
        query_attributes=extract_query_attributes("top"), result_limit=50, query_text="top",
        rerank_mode=mode, **kwargs,
    )


def test_gated_returns_exactly_the_same_survivors_as_off():
    assert {r["id"] for r in _run("gated")} == {r["id"] for r in _run("off")}


def test_gated_still_excludes_wrong_family_candidates_that_only_have_high_similarity():
    ids = {r["id"] for r in _run("gated")}
    assert "ring" not in ids and "skirt" not in ids
    assert {"top_a", "top_b", "top_c"} <= ids


def test_gated_reorders_using_rerank_signals_and_breakdown_sums_to_final_score():
    results = _run("gated")
    for row in results:
        assert set(row["score_breakdown"]) == set(SIGNALS)
        assert abs(sum(row["score_breakdown"].values()) - row["final_score"]) < 1e-9


def test_off_mode_keeps_the_original_breakdown_shape():
    assert "semantic_similarity" in _run("off")[0]["score_breakdown"]


def test_gated_is_ignored_for_image_search_and_explicit_sort():
    image = run_match_products_rpc(FakeSupabase(ROWS), [0.0] * 512, category_id=None, search_mode="image",
                                   query_attributes={}, result_limit=50, rerank_mode="gated")
    assert "visual_similarity" in image[0]["score_breakdown"]
    sorted_run = _run("gated", sort_by="price_asc")
    assert "semantic_similarity" in sorted_run[0]["score_breakdown"]


def test_gated_survivors_are_cut_to_top_k_after_reordering():
    results = run_match_products_rpc(
        FakeSupabase(ROWS), [0.0] * 512, category_id=None, search_mode="text",
        query_attributes=extract_query_attributes("top"), result_limit=2, query_text="top", rerank_mode="gated",
    )
    assert len(results) == 2
