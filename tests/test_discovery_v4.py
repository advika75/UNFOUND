import pytest
from fastapi import HTTPException

from backend.app import _stable_product_image, build_catalog_scale_report, require_admin_key, representative_type_tiles
from backend.catalog_discovery import dedupe_brand_candidates, evaluate_brand_candidate


def product(name, image="https://example.com/item.jpg", **extra):
    return {"id": name, "product_name": name, "description": "", "image_url": image, "brand_id": extra.pop("brand_id", name), **extra}


def test_category_visual_types_select_stable_relevant_image_and_hide_zero_types(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    tiles = representative_type_tiles([
        product("Black crop top", "https://x.supabase.co/storage/v1/object/public/product-images/top.jpg", classifier_confidence=.9),
        product("Another crop top", image="", classifier_confidence=1),
        product("Silver earrings"),
    ], {"tops"})
    assert [tile["slug"] for tile in tiles] == ["crop-top"]
    assert tiles[0]["product_count"] == 2
    assert tiles[0]["image_url"].endswith("/product-images/top.jpg")


def test_missing_image_uses_neutral_null_fallback():
    tile = representative_type_tiles([product("Corset top", image=None)], {"tops"})[0]
    assert tile["image_url"] is None


def test_brand_targeting_rejects_non_sellers_and_deduplicates_handles():
    accepted = evaluate_brand_candidate({"name":"Indie Kurta Label", "handle":"@kurta_label", "bio":"Shop contemporary kurta collection shipping India", "recent_activity":True}, "kurtas")
    rejected = evaluate_brand_candidate({"name":"Kurta memes", "handle":"@kurta_memes", "bio":"meme fanpage", "recent_activity":True}, "kurtas")
    assert accepted["accepted"] is True
    assert rejected["accepted"] is False
    assert len(dedupe_brand_candidates([accepted, {**accepted, "name":"duplicate"}])) == 1


def test_scale_report_counts_only_valid_stable_assets():
    row = product(
        "Black crop top", "https://x.supabase.co/storage/v1/object/public/product-images/top.jpg",
        classifier_confidence=.9, category="women-tops", price=1999,
        embedding=[0.01] * 512,
    )
    report = build_catalog_scale_report([{"id": "b1"}], [row])
    assert report["coverage"]["stable_images"] == 1
    assert report["coverage"]["valid_embeddings"] == 1
    assert report["coverage"]["classified_type"] == 1
    assert report["inventory"]["price_brackets"]["1000_2500"] == 1
    assert report["milestones"]["products"]["2000"]["remaining"] == 1999


def test_admin_key_configuration_and_authorization(monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    with pytest.raises(HTTPException) as missing: require_admin_key(None)
    assert missing.value.status_code == 503
    monkeypatch.setenv("ADMIN_API_KEY", "test-only-secret")
    with pytest.raises(HTTPException) as wrong: require_admin_key("wrong")
    assert wrong.value.status_code == 401
    assert require_admin_key("test-only-secret") is None


def test_stable_image_requires_approved_storage(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    assert _stable_product_image({"image_url":"https://x.supabase.co/storage/v1/object/public/product-images/a.jpg"})
    assert not _stable_product_image({"image_url":"https://images.unsplash.com/a.jpg"})
    assert not _stable_product_image({"image_url":"https://cdninstagram.com/a.jpg"})
