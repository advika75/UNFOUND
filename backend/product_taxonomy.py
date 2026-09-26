"""Central product-family taxonomy shared by search, navigation, and catalog tools."""

from __future__ import annotations

import re
from typing import Any


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


def slugify_type(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
