from datetime import datetime, timezone

from backend.discovery_engine import (
    catalog_context, discovery_sections, diversify, gem_label,
    personalized_score, product_gem_score, score_products,
)

BASE = "https://project.supabase.co"
VECTOR = [1 / (512 ** .5)] * 512


def product(name="Black crop top", **extra):
    row={"id":name,"brand_id":"small","brand_name":"Small Label","product_name":name,"description":"women fitted party top","category_id":1,"normalized_subcategory":"women-tops","classifier_confidence":.95,"image_url":f"{BASE}/storage/v1/object/public/product-images/small/a.jpg","image_status":"VALID","embedding":VECTOR,"niche_score":.88,"scraped_at":datetime.now(timezone.utc).isoformat()}
    row.update(extra); return row


def test_product_gem_score_is_deterministic_and_explainable():
    rows=[product()]; context=catalog_context(rows)
    first=product_gem_score(rows[0],context,BASE); second=product_gem_score(rows[0],context,BASE)
    assert first == second
    assert 0 <= first["gem_score"] <= 100
    assert {"quality","uniqueness","niche_factor","freshness","engagement"} == set(first["components"])
    assert gem_label(first["gem_score"]) == first["gem_label"]


def test_missing_optional_data_does_not_destroy_score_but_broken_assets_are_ineligible():
    good=product(price=None,niche_score=None)
    broken=product("Broken crop top",price=None,niche_score=None,image_url="",embedding=None)
    scored=score_products([good,broken],supabase_url=BASE)
    assert scored[0]["gem_score"] > 0 and scored[0]["discovery_eligible"]
    assert not scored[1]["discovery_eligible"]


def test_hidden_gems_category_isolation_and_brand_diversity():
    rows=[product(f"Crop top {i}",brand_id="same") for i in range(4)] + [product("Silver earrings",brand_id="jewel",description="silver hoop earrings",normalized_subcategory="accessories-jewellery")]
    sections=discovery_sections(rows,[{"id":"same","name":"Tops"},{"id":"jewel","name":"Jewels"}],supabase_url=BASE,category="tops",limit=10)
    assert sections["hidden_gems"]
    assert all(row["product_family"] == "tops" for row in sections["hidden_gems"])
    assert sum(row["brand_id"] == "same" for row in sections["hidden_gems"]) <= 2


def test_personalized_score_is_separate_from_global_score():
    row=product(); base=product_gem_score(row,catalog_context([row]),BASE)
    personalized=personalized_score(base,row,{"preferences":{"preferred_styles":["party"]},"saved_brand_ids":{"small"}})
    assert personalized > base["gem_score"]
    assert product_gem_score(row,catalog_context([row]),BASE)["gem_score"] == base["gem_score"]


def test_diversity_is_deterministic():
    rows=[{"id":str(i),"brand_id":"a" if i<4 else "b"} for i in range(6)]
    assert [r["id"] for r in diversify(rows,4,max_per_brand=2)] == ["0","1","4","5"]
