from backend.catalog_quality import (
    build_brand_health,
    build_catalog_report,
    catalog_quality_score,
    duplicate_groups,
    price_status,
    product_warnings,
    readiness_status,
)


VECTOR = [0.01] * 512


def complete_product(**overrides):
    row = {
        "id": "p1", "brand_id": "b1", "brand_name": "Label", "product_name": "Black Dress",
        "image_url": "https://example.com/dress.jpg", "category_id": 13, "embedding": VECTOR,
        "product_url": "https://www.instagram.com/p/ABC/", "instagram_post_id": "ABC",
        "price": 2499, "scraped_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_missing_fields_and_quality_score() -> None:
    broken = complete_product(product_name=None, image_url=None, category_id=None, embedding=None, brand_id=None, product_url="bad")
    warnings = product_warnings(broken)
    assert {"MISSING_NAME", "MISSING_IMAGE", "MISSING_CATEGORY", "MISSING_EMBEDDING", "MISSING_BRAND", "INVALID_SOURCE_URL"} <= set(warnings)
    assert catalog_quality_score(complete_product()) == 100
    assert catalog_quality_score(broken) == 5


def test_price_validation() -> None:
    assert price_status(None) == "MISSING"
    assert price_status(-500) == "INVALID"
    assert price_status(0) == "INVALID"
    assert price_status("corrupt") == "INVALID"
    assert price_status(50) == "SUSPICIOUS"
    assert price_status(99_999_999) == "SUSPICIOUS"
    assert price_status(2499) == "VALID"


def test_duplicate_candidate_grouping() -> None:
    groups = duplicate_groups([complete_product(), complete_product(id="p2", product_url="https://instagram.com/p/OTHER/")])
    assert any(group["reason"] == "same_instagram_post" for group in groups)
    assert any(group["reason"] == "same_brand_normalized_name" for group in groups)


def test_catalog_summary_and_readiness() -> None:
    brands = [{"id": "b1", "name": "Label"}, {"id": "b2", "name": "Empty"}]
    report = build_catalog_report(brands, [complete_product()])
    assert report["total_brands"] == 2
    assert report["total_products"] == 1
    assert report["brands_with_products"] == 1
    assert report["brands_without_products"] == 1
    assert report["image_coverage_percent"] == 100
    assert report["catalog_status"] == "NOT_READY"
    ready = {**report, "total_products": 100, "image_coverage_percent": 95, "category_coverage_percent": 95, "embedding_coverage_percent": 98}
    assert readiness_status(ready) == "READY"


def test_brand_health_classification() -> None:
    brands = [{"id": "b1", "name": "Good"}, {"id": "b2", "name": "Empty"}, {"id": "b3", "name": "Failed"}]
    products = [complete_product(id=f"p{i}") for i in range(3)]
    rows = {row["brand_id"]: row for row in build_brand_health(brands, products, [{"brand_id": "b3"}])}
    assert rows["b1"]["ingestion_health"] == "HEALTHY"
    assert rows["b2"]["ingestion_health"] == "EMPTY"
    assert rows["b3"]["ingestion_health"] == "FAILED"
