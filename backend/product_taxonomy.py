"""Central product-family taxonomy shared by search, navigation, and catalog tools."""

from __future__ import annotations

import re
from typing import Any, Iterable


PRODUCT_FAMILIES: dict[str, dict[str, tuple[str, ...]]] = {
    "tops": {
        "crop-top": ("crop top", "cropped top"),
        "tank-top": ("tank top", "tank", "camisole", "cami"),
        "corset-top": ("corset top", "corset", "bustier", "fitted top"),
        "party-top": ("party top", "going out top", "going-out top"),
        "casual-top": ("casual top",),
        "blouse": ("blouse",),
        "top": ("top", "tops"),
    },
    "ethnic-upperwear": {
        "kurta": ("kurta", "kurtas"),
        "kurti": ("kurti", "kurtis"),
        "ethnic-top": ("ethnic top", "tunic"),
    },
    "shirts": {
        "oversized-shirt": ("oversized shirt",),
        "linen-shirt": ("linen shirt",),
        "shirt": ("shirt", "shirts", "button-down", "button down"),
    },
    "bottoms": {
        "cargo-pants": ("cargo pants", "cargo pant", "cargo trouser", "utility trouser"),
        "wide-leg-jeans": ("wide leg jeans", "wide-leg jeans"),
        # "denim" is deliberately not a jeans term: it is a material (extracted separately as
        # query material), and matching it made a "Denim Bag" or "Denim Jacket" count as jeans.
        "jeans": ("jeans",),
        "trousers": ("trousers", "trouser", "pants"),
        "shorts": ("shorts",),
        "skirts": ("skirt", "skirts"),
    },
    "dresses": {
        "mini-dress": ("mini dress",),
        "party-dress": ("party dress",),
        "dress": ("dress", "dresses", "gown"),
    },
    "sets": {
        "co-ord-set": ("co-ord", "co ord", "coord", "matching set"),
    },
    "outerwear": {
        "jacket": ("jacket", "blazer", "coat", "outerwear"),
    },
    "jewellery": {
        "earrings": ("earring", "earrings", "hoop earrings", "hoops"),
        "necklace": ("necklace", "necklaces"),
        "ring": ("ring", "rings"),
        "bracelet": ("bracelet", "bracelets", "bangle"),
        "anklet": ("anklet", "anklets"),
        "jewellery": ("jewellery", "jewelry"),
    },
    "bags": {
        "tote": ("tote", "tote bag"),
        "shoulder-bag": ("shoulder bag",),
        "sling-bag": ("sling bag", "sling"),
        "clutch": ("clutch",),
        "crossbody": ("crossbody", "crossbody bag"),
        "backpack": ("backpack",),
        "bag": ("bag", "bags", "handbag"),
    },
    "footwear": {
        "sneakers": ("sneaker", "sneakers", "trainer", "trainers"),
        "heels": ("heel", "heels"),
        "sandals": ("sandal", "sandals"),
        "loafers": ("loafer", "loafers"),
        "boots": ("boot", "boots"),
    },
    "watches": {"watch": ("watch", "watches", "timepiece", "chronograph")},
    "lingerie": {"lingerie": ("lingerie",)},
    "underwear": {"underwear": ("underwear", "innerwear")},
    "hoodies": {"hoodie": ("hoodie", "hoodies")},
    "socks": {"socks": ("sock", "socks")},
}

FAMILY_LABELS = {
    "tops": "Tops", "ethnic-upperwear": "Ethnic Upperwear", "shirts": "Shirts",
    "bottoms": "Bottoms", "dresses": "Dresses", "sets": "Sets", "outerwear": "Outerwear",
    "jewellery": "Jewellery", "bags": "Bags", "footwear": "Footwear", "watches": "Watches",
    "lingerie": "Lingerie", "underwear": "Underwear", "hoodies": "Hoodies", "socks": "Socks",
}

TYPE_LABELS = {slug: slug.replace("-", " ").title() for types in PRODUCT_FAMILIES.values() for slug in types}
TYPE_LABELS.update({"co-ord-set": "Co-ord Sets", "kurta": "Kurtas", "kurti": "Kurtis"})

STOPWORDS = {"under", "below", "with", "from", "this", "that", "show", "find", "stylish", "trendy"}


def _term_pattern(term: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(term)}(?:s)?(?![a-z0-9])"


def _contains(text: str, term: str) -> bool:
    return bool(re.search(_term_pattern(term), text))


