from backend.app import extract_query_attributes
from backend.product_taxonomy import family_match, identify_product, type_match


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
