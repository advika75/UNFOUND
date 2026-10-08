from backend.discovery_engine import _matches
from backend.product_taxonomy import attach_category_audience, gender_match_score


def test_discovery_category_filters_do_not_cross_audiences() -> None:
    women_top = {"product_name": "Women's Ribbed Crop Top", "audience": "WOMEN"}
    men_shirt = {"product_name": "Men's Oxford Shirt", "audience": "MEN"}
    earrings = {"product_name": "Minimal Silver Hoop Earrings", "audience": "UNISEX"}

    assert _matches(women_top, category="women-tops", product_type=None)
    assert not _matches(women_top, category="men-shirts", product_type=None)
    assert _matches(men_shirt, category="men-shirts", product_type=None)
    assert not _matches(men_shirt, category="women-tops", product_type=None)
    assert _matches(earrings, category="accessories-jewellery", product_type=None)
    assert not _matches(earrings, category="women-tops", product_type=None)


def test_a_rail_for_one_family_contains_only_that_family() -> None:
    """A rail for type X (e.g. jewellery) must never admit a different family (e.g.
    dresses), regardless of audience."""
    dress = {"product_name": "Floral Midi Dress", "audience": "WOMEN"}
    necklace = {"product_name": "Gold Pendant Necklace", "audience": "UNISEX"}
    food_photo = {
        "product_name": "amytasty White Top",
        "description": "Autumnal bake, serves 5-6, 2 aubergines...",
        "audience": None,
    }
    assert _matches(necklace, category="accessories-jewellery", product_type=None)
    assert not _matches(dress, category="accessories-jewellery", product_type=None)
    assert not _matches(food_photo, category="men-shirts", product_type=None)


def test_a_mens_rail_contains_no_womens_audience_products() -> None:
    """A rail for a men's type must contain no women's-audience products, and a
    blank/unknown audience must not be treated as a free pass (the exact bug that let
    a food photo and an unaudienced product slip into a "Men Shirts" rail)."""
    mens_shirt = {"product_name": "Men's Oxford Shirt", "audience": "MEN"}
    womens_shirt_style_top = {"product_name": "Women's Button-Down Shirt", "audience": "WOMEN"}
    blank_audience_shirt = {"product_name": "Classic Cotton Shirt", "audience": None}

    assert _matches(mens_shirt, category="men-shirts", product_type=None)
    assert not _matches(womens_shirt_style_top, category="men-shirts", product_type=None)
    assert not _matches(blank_audience_shirt, category="men-shirts", product_type=None)


def test_a_mens_rail_admits_a_product_whose_only_gender_signal_is_its_category() -> None:
    # Real-world case: discovery's fetch_all(supabase, "products") has no join to
    # categories, so a product with a blank own `audience` (~96% of the catalog)
    # had zero gender signal in _matches() even when it sits in an unambiguously
    # gendered category. attach_category_audience() is what the real /api/discovery
    # code paths now run before scoring -- this locks in that _matches() actually
    # honors the attached signal, not just that the helper computes it correctly.
    shirt = {"product_name": "Classic Cotton Shirt", "audience": None, "category_id": 8}
    categories = [{"id": 8, "slug": "men-shirts", "audience": "MEN"}]

    assert not _matches(shirt, category="men-shirts", product_type=None)

    enriched_shirt = attach_category_audience([shirt], categories)[0]
    assert _matches(enriched_shirt, category="men-shirts", product_type=None)
    assert not _matches(enriched_shirt, category="women-tops", product_type=None)


def test_gender_match_score_does_not_treat_men_as_a_substring_of_women() -> None:
    # Real bug found while reusing this function for discovery gating: Python's `in`
    # is substring containment, and "men" in "women" is True. A women's-audience
    # product must never pass a "men" gate just because "women" contains "men".
    assert gender_match_score({"audience": "WOMEN"}, "men") == 0.0
    assert gender_match_score({"audience": "MEN"}, "men") == 1.0