def type_regex(product_type: str | None) -> str:
    """One regex matching any term of the type. Empty string when the type has no terms.

    Identical to what type_match applies term by term, so a database-side `~` filter using this
    pattern selects exactly the rows type_match would (Postgres ARE and Python re agree on this
    lookaround/optional-plural subset; verified against the whole catalog for every taxonomy type).
    """
    return "|".join(_term_pattern(term) for term in type_terms(product_type))


def identify_product(text: str) -> tuple[str | None, str | None]:
    """Return the most-specific (family, type); long phrases always win."""
    normalized = text.lower()
    matches: list[tuple[int, str, str]] = []
    for family, types in PRODUCT_FAMILIES.items():
        for product_type, terms in types.items():
            for term in terms:
                if _contains(normalized, term):
                    matches.append((len(term), family, product_type))
    if not matches:
        return None, None
    _, family, product_type = max(matches, key=lambda row: row[0])
    return family, product_type


def family_terms(family: str | None) -> tuple[str, ...]:
    if not family or family not in PRODUCT_FAMILIES:
        return ()
    return tuple(dict.fromkeys(term for terms in PRODUCT_FAMILIES[family].values() for term in terms))


def type_terms(product_type: str | None) -> tuple[str, ...]:
    for types in PRODUCT_FAMILIES.values():
        if product_type in types:
            return types[product_type]
    return ()


def classify_product_text(text: str) -> tuple[str | None, str | None]:
    return identify_product(text)


def type_match(text: str, product_type: str | None) -> bool:
    return not product_type or any(_contains(text.lower(), term) for term in type_terms(product_type))


def family_match(text: str, family: str | None) -> bool:
    return not family or any(_contains(text.lower(), term) for term in family_terms(family))


# Shared by gender_match_score and gender_explicitly_contradicts so the two can
# never disagree about which fields carry audience evidence. category_audience
# comes from categories.audience (e.g. "WOMEN" for the "Kurtis" category) -- a
# reliable signal even when the category's own display name has no literal
# gender word in it, and far more complete than the product's own audience
# column (populated on well under 4% of rows). Checked first as the most
# trustworthy source.
_AUDIENCE_FIELDS = ("category_audience", "audience", "category", "normalized_main_category", "normalized_subcategory")
_SPECIFIC_GENDERS = ("men", "women")


def _audience_text(product: dict[str, Any]) -> str:
    return " ".join(str(product.get(field) or "") for field in _AUDIENCE_FIELDS).lower()


def gender_match_score(product: dict[str, Any], requested_gender: str | None) -> float:
    """The one gender/audience gate every gendered path (search, discovery, category
    browse) should call -- never reimplement this check separately, it will drift."""
    if not requested_gender:
        return 0.5
    audience = _audience_text(product)
    # Word-boundary match, not plain substring: "men" in "women" is True for Python's
    # `in`, which would wrongly pass a women's-audience row for a men's-gated rail.
    if _contains(audience, requested_gender):
        return 1.0
    if _contains(audience, "unisex"):
        return 0.75
    return 0.0


def gender_explicitly_contradicts(product: dict[str, Any], requested_gender: str | None) -> bool:
    """True only when the product's own audience evidence names a *different*,
    specific gender than the one requested -- never for blank/absent evidence
    (unknown is not the same as contradicted) and never for "unisex" (a valid,
    explicit match). gender_match_score() collapses both "no evidence" and "the
    opposite gender" to the same 0.0, which is correct for scoring but wrong for
    deciding which candidates to evict from a results pool -- that needs this
    finer distinction instead.
    """
    if not requested_gender:
        return False
    requested = requested_gender.lower()
    audience = _audience_text(product)
    if not audience.strip():
        return False
    if _contains(audience, "unisex") or _contains(audience, requested):
        return False
    return any(_contains(audience, other) for other in _SPECIFIC_GENDERS if other != requested)


def attach_category_audience(
    products: list[dict[str, Any]], categories: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Enrich each product with its own category's audience (categories.audience,
    joined by category_id) as category_audience -- gender_match_score()'s most
    trusted signal.

    The search RPCs (match_products, lexical_search_products -- see
    category_audience_signal_migration.sql) already select this via a SQL join.
    The discovery and category-browse code paths instead fetch products with a
    plain, unjoined fetch_all(supabase, "products"), so without this, every row
    they hand to gender_match_score() is missing that field entirely and falls
    through to the product's own audience column, which is NULL for ~96% of rows
    -- not because the signal doesn't exist, but because it was never attached.
    """
    audience_by_category_id = {row.get("id"): row.get("audience") for row in categories if row.get("id") is not None}
    return [
        {**product, "category_audience": audience_by_category_id.get(product.get("category_id"))}
        for product in products
    ]


def slugify_type(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
