from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request

from backend.app import app, brands_health, catalog_health, catalog_issues, discover, list_brand_products, list_brands, list_products, recommend
from training.scraper_pipeline import ScrapedInstagramPost, dedupe_posts, parse_follower_count, run_pipeline, validate_category


@dataclass
class FakeResponse:
    data: Any


class FakeTable:
    def __init__(self, supabase: "FakeSupabase", table_name: str) -> None:
        self.supabase = supabase
        self.table_name = table_name

    def select(self, columns: str) -> "FakeSelectRequest":
        return FakeSelectRequest(self.supabase, self.table_name, columns)

    def upsert(self, payload: dict[str, Any], on_conflict: str | None = None) -> "FakeRequest":
        return FakeRequest(self.supabase, self.table_name, payload, on_conflict)


class FakeSelectRequest:
    def __init__(self, supabase: "FakeSupabase", table_name: str, columns: str) -> None:
        self.supabase = supabase
        self.table_name = table_name
        self.columns = columns
        self.filters: dict[str, Any] = {}
        self.range_start: int | None = None
        self.range_end: int | None = None

    def limit(self, count: int) -> "FakeSelectRequest":
        return self

    def eq(self, column: str, value: Any) -> "FakeSelectRequest":
        self.filters[column] = value
        return self

    def in_(self, column: str, values: list[Any]) -> "FakeSelectRequest":
        self.filters[column] = set(values)
        return self

    def range(self, start: int, end: int) -> "FakeSelectRequest":
        self.range_start = start
        self.range_end = end
        return self

    def execute(self) -> FakeResponse:
        if self.table_name == "brands":
            rows = list(self.supabase.brands.values())
            for column, value in self.filters.items():
                rows = [row for row in rows if row.get(column) == value]
            return FakeResponse(rows)
        if self.table_name == "products":
            rows = list(self.supabase.products.values())
            for column, value in self.filters.items():
                rows = [row for row in rows if row.get(column) in value] if isinstance(value, set) else [row for row in rows if row.get(column) == value]
            if self.range_start is not None and self.range_end is not None:
                rows = rows[self.range_start:self.range_end + 1]
            return FakeResponse(rows)
        if self.table_name == "categories":
            return FakeResponse(self.supabase.categories)
        return FakeResponse([])


class FakeRequest:
    def __init__(
        self,
        supabase: "FakeSupabase",
        table_name: str,
        payload: dict[str, Any],
        on_conflict: str | None = None,
    ) -> None:
        self.supabase = supabase
        self.table_name = table_name
        self.payload = payload
        self.on_conflict = on_conflict

    def execute(self) -> FakeResponse:
        if self.table_name == "brands":
            row = {"id": "brand-1", **self.payload}
            self.supabase.brands[self.payload["name"]] = row
            return FakeResponse([row])

        if self.table_name == "products":
            row = {"id": "product-1", **self.payload}
            self.supabase.products[self.payload["product_url"]] = row
            return FakeResponse([row])

        raise AssertionError(f"Unexpected table {self.table_name}")


class FakeRpcRequest:
    def __init__(self, supabase: "FakeSupabase") -> None:
        self.supabase = supabase

    def execute(self) -> FakeResponse:
        rows = []
        for product in self.supabase.products.values():
            rows.append(
                {
                    "id": product["id"],
                    "brand_id": product.get("brand_id"),
                    "brand_name": product["brand_name"],
                    "category": "Women Dresses",
                    "item_name": product["item_name"],
                    "description": product["description"],
                    "image_url": product["image_url"],
                    "product_url": product["product_url"],
                    "price": product["price"],
                    "source": product["source"],
                    "niche_score": product["niche_score"],
                    "brand_follower_count": 2200,
                    "brand_niche_score": 0.8,
                    "brand_profile_picture_url": "https://example.com/example-label-profile.jpg",
                    "brand_instagram_profile_url": "https://www.instagram.com/example_label/",
                    "category_id": product["category_id"],
                    "similarity": 0.91,
                }
            )
        return FakeResponse(rows)


class FakeSupabase:
    def __init__(self) -> None:
        self.brands: dict[str, dict[str, Any]] = {}
        self.products: dict[str, dict[str, Any]] = {}
        self.categories = [
            {"id": index + 1, "slug": slug, "name": values["name"]}
            for index, (slug, values) in enumerate(__import__("training.scraper_pipeline", fromlist=["CATEGORY_BY_SLUG"]).CATEGORY_BY_SLUG.items())
        ]

    def table(self, table_name: str) -> FakeTable:
        return FakeTable(self, table_name)

    def rpc(self, function_name: str, payload: dict[str, Any]) -> FakeRpcRequest:
        assert function_name == "match_products"
        assert len(payload["query_embedding"]) == 512
        return FakeRpcRequest(self)


