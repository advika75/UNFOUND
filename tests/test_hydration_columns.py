import random
from dataclasses import dataclass
from typing import Any

import pytest

from backend.app import HYDRATION_COLUMNS, enrich_product_candidates, format_product

ALL_COLUMNS = ['audience', 'brand', 'brand_id', 'brand_name', 'catalog_status', 'category_id', 'classification_source',
               'classifier_confidence', 'comments_count', 'created_at', 'currency', 'description', 'embedding', 'id',
               'image_status', 'image_url', 'instagram_post_id', 'instagram_post_url', 'item_name', 'likes_count', 'metadata',
               'name', 'niche_score', 'normalized_main_category', 'normalized_subcategory', 'original_image_url', 'price',
               'product_name', 'product_url', 'raw_caption', 'scraped_at', 'search_vector', 'source', 'source_hashtag',
               'source_url', 'subcategory', 'updated_at']


@dataclass
class Resp:
    data: Any


class Query:
    def __init__(self, owner):
        self.owner = owner

    def select(self, columns):
        self.owner.selected = columns
        return self

    def in_(self, column, values):
        self.owner.ids = list(values)
        return self

    def execute(self):
        return Resp(self.owner.rows)


class Client:
    def __init__(self, rows):
        self.rows, self.selected, self.ids = rows, None, None

    def table(self, name):
        assert name == "products"
        return Query(self)


def rpc_product(row, category):
    """What match_products would return for a raw row, run through format_product."""
    cols = ["id", "brand_id", "brand_name", "item_name", "description", "image_url", "product_url", "price", "source",
            "niche_score", "likes_count", "comments_count", "source_hashtag", "scraped_at", "category_id"]
    rpc = {c: row.get(c) for c in cols}
    rpc.update({"category": category, "category_audience": "WOMEN", "similarity": 0.5, "brand_follower_count": None})
    return format_product(rpc)


def random_row(rng, i):
    def maybe(value):
        return value if rng.random() < 0.5 else rng.choice([None, "", 0])

    row = {c: maybe(f"{c}-{i}") for c in ALL_COLUMNS}
    # numeric columns hold numbers or NULL in the real table, never strings
    row.update({"id": f"id-{i}", "price": rng.choice([None, 0.0, 199.0]), "likes_count": rng.choice([None, 0, 7]),
                "comments_count": rng.choice([None, 0, 3]), "niche_score": rng.choice([None, 0.5]),
                "metadata": rng.choice([{}, {"style": ["x"]}]),
                "created_at": maybe("2026-01-01T00:00:00+00:00"), "scraped_at": maybe("2026-02-02T00:00:00+00:00"),
                "source_url": maybe("https://x/y"), "product_url": maybe("https://x/p")})
    return row


def test_only_the_needed_columns_are_selected():
    client = Client([])
    enrich_product_candidates(client, [{"id": "a"}])
    assert client.selected == "id,created_at,source_url"
    assert "embedding" not in client.selected and "*" not in client.selected


def test_hydration_backfills_scraped_at_from_created_at_and_product_url_from_source_url():
    raw = {"id": "a", "created_at": "2026-01-01T00:00:00+00:00", "source_url": "https://x/src"}
    product = rpc_product({"id": "a", "item_name": "Kurti", "scraped_at": None, "product_url": ""}, "Kurtis")
    assert product["scraped_at"] == "" and product["product_url"] == ""
    out = enrich_product_candidates(Client([raw]), [product])[0]
    assert out["scraped_at"] == "2026-01-01T00:00:00+00:00"
    assert out["product_url"] == "https://x/src"


def test_narrow_hydration_equals_full_row_hydration_for_every_combination_of_missing_data():
    # The equivalence the optimization rests on: for any row, merging only HYDRATION_COLUMNS gives
    # exactly the same formatted product as merging the entire raw row.
    rng = random.Random(1234)
    for i in range(500):
        row = random_row(rng, i)
        product = rpc_product(row, rng.choice(["Kurtis", ""]))
        full = format_product({**row, **product})
        narrow = format_product({**{c: row[c] for c in HYDRATION_COLUMNS}, **product})
        assert narrow == full, (i, {k: (full[k], narrow[k]) for k in full if full[k] != narrow[k]})


def test_missing_row_leaves_the_product_unchanged():
    product = rpc_product({"id": "a", "item_name": "Kurti"}, "Kurtis")
    assert enrich_product_candidates(Client([]), [product])[0] == product


def test_failure_falls_back_to_unhydrated_products():
    class Boom(Client):
        def table(self, name):
            raise RuntimeError("down")

    product = rpc_product({"id": "a", "item_name": "Kurti"}, "Kurtis")
    assert enrich_product_candidates(Boom([]), [product]) == [product]


@pytest.mark.parametrize("column", HYDRATION_COLUMNS)
def test_hydration_columns_are_real_products_columns(column):
    assert column in ALL_COLUMNS
