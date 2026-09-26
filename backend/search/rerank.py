"""Transparent structured reranker for the fused candidate pool.

    final = sum over signals of contribution, where each contribution is
    weight * signal (audience uses its own match/mismatch weights).

Rules:
  * A signal the query did not specify contributes exactly 0 -- never a penalty.
  * Audience is the one signal allowed to penalize: an explicit mismatch
    (query says women, product is evidenced as men's) subtracts
    RERANK_WEIGHTS["audience_mismatch"], which is large enough to demote a
    result below most correct-audience results. No evidence either way is 0.
  * Every result carries score_breakdown (signal -> contribution, sums to
    final_score) and rerank_detail (signal -> raw value and weight).

All weights live in RERANK_WEIGHTS. Pure functions, no DB access.
"""

from __future__ import annotations

import re
from typing import Any

from backend.product_taxonomy import family_match, type_match

RERANK_WEIGHTS: dict[str, float] = {
    "fused": 0.35,
    "type": 0.25,
    "audience": 0.20,
    "audience_mismatch": 0.60,
    "colour": 0.08,
    "style": 0.05,
    "price": 0.05,
    "confidence": 0.02,
}

SIGNALS = ("fused", "type", "audience", "colour", "style", "price", "confidence")

_WOMEN = {"women", "womens", "woman", "female", "ladies"}
_MEN = {"men", "mens", "man", "male"}
_TOKEN = re.compile(r"[a-z]+")


def _haystack(product: dict[str, Any]) -> str:
    from backend.app import product_search_text

    raw_metadata = product.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    extra = " ".join(str(metadata.get(key) or "") for key in ("primary_colour", "secondary_colours", "style", "product_type"))
    return f"{product_search_text(product)} {extra}".lower()


def _audience_tokens(product: dict[str, Any]) -> set[str]:
    text = " ".join(str(product.get(field) or "") for field in (
        "category_audience", "audience", "category", "normalized_main_category", "normalized_subcategory",
    )).lower()
    return set(_TOKEN.findall(text))


def _canonical_gender(value: str | None) -> str | None:
    token = (value or "").strip().lower()
    if token in _WOMEN:
        return "women"
    if token in _MEN:
        return "men"
    return token or None


def audience_signal(product: dict[str, Any], requested: str | None) -> float:
    """+1 match (or unisex), -1 explicit opposite-gender evidence, 0 unspecified/no evidence."""
    wanted = _canonical_gender(requested)
    if wanted not in ("women", "men"):
        return 0.0
    tokens = _audience_tokens(product)
    has_women, has_men, has_unisex = bool(tokens & _WOMEN), bool(tokens & _MEN), "unisex" in tokens
    if has_unisex or (has_women if wanted == "women" else has_men):
        return 1.0
    if has_men if wanted == "women" else has_women:
        return -1.0
    return 0.0


def type_signal(haystack: str, attributes: dict[str, Any]) -> float:
    requested_type, requested_family = attributes.get("product_type"), attributes.get("product_family")
    if not requested_type and not requested_family:
        return 0.0
    if requested_type and type_match(haystack, requested_type):
        return 1.0
    if requested_family and family_match(haystack, requested_family):
        return 0.5
    return 0.0


def _phrase_signal(haystack: str, phrases: list[str]) -> float:
    phrases = [p.lower() for p in phrases if p]
    if not phrases:
        return 0.0
    return sum(1 for phrase in phrases if phrase in haystack) / len(phrases)


def price_signal(product: dict[str, Any], attributes: dict[str, Any]) -> float:
    """+1 inside the requested range, -1 outside it, 0 if no range requested or price unset (0/None)."""
    low, high = attributes.get("min_price"), attributes.get("max_price")
    if low is None and high is None:
        return 0.0
    price = product.get("price")
    if price is None or float(price) <= 0:
        return 0.0
    if (low is not None and float(price) < float(low)) or (high is not None and float(price) > float(high)):
        return -1.0
    return 1.0


def confidence_signal(product: dict[str, Any]) -> float:
    value = product.get("classifier_confidence")
    return max(0.0, min(1.0, float(value))) if value is not None else 0.0


def rerank_candidates(
    products: list[dict[str, Any]],
    attributes: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    price_bounds: tuple[float | None, float | None] | None = None,
) -> list[dict[str, Any]]:
    """Score and sort candidates. price_bounds overrides the parsed query price range (explicit API filters)."""
    w = weights or RERANK_WEIGHTS
    attrs = dict(attributes)
    if price_bounds is not None:
        attrs["min_price"], attrs["max_price"] = price_bounds

    raw = [float(p.get("similarity_score") or p.get("similarity") or 0.0) for p in products]
    lo, hi = (min(raw), max(raw)) if raw else (0.0, 0.0)

    scored: list[dict[str, Any]] = []
    for product, fused_raw in zip(products, raw):
        haystack = _haystack(product)
        signals = {
            "fused": (fused_raw - lo) / (hi - lo) if hi > lo else 1.0,
            "type": type_signal(haystack, attrs),
            "audience": audience_signal(product, attrs.get("gender")),
            "colour": _phrase_signal(haystack, attrs.get("colours") or []),
            "style": _phrase_signal(haystack, (attrs.get("styles") or []) + (attrs.get("fits") or []) + (attrs.get("aesthetic_terms") or [])),
            "price": price_signal(product, attrs),
            "confidence": confidence_signal(product),
        }
        contributions = {}
        for name in SIGNALS:
            if name == "audience":
                contributions[name] = w["audience"] * signals[name] if signals[name] >= 0 else -w["audience_mismatch"]
            else:
                contributions[name] = w[name] * signals[name]
        final = sum(contributions.values())
        scored.append({
            **product,
            "final_score": final,
            "score_breakdown": contributions,
            "rerank_detail": {name: {"value": signals[name], "weight": w[name], "contribution": contributions[name]} for name in SIGNALS},
        })
    from backend.app import deterministic_rank_key

    return sorted(scored, key=deterministic_rank_key)