class FakeModel:
    def encode(self, value: Any, normalize_embeddings: bool = True) -> Any:
        class Vector:
            def tolist(self) -> list[float]:
                return [0.01] * 512

        return Vector()


async def fake_scrape_instagram(
    hashtags: list[str],
    *,
    per_hashtag_limit: int = 12,
) -> list[ScrapedInstagramPost]:
    return [
        ScrapedInstagramPost(
            product_url="https://www.instagram.com/p/test-product/",
            image_url="https://example.com/test-product.jpg",
            description="Photo by example_label on Instagram: Linen summer dress, INR 2490",
            brand_name="example_label",
            source_hashtag="SlowFashionBrand",
            followers=2200,
            instagram_username="example_label",
            instagram_profile_url="https://www.instagram.com/example_label/",
            profile_picture_url="https://example.com/example-label-profile.jpg",
            brand_niche_score=0.8,
        ),
        ScrapedInstagramPost(
            product_url="https://www.instagram.com/p/test-product-2/",
            image_url="https://example.com/test-product-2.jpg",
            description="Photo by example_label on Instagram: Linen summer dress in indigo, INR 2790",
            brand_name="example_label",
            source_hashtag="SlowFashionBrand",
            followers=2200,
            instagram_username="example_label",
            instagram_profile_url="https://www.instagram.com/example_label/",
            profile_picture_url="https://example.com/example-label-profile.jpg",
            brand_niche_score=0.8,
        ),
        ScrapedInstagramPost(
            product_url="https://www.instagram.com/p/test-product/?utm_source=duplicate",
            image_url="https://example.com/test-product.jpg",
            description="Photo by example_label on Instagram: Duplicate linen summer dress",
            brand_name="example_label",
            source_hashtag="SlowFashionBrand",
            followers=2200,
            instagram_username="example_label",
            instagram_profile_url="https://www.instagram.com/example_label/",
            profile_picture_url="https://example.com/example-label-profile.jpg",
            brand_niche_score=0.8,
        )
    ]


