from datetime import datetime, timezone

from backend.discovery_engine import (
    MAX_PRODUCTS_PER_CATEGORY_PER_RAIL, catalog_context, discovery_sections, diversify, gem_label,
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


def test_trending_brands_is_a_real_ranking_not_an_alias_of_emerging():
    # Real bug: app.py used to do `trending_brands = emerging_brands` verbatim -- same
    # brands, same order, despite the UI promising two different rankings. Engineer
    # two brands that clearly disagree between "quality/uniqueness" (emerging) and
    # "freshness/engagement/depth/niche" (trending), plus a third that's too thin
    # (only 1 eligible product) to qualify for either.
    long_ago = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    rows = (
        [product(f"Quality King {i}", brand_id="quality_king", brand_name="Quality King",
                 classifier_confidence=.98, niche_score=.9, scraped_at=long_ago) for i in range(2)]
        + [product(f"Fresh Find {i}", brand_id="fresh_and_engaged", brand_name="Fresh And Engaged",
                    classifier_confidence=.80, niche_score=.05, category_id=None, normalized_subcategory=None,
                    scraped_at=now) for i in range(2)]
        + [product("Lonely Item", brand_id="too_thin", brand_name="Too Thin", scraped_at=now)]
    )
    interactions = [{"target_id": f"Fresh Find {i}", "type": "save_product"} for i in range(2)]
    brands = [
        {"id": "quality_king", "name": "Quality King", "niche_score": .9},
        {"id": "fresh_and_engaged", "name": "Fresh And Engaged", "niche_score": .05},
        {"id": "too_thin", "name": "Too Thin", "niche_score": .5},
    ]
    sections = discovery_sections(rows, brands, supabase_url=BASE, interactions=interactions, limit=10)

    emerging_ids = {b["id"] for b in sections["emerging_brands"]}
    trending_ids = {b["id"] for b in sections["trending_brands"]}
    assert emerging_ids == {"quality_king"}
    assert trending_ids == {"fresh_and_engaged"}
    assert emerging_ids.isdisjoint(trending_ids)
    assert "too_thin" not in emerging_ids and "too_thin" not in trending_ids


def test_hidden_gems_category_isolation_and_brand_diversity():
    rows=[product(f"Crop top {i}",brand_id="same") for i in range(4)] + [product("Silver earrings",brand_id="jewel",description="silver hoop earrings",normalized_subcategory="accessories-jewellery")]
    sections=discovery_sections(rows,[{"id":"same","name":"Tops"},{"id":"jewel","name":"Jewels"}],supabase_url=BASE,category="tops",limit=10)
    assert sections["hidden_gems"]
    assert all(row["product_family"] == "tops" for row in sections["hidden_gems"])
    assert sum(row["brand_id"] == "same" for row in sections["hidden_gems"]) <= 2


def test_personalized_score_is_separate_from_global_score():
    row=product(); base=product_gem_score(row,catalog_context([row]),BASE)
    personalized, matched=personalized_score(base,row,{"preferences":{"preferred_styles":["party"]},"saved_brand_ids":{"small"}})
    assert personalized > base["gem_score"]
    assert matched is True
    assert product_gem_score(row,catalog_context([row]),BASE)["gem_score"] == base["gem_score"]


def test_personalized_score_is_not_matched_without_preferences_or_with_no_overlap():
    # Real bug: a signed-in user with a preferences object but zero actual overlap
    # (or no preferences at all) used to get the exact same number as a signed-out
    # visitor -- plain gem_score -- with nothing to tell them apart. `matched` is what
    # lets the frontend avoid rendering a fake-looking "X% match" in either case.
    row=product(); base=product_gem_score(row,catalog_context([row]),BASE)
    no_prefs, no_prefs_matched = personalized_score(base, row, None)
    assert no_prefs == base["gem_score"]
    assert no_prefs_matched is False
    unrelated, unrelated_matched = personalized_score(
        base, row, {"preferences": {"preferred_styles": ["minimalist"]}, "saved_brand_ids": {"someone-elses-brand"}}
    )
    assert unrelated == base["gem_score"]
    assert unrelated_matched is False


def test_diversity_is_deterministic():
    rows=[{"id":str(i),"brand_id":"a" if i<4 else "b"} for i in range(6)]
    assert [r["id"] for r in diversify(rows,4,max_per_brand=2)] == ["0","1","4","5"]


def _row(id_, *, brand, category, rank):
    # `rank` fakes "already sorted by this rail's score formula" -- lower rank = higher score.
    return {"id": id_, "brand_id": brand, "product_family": category, "_rank": rank}


def test_per_category_cap_stops_one_oversized_category_dominating():
    # This catalog's real skew: one category (Women Tops) with far more high-scoring
    # candidates than everything else combined. Without a category cap, a flat top-N
    # by score would be entirely (or almost entirely) that one category.
    rows = [_row(f"tops-{i}", brand=f"brand-{i}", category="tops", rank=i) for i in range(10)]
    rows += [_row(f"jewellery-{i}", brand=f"jewel-brand-{i}", category="jewellery", rank=100 + i) for i in range(3)]
    rows += [_row(f"dresses-{i}", brand=f"dress-brand-{i}", category="dresses", rank=200 + i) for i in range(3)]
    result = diversify(rows, 6, max_per_brand=1, max_per_category=MAX_PRODUCTS_PER_CATEGORY_PER_RAIL)
    categories = [r["product_family"] for r in result]
    assert categories.count("tops") <= MAX_PRODUCTS_PER_CATEGORY_PER_RAIL
    assert "jewellery" in categories and "dresses" in categories


def test_per_category_cap_never_under_fills_when_variety_is_scarce():
    # Real bug caught while building this: a category cap that leaves a rail short of
    # `limit` just because too few distinct categories exist is worse than no cap at
    # all. With only one category and enough distinct brands, the requested count
    # must still be reached.
    rows = [_row(str(i), brand=f"brand-{i}", category="only-category", rank=i) for i in range(6)]
    result = diversify(rows, 6, max_per_brand=1, max_per_category=3)
    assert len(result) == 6


def test_diversify_same_seed_is_stable_different_seed_can_reorder():
    rows = [_row(f"a{i}", brand=f"brand-a{i}", category="cat-a", rank=i) for i in range(5)]
    rows += [_row(f"b{i}", brand=f"brand-b{i}", category="cat-b", rank=i) for i in range(5)]
    once = [r["id"] for r in diversify(rows, 10, seed="user-123")]
    twice = [r["id"] for r in diversify(rows, 10, seed="user-123")]
    assert once == twice  # same seed (e.g. the same signed-in user) -> stable across reloads
    different_orders = {tuple(r["id"] for r in diversify(rows, 10, seed=f"user-{n}")) for n in range(15)}
    assert len(different_orders) > 1  # different seeds -> can (and does) vary
