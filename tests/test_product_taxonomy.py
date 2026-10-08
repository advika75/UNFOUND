from backend.app import extract_query_attributes
from backend.product_taxonomy import attach_category_audience, family_match, gender_match_score, identify_product, type_match


def test_denim_bag_is_not_a_jeans_type_match():
    # Regression: "denim" used to be a jeans term, so any denim item matched jeans.
    assert not type_match("ffm_greenearth denim bag", "jeans")
    assert not family_match("ffm_greenearth denim bag", "bottoms")


def test_denim_jacket_is_not_jeans():
    assert not type_match("anayracraft denim jacket", "jeans")
    assert identify_product("anayracraft denim jacket") == ("outerwear", "jacket")


def test_denim_bag_is_identified_as_a_bag():
    assert identify_product("denim bag") == ("bags", "bag")


def test_real_jeans_still_match_including_denim_jeans():
    assert type_match("baggy denim jeans", "jeans")
    assert identify_product("cotton denim jeans") == ("bottoms", "jeans")
    assert identify_product("wide leg jeans") == ("bottoms", "wide-leg-jeans")


def test_denim_is_still_captured_as_a_material_not_a_type():
    attributes = extract_query_attributes("vintage denim jacket")
    assert attributes["material"] == "denim"
    assert attributes["product_type"] == "jacket"


def test_attach_category_audience_fixes_a_product_with_no_own_audience_signal():
    # Regression: discovery/category-browse fetch products with a plain, unjoined
    # fetch_all() (no categories join), so gender_match_score() -- which checks
    # category_audience first, as its most trusted signal -- previously always saw
    # that field as absent, even for a product whose category unambiguously has a
    # gender (this is the real-world case for ~96% of products, whose own
    # `audience` column is null but whose category_id maps to a gendered category).
    product = {"id": "p1", "category_id": 8, "audience": None}
    categories = [{"id": 8, "slug": "men-shirts", "name": "Men Shirts", "audience": "MEN"}]

    # Without enrichment (the pre-fix state): no usable gender signal at all.
    assert gender_match_score(product, "men") == 0.0

    enriched = attach_category_audience([product], categories)[0]
    assert enriched["category_audience"] == "MEN"
    assert gender_match_score(enriched, "men") == 1.0
    # And it correctly still rejects the other gender.
    assert gender_match_score(enriched, "women") == 0.0


def test_attach_category_audience_handles_missing_and_unknown_category_ids():
    products = [
        {"id": "p1", "category_id": 8},
        {"id": "p2", "category_id": None},
        {"id": "p3", "category_id": 999},  # not present in categories at all
    ]
    categories = [{"id": 8, "audience": "MEN"}]
    enriched = attach_category_audience(products, categories)
    assert [row["category_audience"] for row in enriched] == ["MEN", None, None]


def test_attach_category_audience_does_not_mutate_the_input_rows():
    product = {"id": "p1", "category_id": 8}
    attach_category_audience([product], [{"id": 8, "audience": "MEN"}])
    assert "category_audience" not in product


def test_attach_category_audience_accepts_a_dict_values_view_like_category_map_returns():
    # category_map(supabase) returns a slug-keyed dict; real call sites pass
    # categories.values() straight through, not a list.
    categories_by_slug = {"men-shirts": {"id": 8, "audience": "MEN"}}
    enriched = attach_category_audience([{"id": "p1", "category_id": 8}], categories_by_slug.values())
    assert enriched[0]["category_audience"] == "MEN"
