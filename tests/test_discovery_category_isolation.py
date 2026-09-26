from backend.discovery_engine import _matches


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
