import re
from dataclasses import dataclass
from typing import Any

import pytest

from backend.app import SEARCH_CACHE, extract_query_attributes, product_search_text, run_match_products_rpc
from backend.product_taxonomy import PRODUCT_FAMILIES, type_match, type_regex


@pytest.fixture(autouse=True)
def _clear_search_cache():
    SEARCH_CACHE.clear()
    yield
    SEARCH_CACHE.clear()


@dataclass
class Resp:
    data: Any


class Rpc:
    def __init__(self, rows):
        self.rows = rows

    def execute(self):
        return Resp(self.rows)


class Recorder:
    """Records every call; any table() access (i.e. a full-catalog download) fails the test."""

    def __init__(self, vector_rows, exact_rows):
        self.vector_rows, self.exact_rows, self.calls = vector_rows, exact_rows, []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return Rpc(self.exact_rows if name == "exact_type_products" else self.vector_rows)

    def table(self, name):
        raise AssertionError("augmentation must not read tables client-side")


def vrow(rid, name, sim=0.5):
    return {"id": rid, "brand_id": "b", "brand_name": "B", "category": "Kurtis", "item_name": name, "description": "",
            "image_url": "", "product_url": "", "price": 1.0, "source": "s", "niche_score": 0.5, "likes_count": 0,
            "comments_count": 0, "source_hashtag": "", "scraped_at": "", "brand_follower_count": 1, "brand_niche_score": 0.5,
            "brand_profile_picture_url": "", "brand_instagram_profile_url": "", "category_id": 1, "similarity": sim}


def erow(rid, name):  # raw products-table row shape, as exact_type_products returns it
    return {"id": rid, "product_name": name, "item_name": name, "description": "", "catalog_status": "ACTIVE", "metadata": {}}


def run(client, query="kurti"):
    return run_match_products_rpc(client, [0.0] * 512, category_id=None, search_mode="text",
                                  query_attributes=extract_query_attributes(query), result_limit=50, query_text=query)


def test_augmentation_uses_one_rpc_and_never_downloads_the_catalog():
    client = Recorder([vrow("v1", "Cotton Kurti")], [erow("e1", "Silk kurti")])
    ids = {r["id"] for r in run(client)}
    names = [c[0] for c in client.calls]
    assert names.count("exact_type_products") == 1
    assert ids == {"v1", "e1"}


def test_augmentation_excludes_ids_already_retrieved_and_bounds_rows():
    client = Recorder([vrow("v1", "Cotton Kurti"), vrow("v2", "Printed Kurti")], [])
    run(client)
    payload = next(p for n, p in client.calls if n == "exact_type_products")
    assert payload["exclude_ids"] == ["v1", "v2"]
    assert payload["row_limit"] == 100
    assert payload["type_pattern"] == type_regex("kurti")


def test_no_product_type_means_no_augmentation_call():
    client = Recorder([vrow("v1", "Something")], [erow("e1", "x")])
    run(client, query="beautiful things")
    assert "exact_type_products" not in [c[0] for c in client.calls]


def test_image_search_never_augments():
    client = Recorder([vrow("v1", "Kurti")], [erow("e1", "x")])
    run_match_products_rpc(client, [0.0] * 512, category_id=None, search_mode="image", query_attributes={}, result_limit=50)
    assert "exact_type_products" not in [c[0] for c in client.calls]


def test_rpc_failure_falls_back_to_vector_candidates_only():
    class Failing(Recorder):
        def rpc(self, name, payload):
            if name == "exact_type_products":
                raise RuntimeError("boom")
            return super().rpc(name, payload)

    ids = {r["id"] for r in run(Failing([vrow("v1", "Cotton Kurti")], []))}
    assert ids == {"v1"}


def test_type_regex_is_empty_for_a_type_with_no_terms_so_it_can_never_match_everything():
    assert type_regex("not-a-real-type") == ""
    assert type_regex(None) == ""


@pytest.mark.parametrize("ptype", [t for fam in PRODUCT_FAMILIES.values() for t in fam])
def test_type_regex_selects_exactly_what_type_match_selects(ptype):
    # The database filter is only correct if this regex and type_match agree on every input.
    samples = ["black kurti", "kurtis set", "denim bag", "crop top blue", "co-ord set", "cropped top", "hoops gold",
               "tank top", "jeans", "skirts", "party dress", "topaz ring", "bracelets", "watch", "nothing here", ""]
    pattern = re.compile(type_regex(ptype))
    for text in samples:
        assert bool(pattern.search(text)) == type_match(text, ptype), (ptype, text)


def test_product_search_text_is_unchanged_reference_for_the_sql_haystack():
    row = {"product_name": "A", "item_name": "B", "description": "C", "subcategory": "D", "normalized_main_category": "E",
           "normalized_subcategory": "F", "audience": "G", "metadata": {"colour": "H", "color": "I", "fit": "J"}}
    assert product_search_text(row) == "a b c  d e f g h i  j"