def test_instagram_product_ingestion_is_searchable(monkeypatch, tmp_path) -> None:
    fake_supabase = FakeSupabase()
    fake_model = FakeModel()
    brand_cache_path = tmp_path / "brands.csv"

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setattr(
        "training.scraper_pipeline.generate_embedding",
        lambda model, product: [1 / (512 ** 0.5)] * 512,
    )

    inserted = __import__("asyncio").run(
        run_pipeline(
            ["SlowFashionBrand"],
            scrape_fn=fake_scrape_instagram,
            model=fake_model,
            supabase=fake_supabase,
            openai_client=None,
                brand_cache_path=brand_cache_path,
                verify_connections=True,
                store_images=False,
        )
    )

    assert len(inserted) == 2
    product = inserted[0]
    assert product["source"] == "instagram"
    assert product["product_url"] == "https://www.instagram.com/p/test-product/"
    assert product["brand_id"] == "brand-1"
    assert product["item_name"] == "Linen summer dress"
    assert product["brand_name"] == "example_label"
    assert product["product_name"] == "Linen summer dress"
    assert product["price"] == 2490.0
    expected_category_id = next(row["id"] for row in fake_supabase.categories if row["slug"] == "women-dresses")
    assert product["category_id"] == expected_category_id
    assert len(product["embedding"]) == 512
    with brand_cache_path.open("r", newline="", encoding="utf-8") as csv_file:
        brand_rows = list(csv.DictReader(csv_file))
    assert len(brand_rows) == 1
    assert brand_rows[0]["brand_name"] == "example_label"
    assert brand_rows[0]["instagram_username"] == "example_label"
    assert brand_rows[0]["instagram_profile_url"] == "https://www.instagram.com/example_label/"
    assert brand_rows[0]["profile_url"] == "https://www.instagram.com/example_label/"
    assert brand_rows[0]["profile_picture_url"] == "https://example.com/example-label-profile.jpg"
    assert brand_rows[0]["followers"] == "2200"
    assert brand_rows[0]["source_hashtag"] == "SlowFashionBrand"
    assert fake_supabase.brands["example_label"]["profile_picture_url"] == "https://example.com/example-label-profile.jpg"
    assert fake_supabase.brands["example_label"]["instagram_profile_url"] == "https://www.instagram.com/example_label/"
    assert fake_supabase.brands["example_label"]["instagram_username"] == "example_label"

    app.state.supabase = fake_supabase
    app.state.clip_model = fake_model

    body = __import__("asyncio").run(
        discover(request=Request({"type": "http", "headers": []}), text_query="linen dress", image_file=None, category_id=None)
    )

    assert body["results"][0]["product_url"] == "https://www.instagram.com/p/test-product/"
    assert body["results"][0]["source"] == "instagram"
    assert body["results"][0]["product_name"] == "Linen summer dress"
    assert body["results"][0]["category"] == "Women Dresses"
    assert body["results"][0]["similarity_score"] == 0.91
    assert body["similar_brands"][0]["brand_name"] == "example_label"
    assert body["similar_brands"][0]["follower_count"] == 2200
    assert body["similar_brands"][0]["profile_picture_url"] == "https://example.com/example-label-profile.jpg"
    assert body["similar_brands"][0]["instagram_profile_url"] == "https://www.instagram.com/example_label/"

    recommendations = __import__("asyncio").run(
        recommend(text_query="linen dress", image_file=None, category_id=None)
    )

    assert recommendations["similar_products"][0]["product_url"] == "https://www.instagram.com/p/test-product/"
    assert recommendations["similar_brands"][0]["brand_name"] == "example_label"
    assert recommendations["similar_brands"][0]["matched_product_count"] == 2

    filtered = __import__("asyncio").run(
        discover(
            request=Request({"type": "http", "headers": []}),
            text_query="linen dress",
            image_file=None,
            category_id=None,
            min_price=2600,
            max_price=None,
            min_niche_score=0.7,
            brand="example",
        )
    )
    assert len(filtered["results"]) == 1
    assert filtered["results"][0]["product_url"] == "https://www.instagram.com/p/test-product-2/"

    brands_body = __import__("asyncio").run(list_brands())
    assert brands_body["brands"][0]["profile_picture_url"] == "https://example.com/example-label-profile.jpg"
    assert brands_body["brands"][0]["instagram_profile_url"] == "https://www.instagram.com/example_label/"
    assert brands_body["total"] == 1

    products_body = __import__("asyncio").run(list_products(limit=1, offset=0))
    assert len(products_body["products"]) == 1
    assert products_body["products"][0]["brand_id"] == "brand-1"
    assert products_body["has_more"] is True

    brand_products_body = __import__("asyncio").run(list_brand_products("brand-1"))
    assert len(brand_products_body["products"]) == 2
    assert {row["brand_id"] for row in brand_products_body["products"]} == {"brand-1"}

    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    health = __import__("asyncio").run(catalog_health("test-admin-key"))
    assert health["total_brands"] == 1
    assert health["total_products"] == 2
    issues = __import__("asyncio").run(catalog_issues(issue="MISSING_EMBEDDING", limit=50, x_admin_key="test-admin-key"))
    assert issues["total"] == 0
    brand_health = __import__("asyncio").run(brands_health("test-admin-key"))
    assert brand_health["brands"][0]["product_count"] == 2


def test_scraper_validation_helpers_are_defensive() -> None:
    deduped = dedupe_posts(
        [
            ScrapedInstagramPost(
                product_url="https://www.instagram.com/p/a/",
                image_url="https://example.com/a.jpg",
                description="Photo by brand on Instagram: denim jeans",
                brand_name="brand",
            ),
            ScrapedInstagramPost(
                product_url="https://www.instagram.com/p/a/?utm_source=x",
                image_url="https://example.com/a.jpg",
                description="Photo by brand on Instagram: denim jeans duplicate",
                brand_name="brand",
            ),
        ]
    )

    assert len(deduped) == 1
    assert validate_category(
        {"category_slug": "bad-category", "confidence_score": 9},
        "linen dress",
    ) == {"category_slug": "women-dresses", "confidence_score": 0.85}
    assert validate_category(
        {"category_slug": "women-bags", "confidence_score": "2.3"},
        "canvas bag",
    ) == {"category_slug": "women-bags", "confidence_score": 1.0}
    assert parse_follower_count("1,234 followers") == 1234
    assert parse_follower_count("12k followers") == 12000
    assert parse_follower_count("1.5m followers") == 1500000
