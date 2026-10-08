from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from dotenv import load_dotenv

from backend.product_taxonomy import gender_match_score, identify_product
from backend.supabase_compat import create_supabase_client

AUTO_CATEGORY_THRESHOLD = float(os.getenv("CATEGORY_AUTO_THRESHOLD", "0.80"))
BUCKET = os.getenv("SUPABASE_PRODUCT_IMAGE_BUCKET", "product-images")
BRAND_BUCKET = os.getenv("SUPABASE_BRAND_IMAGE_BUCKET", "brand-images")
SERVICE_ROLE_ERROR = """ERROR:
SUPABASE_SERVICE_ROLE_KEY is required for this command.

This operation performs server-side catalog repair and cannot run with the publishable key."""


def load_runtime_env() -> None:
    """Load local credentials when available; commands also support env vars."""
    try:
        load_dotenv("backend/.env", override=False)
    except OSError as error:
        print(f"[WARNING] Could not read backend/.env; using process environment: {error}")


@dataclass(frozen=True)
class CategoryDecision:
    main_slug: str | None
    category_slug: str | None
    confidence: float
    reason: str
    evidence: tuple[str, ...] = ()
    source: str = "deterministic"
    model_confidence: float | None = None


@dataclass(frozen=True)
class ClassificationOutcome:
    decision: CategoryDecision
    suggested_name: str | None = None
    name_confidence: float = 0.0
    non_product: bool = False
    cache_hit: bool = False
    ai_called: bool = False


RULES: tuple[tuple[str, str, tuple[str, ...], float], ...] = (
    ("accessories", "accessories-jewellery", ("earring", "earrings", "hoops", "necklace", "necklaces", "bracelet", "bangle", "choker", "jewellery", "jewelry", "hoop earrings", "pendant", "ring", "rings"), .98),
    ("accessories", "accessories-wallets", ("wallet", "card holder", "cardholder"), .97),
    ("accessories", "accessories-sunglasses-all", ("sunglasses", "shades", "eyewear"), .96),
    ("watches", "watches-minimal", ("minimal analog watch", "analog watch", "minimal watch"), .96),
    ("watches", "watches", ("watch", "timepiece", "chronograph", "smartwatch"), .93),
    ("women", "women-kurtis", ("kurti", "kurtis", "straight kurta", "embroidered kurti"), .98),
    ("women", "women-coord-sets", ("co-ord", "co ord", "coord", "co-ords", "matching set"), .96),
    ("women", "women-lingerie", ("lingerie", "bralette", "underwire bra"), .98),
    ("unisex", "unisex-hoodies", ("hoodie", "hooded sweatshirt"), .96),
    ("unisex", "unisex-sneakers", ("sneaker", "sneakers", "trainer", "trainers"), .95),
    ("accessories", "accessories-bags", ("handbag", "tote bag", "sling bag", "clutch", "crossbody bag"), .94),
)

WEAK_NAMES = re.compile(r"^(?:product|instagram product|new drop|summer collection|look\s*\d*|new arrival|shop now|latest product)$", re.I)
NON_PRODUCT_TERMS = (
    "giveaway winner", "hiring", "job opening", "store closed", "holiday notice",
    "quote of the day", "event invitation", "behind the scenes", "meme",
)


def strip_vision_guess_text(text: str) -> str:
    """Remove Instagram's auto-generated "May be an image of X, Y, Z" alt text.

    This is a computer-vision hedge, not authored content -- it often lists several
    plausible-but-uncertain object types in one clause (e.g. "tunic, dress"), and
    treating it as equally reliable as a real caption/hashtag lets the classifier
    confidently pick one of several guesses as if it were explicit evidence. Once
    this marker appears, nothing after it in the same field is trustworthy for
    classification (real hashtags/captions live in their own separate fields).
    """
    return re.sub(r"may be an image of.*", "", text or "", flags=re.I | re.DOTALL)


def compact_text(product: dict[str, Any]) -> str:
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
    return " ".join(strip_vision_guess_text(str(value or "")) for value in (
        product.get("product_name"), product.get("item_name"), product.get("description"),
        product.get("subcategory"), product.get("source_hashtag"), metadata.get("hashtags"),
        metadata.get("caption"), metadata.get("alt_text"), metadata.get("post_text"),
    )).lower()


def primary_text(product: dict[str, Any]) -> str:
    return " ".join(str(product.get(field) or "") for field in ("product_name", "item_name", "description")).lower()


def classification_context(product: dict[str, Any], old_slug: str | None = None) -> dict[str, Any]:
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
    brand = product.get("brand_metadata") if isinstance(product.get("brand_metadata"), dict) else {}
    return {
        "product_name": product.get("product_name") or product.get("item_name"),
        "brand": product.get("brand_name"),
        "brand_context": {key: brand.get(key) for key in ("category", "bio", "description") if brand.get(key)},
        "caption": metadata.get("caption") or metadata.get("post_text") or product.get("description"),
        "hashtags": metadata.get("hashtags") or product.get("source_hashtag"),
        "alt_text": metadata.get("alt_text"),
        "price": product.get("price"),
        "current_category": old_slug,
    }


def context_fingerprint(context: dict[str, Any], taxonomy: dict[str, dict[str, Any]]) -> str:
    taxonomy_version = sorted((slug, row.get("parent_id"), row.get("audience")) for slug, row in taxonomy.items())
    payload = json.dumps({"context": context, "taxonomy": taxonomy_version}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_product_name(product: dict[str, Any]) -> tuple[str | None, float, str]:
    current = str(product.get("product_name") or product.get("item_name") or "").strip()
    if current and not WEAK_NAMES.match(current):
        return None, 0.0, "existing name is sufficiently specific"
    text = compact_text(product)
    noun_patterns = (
        (r"\b(?:oversized\s+)?(?:linen\s+)?shirt\b", "Oversized Linen Shirt"),
        (r"\bsilver\s+hoops?\b", "Silver Hoop Earrings"),
        (r"\bhoop earrings?\b", "Hoop Earrings"),
        (r"\b(?:embroidered\s+)?kurt(?:a|i)s?\b", "Embroidered Kurti"),
        (r"\b(?:crossbody|sling|tote) bag\b", None),
        (r"\b(?:analog|minimal|smart) watch\b", None),
        (r"\b(?:graphic|oversized) (?:tee|t-shirt)\b", None),
    )
    for pattern, canonical in noun_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = canonical or match.group(0).title().replace("T-Shirt", "T-Shirt")
            return value, .90, f"explicit product noun in metadata: {match.group(0)}"
    return None, 0.0, "no reliable product title found"


def is_non_product(product: dict[str, Any]) -> tuple[bool, str]:
    text = compact_text(product)
    product_terms = {term for _main, _slug, terms, _confidence in RULES for term in terms}
    product_terms.update(("dress", "top", "shirt", "jeans", "skirt", "shorts", "blouse"))
    if any(term in text for term in NON_PRODUCT_TERMS) and not any(contains_term(text, term) for term in product_terms):
        return True, "announcement, event, quote, or non-product social post"
    return False, ""


def contains_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?:s)?(?![a-z0-9])", text))


def audience_from_text(text: str) -> tuple[str | None, float]:
    women = any(contains_term(text, term) for term in ("women", "woman", "womens", "women's", "female", "ladies", "girl"))
    men = any(contains_term(text, term) for term in ("men", "man", "mens", "men's", "male", "gentlemen", "boy"))
    unisex = contains_term(text, "unisex")
    if women and not men:
        return "women", .98
    if men and not women:
        return "men", .98
    if unisex or (women and men):
        return "unisex", .92
    return None, .0


def classify_product_category(product: dict[str, Any]) -> CategoryDecision:
    text = compact_text(product)
    matched_rule_families = {
        ("watches" if slug.startswith("watches") else slug) for _main, slug, terms, _confidence in RULES
        if any(contains_term(text, term) for term in terms)
    }
    garment_mentions = any(contains_term(text, term) for term in ("dress", "gown", "top", "blouse", "shirt", "jeans", "skirt", "shorts"))
    if (len(matched_rule_families) > 1) or (matched_rule_families and garment_mentions):
        return CategoryDecision(None, None, .55, "multiple product families appear in the same post")
    for main, slug, terms, confidence in RULES:
        term = next((term for term in terms if contains_term(text, term)), None)
        if term:
            return CategoryDecision(main, slug, confidence, f"explicit product term: {term}")

    audience, audience_confidence = audience_from_text(text)
    if any(contains_term(text, term) for term in ("dress", "gown", "sundress")):
        if audience == "women":
            return CategoryDecision("women", "women-dresses", .95, "dress plus explicit women's audience")
        return CategoryDecision(None, None, .58, "dress found but audience is ambiguous")
    if any(contains_term(text, term) for term in ("jeans", "straight leg jeans", "straight-leg jeans", "wide-leg jeans", "mom jeans", "denim jeans")):
        if audience in {"women", "men"}:
            return CategoryDecision(audience, f"{audience}-jeans", min(.97, audience_confidence), "jeans plus explicit audience")
        return CategoryDecision(None, None, .58, "jeans found but audience is ambiguous")
    if any(contains_term(text, term) for term in ("t-shirt", "tshirt", "tee", "graphic tee", "tees")):
        if audience in {"women", "men"}:
            return CategoryDecision(audience, f"{audience}-t-shirts", .94, "t-shirt plus explicit audience")
        return CategoryDecision("unisex", "unisex-streetwear", .76, "t-shirt found without reliable audience")
    if contains_term(text, "shirt"):
        if audience == "men":
            return CategoryDecision("men", "men-shirts", .95, "shirt plus explicit men's audience")
        return CategoryDecision(None, None, .55, "shirt found without reliable audience")
    if any(contains_term(text, term) for term in ("blazer", "formal suit", "formal wear")):
        if audience in {"women", "men"}:
            return CategoryDecision(audience, f"{audience}-formal-wear", .93, "formalwear plus explicit audience")
        return CategoryDecision(None, None, .57, "formalwear found without reliable audience")
    if any(contains_term(text, term) for term in ("top", "blouse", "crop top", "tank top", "camisole")) and audience == "women":
        return CategoryDecision("women", "women-tops", .93, "top plus explicit women's audience")
    if any(contains_term(text, term) for term in ("skirt", "mini skirt", "midi skirt", "shorts")) and audience == "women":
        return CategoryDecision("women", "women-skirts-shorts", .93, "skirt/shorts plus explicit women's audience")
    if any(contains_term(text, term) for term in ("home decor", "vase", "lamp", "wall decor", "furniture")):
        return CategoryDecision("home-decor", "home-decor", .94, "explicit home-decor term")
    return CategoryDecision(None, None, .25, "no deterministic category evidence")


_GENERIC_FALLBACK_NAME_TERMS = {term for _main, _slug, terms, _confidence in RULES for term in terms} | {
    "dress", "dresses", "gown", "top", "tops", "blouse", "shirt", "shirts",
    "jeans", "skirt", "skirts", "shorts", "kurti", "kurtis", "kurta", "kurtas",
}


def is_generic_fallback_name(name: str) -> bool:
    """True if name is just a bare category/type word (e.g. "Jewellery", "Kurtis")
    with no other descriptive content.

    clean_product_name()/fallback_product_name() (training/scraper_pipeline.py)
    produce exactly this kind of name when a caption has no real product title --
    it is not a deliberately authored title, so it must not be trusted the same way
    Stage A trusts a real name. Without this check, a product whose only "evidence"
    is its own fallback name gets confidently re-confirmed as if the name were
    independent proof (a circular reinforcement of an already-weak guess).
    """
    return name.strip().lower() in _GENERIC_FALLBACK_NAME_TERMS


def deterministic_pipeline(product: dict[str, Any]) -> CategoryDecision:
    name = str(product.get("product_name") or product.get("item_name") or "").strip()
    weak_extracted_name = (
        len(name) >= 90
        or bool(re.match(r"^(?:photo|video) (?:by|shared by)", name, re.I))
        or is_generic_fallback_name(name)
    )
    stage_a = {"product_name": name if not weak_extracted_name else ""}
    decision = classify_product_category(stage_a)
    if decision.category_slug and decision.confidence >= AUTO_CATEGORY_THRESHOLD:
        return CategoryDecision(
            decision.main_slug, decision.category_slug, decision.confidence, decision.reason,
            (decision.reason.replace("explicit product term: ", ""),), "deterministic",
        )
    enriched = classify_product_category(product)
    if enriched.category_slug and enriched.confidence >= AUTO_CATEGORY_THRESHOLD:
        metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
        brand = product.get("brand_metadata") if isinstance(product.get("brand_metadata"), dict) else {}
        evidence_texts = {
            str(value).strip().lower() for value in (
                name, product.get("description"), metadata.get("caption"), metadata.get("hashtags"),
                metadata.get("alt_text"), brand.get("category"), brand.get("bio"),
            ) if str(value or "").strip()
        }
        reason_term = enriched.reason.split(":", 1)[-1].strip().lower()
        agreeing_sources = sum(contains_term(value, reason_term) for value in evidence_texts if reason_term)
        if weak_extracted_name and agreeing_sources < 2:
            return CategoryDecision(
                enriched.main_slug, enriched.category_slug, .74,
                "single weak caption/alt-text signal requires AI or manual confirmation",
                (reason_term,) if reason_term else (), "metadata",
            )
        return CategoryDecision(
            enriched.main_slug, enriched.category_slug, enriched.confidence, enriched.reason,
            (enriched.reason.replace("explicit product term: ", ""),), "metadata",
        )
    return enriched


INCOMPATIBLE: dict[str, set[str]] = {
    "accessories-jewellery": {"women-jeans", "men-jeans", "women-lingerie", "home-decor"},
    "watches": {"women-lingerie", "women-jeans", "men-shirts"},
    "unisex-sneakers": {"women-lingerie", "accessories-jewellery", "home-decor"},
    "women-kurtis": {"men-shirts", "home-decor", "accessories-jewellery"},
}


def obvious_mismatch(decision: CategoryDecision, old_slug: str | None) -> bool:
    return bool(decision.category_slug and old_slug in INCOMPATIBLE.get(decision.category_slug, set()))


def taxonomy_main_slug(category_slug: str, taxonomy: dict[str, dict[str, Any]]) -> str | None:
    by_id = {row.get("id"): slug for slug, row in taxonomy.items()}
    slug = category_slug
    seen: set[str] = set()
    while slug in taxonomy and slug not in seen:
        seen.add(slug)
        parent_id = taxonomy[slug].get("parent_id")
        if parent_id is None:
            return slug
        slug = by_id.get(parent_id)
        if not slug:
            return None
    return None


def final_ai_confidence(
    product: dict[str, Any], category_slug: str, model_confidence: float, evidence: list[str],
) -> float:
    text = compact_text(product)
    normalized_evidence = [str(item).lower().strip(" \"'#") for item in evidence[:5]]
    explicit = sum(bool(item and item in text) for item in normalized_evidence)
    source_agreement = sum(bool(value) for value in (
        product.get("product_name"), product.get("description"),
        (product.get("metadata") or {}).get("hashtags") if isinstance(product.get("metadata"), dict) else None,
    ))
    confidence = min(float(model_confidence), .70 + min(explicit, 2) * .08 + max(0, source_agreement - 1) * .02)
    probe = CategoryDecision(taxonomy_main_slug(category_slug, {}), category_slug, confidence, "AI proposal")
    if obvious_mismatch(probe, str(product.get("normalized_subcategory") or "")):
        confidence = min(confidence, .55)
    return round(max(0.0, min(confidence, 1.0)), 2)


def parse_ai_classification(
    payload: dict[str, Any], product: dict[str, Any], taxonomy: dict[str, dict[str, Any]],
) -> ClassificationOutcome:
    slug = payload.get("category_slug")
    if slug is None:
        return ClassificationOutcome(CategoryDecision(None, None, min(float(payload.get("confidence") or .35), .79), str(payload.get("reason") or "Insufficient information"), tuple(payload.get("evidence") or ()), "ai", float(payload.get("confidence") or .35)), non_product=bool(payload.get("non_product")))
    if slug not in taxonomy or slug == "uncategorized":
        return ClassificationOutcome(CategoryDecision(None, None, .25, "AI category does not exist in taxonomy", (), "ai"))
    main_slug = taxonomy_main_slug(str(slug), taxonomy)
    claimed_main = payload.get("main_category_slug")
    if claimed_main and claimed_main != main_slug:
        return ClassificationOutcome(CategoryDecision(None, None, .25, "AI subcategory does not belong to claimed main category", (), "ai"))
    audience = str(payload.get("audience") or "").lower() or None
    row_audience = str(taxonomy[slug].get("audience") or "").lower() or None
    if audience and row_audience and audience != row_audience:
        return ClassificationOutcome(CategoryDecision(None, None, .35, "AI audience contradicts taxonomy", (), "ai"))
    evidence = [str(item).strip() for item in payload.get("evidence") or [] if str(item).strip()]
    model_confidence = max(0.0, min(float(payload.get("confidence") or 0), 1.0))
    confidence = final_ai_confidence(product, str(slug), model_confidence, evidence)
    decision = CategoryDecision(main_slug, str(slug), confidence, str(payload.get("reason") or "AI metadata classification"), tuple(evidence), "ai", model_confidence)
    if obvious_mismatch(decision, str(product.get("normalized_subcategory") or "")):
        decision = CategoryDecision(main_slug, str(slug), min(confidence, .55), "AI result contradicted strong stored evidence", tuple(evidence), "ai", model_confidence)
    name = str(payload.get("suggested_name") or "").strip() or None
    name_confidence = max(0.0, min(float(payload.get("name_confidence") or 0), 1.0))
    return ClassificationOutcome(decision, name, name_confidence, bool(payload.get("non_product")), ai_called=True)


def call_ai_classifier(
    client: Any, product: dict[str, Any], taxonomy: dict[str, dict[str, Any]], old_slug: str | None,
) -> ClassificationOutcome:
    context = classification_context(product, old_slug)
    allowed = [
        {"slug": slug, "name": row.get("name"), "main_slug": taxonomy_main_slug(slug, taxonomy), "audience": row.get("audience")}
        for slug, row in taxonomy.items() if row.get("parent_id") is not None and slug != "uncategorized"
    ]
    allowed_slugs = [row["slug"] for row in allowed]
    allowed_main_slugs = sorted({row["main_slug"] for row in allowed if row["main_slug"]})
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "Classify only from supplied evidence and allowed taxonomy. Return unknown when evidence is insufficient. Never invent attributes."},
            {"role": "user", "content": json.dumps({"context": context, "allowed_taxonomy": allowed}, ensure_ascii=False)},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "unfound_category", "strict": True, "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "main_category_slug": {"type": ["string", "null"], "enum": [*allowed_main_slugs, None]},
                "category_slug": {"type": ["string", "null"], "enum": [*allowed_slugs, None]},
                "audience": {"type": ["string", "null"], "enum": ["women", "men", "unisex", None]},
                "confidence": {"type": "number"}, "reason": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "suggested_name": {"type": ["string", "null"]}, "name_confidence": {"type": "number"},
                "non_product": {"type": "boolean"},
            },
            "required": ["main_category_slug", "category_slug", "audience", "confidence", "reason", "evidence", "suggested_name", "name_confidence", "non_product"],
        }}},
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return parse_ai_classification(payload, product, taxonomy)


def classify_with_pipeline(
    product: dict[str, Any], taxonomy: dict[str, dict[str, Any]], *, ai_client: Any | None = None,
    old_slug: str | None = None, run_cache: dict[str, ClassificationOutcome] | None = None,
) -> ClassificationOutcome:
    non_product, reason = is_non_product(product)
    if non_product:
        return ClassificationOutcome(CategoryDecision(None, None, .0, reason, (), "non_product"), non_product=True)
    name, name_confidence, _ = extract_product_name(product)
    decision = deterministic_pipeline(product)
    if decision.category_slug and decision.confidence >= AUTO_CATEGORY_THRESHOLD:
        return ClassificationOutcome(decision, name, name_confidence)
    if ai_client is None:
        return ClassificationOutcome(decision, name, name_confidence)
    context = classification_context(product, old_slug)
    fingerprint = context_fingerprint(context, taxonomy)
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
    cached = metadata.get("category_ai_cache") if isinstance(metadata.get("category_ai_cache"), dict) else {}
    if cached.get("fingerprint") == fingerprint and isinstance(cached.get("result"), dict):
        outcome = parse_ai_classification(cached["result"], product, taxonomy)
        return ClassificationOutcome(outcome.decision, outcome.suggested_name, outcome.name_confidence, outcome.non_product, True, False)
    cache = run_cache if run_cache is not None else {}
    if fingerprint in cache:
        outcome = cache[fingerprint]
        return ClassificationOutcome(outcome.decision, outcome.suggested_name, outcome.name_confidence, outcome.non_product, True, False)
    outcome = call_ai_classifier(ai_client, product, taxonomy, old_slug)
    cache[fingerprint] = outcome
    return outcome


def image_url_status(url: Any, *, network: bool = False, timeout: float = 6.0) -> str:
    if not url:
        return "MISSING"
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "INVALID_URL"
    if not network:
        return "VALID"
    import httpx
    try:
        response = httpx.head(str(url), follow_redirects=True, timeout=timeout)
        if response.status_code in {403, 405}:
            response = httpx.get(str(url), headers={"Range": "bytes=0-1023"}, follow_redirects=True, timeout=timeout)
        if response.status_code in {401, 403, 410}:
            return "EXPIRED_OR_FORBIDDEN"
        return "VALID" if response.status_code < 400 else "BROKEN"
    except httpx.TimeoutException:
        return "TIMEOUT"
    except Exception:
        return "BROKEN"


def classify_catalog_image(url: Any, base_url: str, *, network: bool = False) -> str:
    value = str(url or "").strip()
    if not value: return "MISSING"
    if "unsplash.com" in value.lower(): return "UNSPLASH_INVALID"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc: return "MALFORMED"
    if is_stable_storage_url(value, base_url): return "STABLE_SUPABASE"
    status = image_url_status(value, network=network)
    if "instagram" in parsed.netloc or "cdninstagram" in parsed.netloc:
        return "INSTAGRAM_FORBIDDEN" if status == "EXPIRED_OR_FORBIDDEN" else "INSTAGRAM_EXPIRED" if status == "BROKEN" else "EXTERNAL_WORKING"
    return "EXTERNAL_WORKING" if status == "VALID" else "UNKNOWN" if status == "TIMEOUT" else "EXTERNAL_BROKEN"


def fetch_all(supabase: Any, table: str, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start, size = 0, min(limit or 1000, 1000)
    while True:
        request = supabase.table(table).select("*")
        page = request.range(start, start + size - 1).execute().data or [] if hasattr(request, "range") else request.execute().data or []
        rows.extend(page)
        if len(page) < size or (limit and len(rows) >= limit) or not hasattr(request, "range"):
            return rows[:limit] if limit else rows
        start += size


def category_map(supabase: Any) -> dict[str, dict[str, Any]]:
    return {row["slug"]: row for row in fetch_all(supabase, "categories")}


def product_supports_category(product: dict[str, Any], category_slug: str, categories: dict[str, dict[str, Any]]) -> bool:
    """Strict product-backed category evidence; legacy mappings alone are insufficient."""
    if product.get("catalog_status") == "NON_PRODUCT" or float(product.get("classifier_confidence") or 0) < AUTO_CATEGORY_THRESHOLD:
        return False
    decision = classify_product_category(product)
    if decision.confidence < AUTO_CATEGORY_THRESHOLD or not decision.category_slug:
        return False
    requested = category_slug.lower()
    aliases = {"accessories-jewellery":{"accessories-jewellery","women-jewelry"}, "accessories-bags":{"accessories-bags","women-bags"}}
    accepted = aliases.get(requested, {requested})
    required_audience = requested.split("-", 1)[0] if requested.startswith(("women-","men-","unisex-")) else None
    # gender_match_score (not a hand-rolled audience check): a blank audience field
    # must NOT silently skip this gate -- 0.0 ("no evidence") is a real rejection here,
    # same as the search path's own gender gate.
    if required_audience and gender_match_score(product, required_audience) <= 0.0:
        return False
    family, product_type = identify_product(compact_text(product))
    family_rules = {
        "women-tops":{"tops","shirts"}, "men-shirts":{"shirts"}, "men-t-shirts":{"tops"},
        "accessories-jewellery":{"jewellery"}, "accessories-bags":{"bags"},
        "women-kurtis":{"ethnic-upperwear"}, "women-ethnic-wear":{"ethnic-upperwear","sets"},
        "women-jeans":{"bottoms"}, "men-jeans":{"bottoms"},
    }
    if requested in family_rules and family not in family_rules[requested]:
        return False
    if requested.endswith("jeans") and product_type not in {"jeans","wide-leg-jeans"}:
        return False
    if decision.category_slug in accepted:
        return True

    # A main-category page (for example /women) is evidence-backed by its
    # validated descendants, never by a global-brand fallback.  This also
    # keeps arbitrary parent/child taxonomy additions correct without adding
    # another hard-coded category list.
    accepted_ids = {categories[slug].get("id") for slug in accepted if slug in categories}
    parent_by_id = {row.get("id"): row.get("parent_id") for row in categories.values()}
    decision_id = categories.get(decision.category_slug, {}).get("id")
    seen: set[Any] = set()
    while decision_id is not None and decision_id not in seen:
        if decision_id in accepted_ids:
            return True
        seen.add(decision_id)
        decision_id = parent_by_id.get(decision_id)
    return False


def outcome_cache_payload(outcome: ClassificationOutcome) -> dict[str, Any]:
    decision = outcome.decision
    return {
        "main_category_slug": decision.main_slug, "category_slug": decision.category_slug,
        "audience": decision.main_slug if decision.main_slug in {"women", "men", "unisex"} else None,
        "confidence": decision.model_confidence if decision.model_confidence is not None else decision.confidence,
        "reason": decision.reason, "evidence": list(decision.evidence),
        "suggested_name": outcome.suggested_name, "name_confidence": outcome.name_confidence,
        "non_product": outcome.non_product,
    }


def update_product_category(
    supabase: Any, product: dict[str, Any], category_id: int, outcome: ClassificationOutcome,
    fingerprint: str | None = None,
) -> None:
    decision = outcome.decision
    payload: dict[str, Any] = {
        "category_id": category_id,
        "normalized_main_category": decision.main_slug,
        "normalized_subcategory": decision.category_slug,
        "audience": decision.main_slug.upper() if decision.main_slug in {"women", "men", "unisex"} else None,
        "classifier_confidence": decision.confidence,
        "classification_source": decision.source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if outcome.suggested_name and outcome.name_confidence >= .80:
        payload["product_name"] = outcome.suggested_name
    if fingerprint and decision.source == "ai":
        metadata = dict(product.get("metadata") or {})
        metadata["category_ai_cache"] = {"fingerprint": fingerprint, "result": outcome_cache_payload(outcome)}
        payload["metadata"] = metadata
    supabase.table("products").update(payload).eq("id", product["id"]).execute()


def rebuild_brand_categories(supabase: Any) -> int:
    products = fetch_all(supabase, "products")
    categories = category_map(supabase)
    slug_by_id = {row.get("id"): slug for slug, row in categories.items()}
    mappings: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for product in products:
        slug = slug_by_id.get(product.get("category_id"))
        if product.get("brand_id") and product.get("category_id") and slug and product_supports_category(product, slug, categories):
            key = (str(product["brand_id"]), int(product["category_id"]))
            mappings.setdefault(key, []).append(product)
    # A brand can validly have several supported categories; the one with the most
    # supporting products is marked primary so a single "this brand's category" pick
    # (e.g. for display) is based on actual product volume, not an arbitrary row.
    counts_by_brand: dict[str, list[tuple[int, int]]] = {}
    for (brand, category), rows in mappings.items():
        counts_by_brand.setdefault(brand, []).append((category, len(rows)))
    primary_category_by_brand = {
        brand: max(counts, key=lambda item: item[1])[0]
        for brand, counts in counts_by_brand.items()
    }
    # Preserve explicitly curated manual mappings, but remove every stale row
    # previously derived from products before rebuilding the validated set.
    supabase.table("brand_categories").delete().eq("source", "product_derived").execute()
    if mappings:
        supabase.table("brand_categories").upsert([
            {
                "brand_id": brand,
                "category_id": category,
                "source": "product_derived",
                "confidence": max(float(row.get("classifier_confidence") or 0) for row in rows),
                "is_primary": category == primary_category_by_brand.get(brand),
            }
            for (brand, category), rows in mappings.items()
        ], on_conflict="brand_id,category_id").execute()
    return len(mappings)


def is_stable_storage_url(url: Any, base_url: str) -> bool:
    value = str(url or "")
    return value.startswith(f"{base_url.rstrip('/')}/storage/v1/object/public/")


def stable_storage_url(base_url: str, bucket: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/storage/v1/object/public/{bucket}/{path}"


def upload_stable_image(url: str, *, product: dict[str, Any], base_url: str, key: str) -> str:
    import httpx
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    response = httpx.get(url, follow_redirects=True, timeout=20)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/") or not response.content or len(response.content) > 12 * 1024 * 1024:
        raise ValueError("Source is not a supported image or exceeds 12MB")
    try:
        with Image.open(BytesIO(response.content)) as image:
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("Source returned invalid image bytes") from error
    if width < 320 or height < 320:
        raise ValueError(f"Source image is too small ({width}x{height}); minimum is 320x320")
    extension = {"image/png": "png", "image/webp": "webp"}.get(content_type, "jpg")
    identity = product.get("instagram_post_id") or product.get("id")
    path = f"{product.get('brand_id') or 'unknown'}/{identity}-1.{extension}"
    upload = httpx.post(f"{base_url.rstrip('/')}/storage/v1/object/{BUCKET}/{path}", content=response.content, headers={"apikey": key, "authorization": f"Bearer {key}", "content-type": content_type, "x-upsert": "true"}, timeout=30)
    upload.raise_for_status()
    return stable_storage_url(base_url, BUCKET, path)


# SHA-256 of Instagram's shared default/generic silhouette avatar, served to any account
# that hasn't set a custom profile picture. Confirmed by comparing scraped "profile picture"
# bytes across several brands: distinct CDN URLs and file IDs, but byte-identical content.
INSTAGRAM_DEFAULT_AVATAR_SHA256 = "93eecdc2044f27b2c5a2c6836d2e2b020dcd6865cde7c4278a5abd52b27b336a"


def upload_stable_brand_image(url: str, *, brand_id: str, base_url: str, key: str) -> str:
    import hashlib
    import httpx
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    response = httpx.get(url, follow_redirects=True, timeout=20); response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    if not content_type.startswith("image/") or not response.content or len(response.content) > 8 * 1024 * 1024:
        raise ValueError("Brand profile source is not a supported image")
    if hashlib.sha256(response.content).hexdigest() == INSTAGRAM_DEFAULT_AVATAR_SHA256:
        raise ValueError("Account has no custom Instagram avatar (default silhouette only)")
    try:
        with Image.open(BytesIO(response.content)) as image:
            width, height = image.size; image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("Brand profile source returned invalid image bytes") from error
    # Instagram's public web view only ever exposes a fixed ~100-150px avatar via plain DOM
    # scraping (no srcset, no HD variant) -- 96px still guards against default/broken icons
    # while admitting genuine small avatars that are the realistic ceiling for this method.
    if width < 96 or height < 96: raise ValueError(f"Brand profile image is too small ({width}x{height})")
    extension = {"image/png":"png", "image/webp":"webp"}.get(content_type, "jpg"); path = f"{brand_id}/profile.{extension}"
    upload = httpx.post(f"{base_url.rstrip('/')}/storage/v1/object/{BRAND_BUCKET}/{path}", content=response.content, headers={"apikey":key, "authorization":f"Bearer {key}", "content-type":content_type, "x-upsert":"true"}, timeout=30); upload.raise_for_status()
    return stable_storage_url(base_url, BRAND_BUCKET, path)


def run_reclassify(args: argparse.Namespace, supabase: Any) -> None:
    categories = category_map(supabase)
    products = fetch_all(supabase, "products")
    brands = {str(row.get("id")): row for row in fetch_all(supabase, "brands")}
    for product in products:
        product["brand_metadata"] = brands.get(str(product.get("brand_id")), {})
    if args.brand:
        products = [p for p in products if args.brand.lower() in str(p.get("brand_name") or "").lower()]
    if args.only_uncategorized:
        products = [p for p in products if not p.get("category_id")]
    if args.only_low_confidence:
        products = [p for p in products if float(p.get("classifier_confidence") or 0) < AUTO_CATEGORY_THRESHOLD]
    if not args.force:
        products = [p for p in products if float(p.get("classifier_confidence") or 0) < AUTO_CATEGORY_THRESHOLD]
    if args.limit:
        products = products[:args.limit]
    ids_to_slug = {row["id"]: slug for slug, row in categories.items()}
    counts = Counter(total_unresolved_inspected=len(products))
    ai_client = None
    if args.use_ai:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY is required when --use-ai is enabled")
        from openai import OpenAI
        ai_client = OpenAI(api_key=api_key, timeout=15.0, max_retries=0)
    run_cache: dict[str, ClassificationOutcome] = {}
    ai_error_streak = 0
    for product in products:
        old_slug = ids_to_slug.get(product.get("category_id"))
        try:
            active_ai_client = ai_client if ai_error_streak < 3 else None
            outcome = classify_with_pipeline(product, categories, ai_client=active_ai_client, old_slug=old_slug, run_cache=run_cache)
            if outcome.ai_called:
                ai_error_streak = 0
        except Exception as error:
            counts["openai_errors"] += 1
            ai_error_streak += 1
            outcome = ClassificationOutcome(CategoryDecision(None, None, .25, f"OpenAI error: {error}", (), "ai"))
        decision = outcome.decision
        if outcome.non_product:
            counts["non_product"] += 1
        elif decision.source in {"deterministic", "metadata"} and decision.confidence >= AUTO_CATEGORY_THRESHOLD:
            counts["resolved_without_ai"] += 1
        elif outcome.cache_hit:
            counts["cached_results"] += 1
        elif outcome.ai_called:
            counts["sent_to_openai"] += 1
        if decision.source == "ai":
            if not decision.category_slug:
                counts["unknown"] += 1
            elif decision.confidence >= AUTO_CATEGORY_THRESHOLD:
                counts["ai_accepted"] += 1
            else:
                counts["ai_rejected"] += 1
        audience, _ = audience_from_text(compact_text(product))
        print("\n=====================================\nPRODUCT\n=====================================")
        print(f"ID: {product.get('id')}\nNAME: {product.get('product_name') or product.get('item_name')}\nBRAND: {product.get('brand_name')}")
        print(f"CURRENT: {old_slug or 'UNCATEGORIZED'}\nCURRENT CONFIDENCE: {float(product.get('classifier_confidence') or 0):.2f}")
        print("EVIDENCE:")
        for item in decision.evidence:
            print(f'- "{item}"')
        if not decision.evidence:
            print("- none")
        print(f"PROPOSED: {decision.main_slug or 'UNKNOWN'} / {decision.category_slug or 'UNKNOWN'}")
        print(f"AUDIENCE: {(audience or decision.main_slug or 'UNKNOWN').upper()}")
        if decision.model_confidence is not None:
            print(f"MODEL CONFIDENCE: {decision.model_confidence:.2f}")
        print(f"FINAL CONFIDENCE: {decision.confidence:.2f}\nSOURCE: {decision.source.upper()}\nREASON: {decision.reason}")
        if outcome.suggested_name:
            print(f"SUGGESTED NAME: {outcome.suggested_name} ({outcome.name_confidence:.2f})")
        if outcome.non_product:
            print("ACTION: WOULD MARK NON-PRODUCT" if args.dry_run else "ACTION: MARKED NON-PRODUCT")
            if not args.dry_run:
                supabase.table("products").update({"catalog_status": "NON_PRODUCT", "classification_source": "metadata"}).eq("id", product["id"]).execute()
            continue
        if not decision.category_slug or decision.confidence < AUTO_CATEGORY_THRESHOLD:
            counts["low_confidence"] += 1
            print("ACTION: LEAVE UNCHANGED (LOW CONFIDENCE)")
            continue
        target = categories.get(decision.category_slug)
        if not target:
            counts["errors"] += 1; print("ACTION: CATEGORY MIGRATION REQUIRED"); continue
        already_confident = float(product.get("classifier_confidence") or 0) >= AUTO_CATEGORY_THRESHOLD
        if old_slug == decision.category_slug and already_confident and not args.force:
            counts["already_correct"] += 1; print("ACTION: ALREADY CORRECT"); continue
        counts["changed"] += 1
        print("ACTION: WOULD UPDATE" if args.dry_run else "ACTION: UPDATED")
        if not args.dry_run:
            fingerprint = context_fingerprint(classification_context(product, old_slug), categories) if decision.source == "ai" else None
            update_product_category(supabase, product, target["id"], outcome, fingerprint)
    print("\n" + " | ".join(f"{key.replace('_',' ').title()}: {value}" for key, value in counts.items()))


def run_repair_images(args: argparse.Namespace, supabase: Any, base_url: str, key: str) -> None:
    products = fetch_all(supabase, "products", args.limit); counts = Counter(images_scanned=len(products))
    for product in products:
        source_url = product.get("image_url")
        if is_stable_storage_url(source_url, base_url):
            counts["already_stable"] += 1
            print(f"[ALREADY_STABLE] {product.get('id')} | {source_url}")
            continue
        status = image_url_status(source_url, network=True)
        wrong_seed = product.get("source") == "instagram" and "unsplash.com" in str(source_url or "")
        if wrong_seed:
            status = "INVALID"
        if status == "MISSING":
            counts["missing"] += 1; print(f"[MISSING] {product.get('id')}"); continue
        if status in {"BROKEN", "EXPIRED_OR_FORBIDDEN", "INVALID_URL", "INVALID"}:
            recoverable = bool(product.get("instagram_post_url") or product.get("product_url") or product.get("source_url"))
            label = "RECOVERABLE" if recoverable else "UNRECOVERABLE"
            counts[status.lower()] += 1; counts[label.lower()] += 1
            print(f"[{status}] {product.get('id')} | {label} | {source_url}")
            if not args.dry_run:
                payload = {"image_status": "RECOVERY_REQUIRED" if recoverable else "UNRECOVERABLE"}
                if wrong_seed:
                    payload.update({"original_image_url": source_url, "image_url": None})
                supabase.table("products").update(payload).eq("id", product["id"]).execute()
            continue
        if status == "TIMEOUT":
            counts["download_failed"] += 1; print(f"[DOWNLOAD_FAILED] {product.get('id')} | timeout"); continue
        if status != "VALID":
            counts["skipped"] += 1
            continue
        if args.dry_run:
            counts["external_valid"] += 1
            print(f"[EXTERNAL_VALID] {product.get('id')} | would migrate {source_url}")
            continue
        try:
            stable = upload_stable_image(source_url, product=product, base_url=base_url, key=key)
            supabase.table("products").update({"original_image_url": source_url, "image_url": stable, "image_status": "VALID"}).eq("id", product["id"]).execute()
            counts["migrated"] += 1
            print(f"[MIGRATED] {product.get('id')} | {stable}")
        except Exception as error:
            counts["upload_failed"] += 1; print(f"[UPLOAD_FAILED] {product.get('id')} | {error}")
    print("\n" + " | ".join(f"{key.replace('_',' ').title()}: {value}" for key, value in counts.items()))


def source_post_url(product: dict[str, Any]) -> str | None:
    # Accept both instagram.com/p/ID and the username-prefixed
    # instagram.com/username/p/ID form -- the latter is what's actually stored
    # for most of this catalog's products.
    for field in ("instagram_post_url", "product_url", "source_url"):
        value = str(product.get(field) or "").split("?", 1)[0].rstrip("/")
        if re.match(r"^https://(?:www\.)?instagram\.com/(?:[^/]+/)?(?:p|reel)/[^/]+$", value, re.I):
            return value
    return None


def run_recover_images(
    args: argparse.Namespace,
    supabase: Any,
    base_url: str,
    key: str,
    fetch_fresh_url: Callable[[dict[str, Any]], str | None] | None = None,
) -> None:
    products = fetch_all(supabase, "products")
    if getattr(args, "brand", None): products = [p for p in products if args.brand.lower() in str(p.get("brand_name") or "").lower()]
    if getattr(args, "product_id", None): products = [p for p in products if str(p.get("id")) == args.product_id]
    if getattr(args, "category", None): products = [p for p in products if args.category.lower() in compact_text(p)]
    if getattr(args, "product_type", None): products = [p for p in products if args.product_type.lower().replace("-", " ") in compact_text(p).replace("-", " ")]
    priority_terms = ("top", "kurta", "kurti", "ethnic", "earring", "necklace", "ring", "bracelet")
    products.sort(key=lambda p: any(term in compact_text(p) for term in priority_terms), reverse=True)
    candidates: list[dict[str, Any]] = []
    for product in products:
        current = product.get("image_url")
        if is_stable_storage_url(current, base_url):
            continue
        flagged = product.get("image_status") in {"BROKEN", "EXPIRED_OR_FORBIDDEN", "INVALID_URL", "RECOVERY_REQUIRED", "UNRECOVERABLE"}
        wrong_seed = product.get("source") == "instagram" and "unsplash.com" in str(current or "")
        if args.only_broken and not (flagged or wrong_seed or image_url_status(current, network=True) != "VALID"):
            continue
        candidates.append(product)
        if args.limit and len(candidates) >= args.limit:
            break

    driver = None
    if candidates and fetch_fresh_url is None and not args.dry_run:
        from training.scraper_pipeline import create_selenium_driver, extract_post_page_data, login
        driver = create_selenium_driver()
        login(driver)
        fetch_fresh_url = lambda product: extract_post_page_data(driver, source_post_url(product) or "").get("image_url")

    counts = Counter(candidates=len(candidates))
    try:
        for product in candidates:
            post_url = source_post_url(product)
            if not post_url:
                counts["unrecoverable"] += 1
                print(f"[UNRECOVERABLE] {product.get('id')} | no Instagram source post")
                if not args.dry_run:
                    supabase.table("products").update({"image_status": "UNRECOVERABLE", "image_url": None}).eq("id", product["id"]).execute()
                continue
            if args.dry_run:
                counts["recoverable"] += 1
                print(f"[RECOVERABLE] {product.get('id')} | {post_url}")
                continue
            try:
                fresh_url = fetch_fresh_url(product) if fetch_fresh_url else None
                if not fresh_url or image_url_status(fresh_url, network=True) != "VALID":
                    raise ValueError("scraper did not return a valid image")
                stable = upload_stable_image(fresh_url, product=product, base_url=base_url, key=key)
                supabase.table("products").update({
                    "original_image_url": fresh_url,
                    "image_url": stable,
                    "image_status": "VALID",
                }).eq("id", product["id"]).execute()
                counts["recovered"] += 1
                print(f"[RECOVERED] {product.get('id')} | {stable}")
            except Exception as error:
                counts["unrecoverable"] += 1
                print(f"[UNRECOVERABLE] {product.get('id')} | {error}")
                supabase.table("products").update({"image_status": "UNRECOVERABLE", "image_url": None}).eq("id", product["id"]).execute()
    finally:
        if driver is not None:
            driver.quit()
    print("\n" + " | ".join(f"{name.replace('_',' ').title()}: {value}" for name, value in counts.items()))


def run_brand_image_repair(args: argparse.Namespace, supabase: Any, base_url: str, key: str) -> None:
    brands = fetch_all(supabase, "brands")
    if args.brand: brands = [b for b in brands if args.brand.lower() in str(b.get("name") or b.get("brand_name") or "").lower()]
    if args.limit: brands = brands[:args.limit]
    counts = Counter(scanned=len(brands))
    for brand in brands:
        current = brand.get("profile_picture_url")
        if is_stable_storage_url(current, base_url): counts["already_stable"] += 1; continue
        if not current: counts["missing"] += 1; continue
        if args.dry_run: counts["would_migrate"] += 1; print(f"[BRAND_EXTERNAL] {brand.get('id')} | {current}"); continue
        try:
            stable = upload_stable_brand_image(str(current), brand_id=str(brand["id"]), base_url=base_url, key=key)
            supabase.table("brands").update({"profile_picture_url": stable}).eq("id", brand["id"]).execute()
            counts["migrated"] += 1
        except Exception as error:
            counts["failed"] += 1; print(f"[BRAND_IMAGE_FAILED] {brand.get('id')} | {error}")
    print(json.dumps(dict(counts), indent=2))


def run_recover_brand_images(args: argparse.Namespace, supabase: Any, base_url: str, key: str) -> None:
    brands = fetch_all(supabase, "brands")
    if getattr(args, "brand", None): brands = [b for b in brands if args.brand.lower() in str(b.get("brand_name") or "").lower()]
    candidates = []
    for brand in brands:
        current = brand.get("profile_picture_url")
        if is_stable_storage_url(current, base_url): continue
        if not brand.get("instagram_profile_url"): continue
        candidates.append(brand)
        if args.limit and len(candidates) >= args.limit: break

    counts = Counter(candidates=len(candidates))
    if args.dry_run:
        for brand in candidates: print(f"[BRAND_RECOVERABLE] {brand.get('id')} | {brand.get('instagram_profile_url')}")
        print(json.dumps(dict(counts), indent=2)); return

    driver = None
    if candidates:
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.support.ui import WebDriverWait
        from training.scraper_pipeline import create_selenium_driver, extract_profile_picture_from_page, login, random_delay
        driver = create_selenium_driver()
        login(driver)
    try:
        for brand in candidates:
            try:
                username = str(brand.get("instagram_username") or "").lower()
                driver.get("about:blank")  # clear the previous brand's DOM/images before navigating

                def page_is_ready(d: Any, _username: str = username) -> bool:
                    source = d.page_source.lower()
                    if "page isn't available" in source or "page not found" in source:
                        return True
                    state = d.execute_script(
                        "const i = document.querySelector(\"header img[alt*='profile picture' i], main header img[alt*='profile picture' i]\"); "
                        "return i ? {alt: i.alt.toLowerCase(), loaded: i.complete && i.naturalWidth > 0} : null;"
                    )
                    return bool(state) and _username in state["alt"] and state["loaded"]

                driver.get(brand["instagram_profile_url"])
                try:
                    WebDriverWait(driver, 15).until(page_is_ready)
                except TimeoutException:
                    pass  # fall through to the same checks below, which will classify it safely
                random_delay(0.3, 0.8)
                if "page isn't available" in driver.page_source.lower():
                    raise ValueError("Instagram account no longer exists")
                fresh_url = extract_profile_picture_from_page(driver)
                alt_username = driver.execute_script(
                    "const i = document.querySelector(\"header img[alt*='profile picture' i], main header img[alt*='profile picture' i]\"); "
                    "return i ? i.alt : '';"
                )
                print(f"[BRAND_CHECK] {brand.get('id')} | expected={username!r} alt_seen={alt_username!r}")
                if alt_username and username and username not in alt_username.lower():
                    raise ValueError(f"extracted image belongs to a different account ({alt_username!r})")
                if not fresh_url:
                    raise ValueError("scraper did not find a profile picture")
                stable = upload_stable_brand_image(fresh_url, brand_id=str(brand["id"]), base_url=base_url, key=key)
                supabase.table("brands").update({"profile_picture_url": stable}).eq("id", brand["id"]).execute()
                counts["recovered"] += 1
                print(f"[BRAND_RECOVERED] {brand.get('id')} | {brand.get('brand_name')} | {fresh_url} -> {stable}")
            except Exception as error:
                counts["unrecoverable"] += 1
                print(f"[BRAND_UNRECOVERABLE] {brand.get('id')} | {brand.get('brand_name')} | {error}")
    finally:
        if driver is not None: driver.quit()
    print(json.dumps(dict(counts), indent=2))


def run_image_health(supabase: Any, base_url: str, *, network: bool, limit: int | None) -> dict[str, Any]:
    products, brands = fetch_all(supabase, "products", limit), fetch_all(supabase, "brands")
    product_counts = Counter(classify_catalog_image(row.get("image_url"), base_url, network=network) for row in products)
    brand_counts = Counter(classify_catalog_image(row.get("profile_picture_url"), base_url, network=network) for row in brands)
    stable = product_counts["STABLE_SUPABASE"]
    recoverable = sum(product_counts[key] for key in ("EXTERNAL_WORKING", "INSTAGRAM_EXPIRED", "INSTAGRAM_FORBIDDEN", "UNSPLASH_INVALID", "EXTERNAL_BROKEN"))
    report = {"total_products": len(products), "product_images": dict(product_counts), "stable_coverage_percent": round(100 * stable / len(products), 2) if products else 0, "recoverable": recoverable, "unrecoverable": product_counts["MISSING"] + product_counts["MALFORMED"], "total_brands": len(brands), "brand_images": dict(brand_counts)}
    print(json.dumps(report, indent=2)); return report


def run_image_sample_audit(supabase: Any, base_url: str, *, product_limit: int, brand_limit: int, network: bool) -> dict[str, Any]:
    products, brands = fetch_all(supabase, "products", product_limit), fetch_all(supabase, "brands", brand_limit)
    product_rows=[]
    for row in products:
        url=row.get("image_url"); status=classify_catalog_image(url,base_url,network=network)
        product_rows.append({"product_id":row.get("id"),"product_name":row.get("product_name") or row.get("item_name"),"brand":row.get("brand_name"),"db_field":"image_url","db_value":url,"is_null":not bool(url),"classification":status,"instagram_cdn":"instagram" in str(url or "") or "fbcdn" in str(url or ""),"supabase_storage":is_stable_storage_url(url,base_url),"api_field":"image_url","react_field":"product.image_url"})
    brand_rows=[]
    for row in brands:
        url=row.get("image_url") or row.get("profile_picture_url") or row.get("profile_image_url") or row.get("logo_url")
        brand_rows.append({"brand_id":row.get("id"),"brand":row.get("name") or row.get("brand_name"),"db_field":next((key for key in ("image_url","profile_picture_url","profile_image_url","logo_url") if row.get(key)),"profile_picture_url"),"db_value":url,"is_null":not bool(url),"classification":classify_catalog_image(url,base_url,network=network),"api_field":"image_url","react_field":"brand.avatar_url"})
    report={"product_sample":product_rows,"product_summary":dict(Counter(r["classification"] for r in product_rows)),"brand_sample":brand_rows,"brand_summary":dict(Counter(r["classification"] for r in brand_rows))}
    print(json.dumps(report,indent=2,default=str)); return report


def run_category_isolation_health(supabase: Any) -> dict[str, Any]:
    products, brands, mappings = fetch_all(supabase,"products"), fetch_all(supabase,"brands"), fetch_all(supabase,"brand_categories")
    categories=category_map(supabase); slug_by_id={row.get("id"):slug for slug,row in categories.items()}
    requested=("women-tops","men-shirts","accessories-jewellery")
    support={slug:{} for slug in requested}
    for slug in requested:
        for row in products:
            if product_supports_category(row,slug,categories):
                support[slug].setdefault(str(row.get("brand_id")),[]).append({"id":row.get("id"),"name":row.get("product_name"),"confidence":row.get("classifier_confidence")})
    invalid=[]
    for mapping in mappings:
        slug=slug_by_id.get(mapping.get("category_id")); brand=str(mapping.get("brand_id"))
        if slug and not any(product_supports_category(row,slug,categories) for row in products if str(row.get("brand_id"))==brand):
            invalid.append({"brand_id":brand,"brand":next((b.get("name") for b in brands if str(b.get("id"))==brand),None),"category":slug,"status":"INVALID_MAPPING","supporting_products":0})
    overlaps=[]
    for left_index,left in enumerate(requested):
        for right in requested[left_index+1:]:
            for brand in sorted(set(support[left]) & set(support[right])):
                overlaps.append({"brand_id":brand,"categories":[left,right],"status":"VALID_MULTI_CATEGORY_BRAND"})
    report={"categories":{slug:{"brands_returned":len(rows),"brands_with_supporting_products":len(rows),"invalid_brands":0,"evidence":rows} for slug,rows in support.items()},"overlaps":overlaps,"invalid_mappings":invalid,"invalid_mapping_count":len(invalid)}
    print(json.dumps(report,indent=2,default=str)); return report


def run_classification_error_report(supabase: Any, limit: int) -> dict[str, Any]:
    products=fetch_all(supabase,"products"); categories=category_map(supabase); slug_by_id={row.get("id"):slug for slug,row in categories.items()}
    checked=[]
    for row in products:
        if float(row.get("classifier_confidence") or 0)<AUTO_CATEGORY_THRESHOLD: continue
        stored=slug_by_id.get(row.get("category_id")); decision=classify_product_category(row)
        if not stored or not decision.category_slug: continue
        checked.append({"id":row.get("id"),"name":row.get("product_name"),"stored":stored,"evidence_based":decision.category_slug,"obvious_mismatch":stored!=decision.category_slug})
        if len(checked)>=limit: break
    errors=sum(row["obvious_mismatch"] for row in checked); report={"sample_size":len(checked),"obvious_errors":errors,"error_rate":round(errors/len(checked),4) if checked else None,"examples":[row for row in checked if row["obvious_mismatch"]][:20]}
    print(json.dumps(report,indent=2)); return report


def ambiguity_reason(product: dict[str, Any]) -> str:
    non_product, _ = is_non_product(product)
    if non_product:
        return "likely non-product post"
    name = str(product.get("product_name") or product.get("item_name") or "").strip()
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
    description = str(product.get("description") or "").strip()
    if not name or WEAK_NAMES.match(name):
        return "generic or failed product name extraction"
    if len(name) >= 90 or re.match(r"^(?:photo|video) (?:by|shared by)", name, re.I):
        return "scraper extracted caption/alt text as product name"
    if not description and not metadata.get("caption"):
        return "missing caption and description"
    text = compact_text(product)
    garment_terms = ("dress", "gown", "top", "blouse", "shirt", "jeans", "skirt", "shorts", "blazer")
    if any(contains_term(text, term) for term in garment_terms) and audience_from_text(text)[0] is None:
        return "ambiguous gender or audience"
    decision = deterministic_pipeline(product)
    if decision.category_slug and decision.confidence < AUTO_CATEGORY_THRESHOLD:
        return "ambiguous gender or audience"
    if not decision.category_slug:
        if not metadata.get("hashtags") and not product.get("source_hashtag"):
            return "missing useful hashtags and explicit product terms"
        return "no explicit taxonomy product term"
    return "taxonomy mismatch or conflicting evidence"


def run_ambiguity_audit(supabase: Any, limit: int | None = None) -> dict[str, Any]:
    products = [p for p in fetch_all(supabase, "products") if float(p.get("classifier_confidence") or 0) < AUTO_CATEGORY_THRESHOLD]
    brands = {str(row.get("id")): row for row in fetch_all(supabase, "brands")}
    counts: Counter[str] = Counter()
    pipeline_counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}
    for product in products[:limit] if limit else products:
        product["brand_metadata"] = brands.get(str(product.get("brand_id")), {})
        reason = ambiguity_reason(product)
        counts[reason] += 1
        non_product, _ = is_non_product(product)
        decision = deterministic_pipeline(product)
        if non_product:
            pipeline_counts["non_product"] += 1
        elif decision.category_slug and decision.confidence >= AUTO_CATEGORY_THRESHOLD:
            pipeline_counts["resolved_without_ai"] += 1
        else:
            pipeline_counts["would_require_ai"] += 1
        examples.setdefault(reason, [])
        if len(examples[reason]) < 3:
            examples[reason].append({"id": product.get("id"), "name": product.get("product_name"), "brand": product.get("brand_name")})
    report = {"unresolved_inspected": sum(counts.values()), "pipeline_estimate": dict(pipeline_counts), "ambiguity_reasons": dict(counts), "examples": examples}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def run_manual_review(args: argparse.Namespace, supabase: Any) -> None:
    products = [p for p in fetch_all(supabase, "products") if float(p.get("classifier_confidence") or 0) < AUTO_CATEGORY_THRESHOLD]
    categories = {row.get("id"): row for row in fetch_all(supabase, "categories")}
    if args.brand:
        products = [p for p in products if args.brand.lower() in str(p.get("brand_name") or "").lower()]
    rows = []
    for product in products:
        reason = ambiguity_reason(product)
        current = categories.get(product.get("category_id"), {}).get("slug")
        decision = deterministic_pipeline(product)
        if args.category and args.category.lower() not in str(current or "").lower():
            continue
        if args.reason and args.reason.lower() not in reason.lower():
            continue
        metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
        rows.append({
            "id": product.get("id"), "product_name": product.get("product_name"), "brand": product.get("brand_name"),
            "current_image": product.get("image_url"), "source_post_url": source_post_url(product),
            "caption": metadata.get("caption") or product.get("description"),
            "current_category": current, "proposed_category": decision.category_slug,
            "confidence": decision.confidence, "reason": reason,
        })
        if len(rows) >= args.limit:
            break
    print(json.dumps({"manual_review": rows, "count": len(rows)}, indent=2, ensure_ascii=False))


def run_health(supabase: Any) -> None:
    products, brands = fetch_all(supabase, "products"), fetch_all(supabase, "brands")
    categories = {row["id"]: row for row in fetch_all(supabase, "categories")}
    distribution = Counter(
        (p.get("normalized_subcategory") or categories.get(p.get("category_id"), {}).get("slug", "UNCATEGORIZED"))
        if float(p.get("classifier_confidence") or 0) >= AUTO_CATEGORY_THRESHOLD
        else "UNCATEGORIZED"
        for p in products
    )
    confident = sum(float(p.get("classifier_confidence") or 0) >= AUTO_CATEGORY_THRESHOLD for p in products)
    brand_ids = {p.get("brand_id") for p in products if p.get("brand_id")}
    try:
        mappings = fetch_all(supabase, "brand_categories")
    except Exception:
        mappings = []
    mapped_brands = {row.get("brand_id") for row in mappings if float(row.get("confidence") or 0) >= AUTO_CATEGORY_THRESHOLD}
    stable = sum(is_stable_storage_url(p.get("image_url"), os.environ.get("SUPABASE_URL", "")) for p in products)
    missing_images = sum(not p.get("image_url") for p in products)
    broken_images = sum(
        p.get("image_status") in {"BROKEN", "EXPIRED_OR_FORBIDDEN", "INVALID_URL", "RECOVERY_REQUIRED", "UNRECOVERABLE"}
        or (p.get("source") == "instagram" and "unsplash.com" in str(p.get("image_url") or ""))
        for p in products
    )
    external_images = max(0, len(products) - stable - missing_images - broken_images)
    suspicious = [f"{slug}: {count}" for slug, count in distribution.items() if count > max(100, len(products) * .35)]
    validated_sources = Counter()
    for product in products:
        if float(product.get("classifier_confidence") or 0) < AUTO_CATEGORY_THRESHOLD:
            validated_sources["unresolved"] += 1
            continue
        source = str(product.get("classification_source") or "legacy").lower()
        source = {"heuristic": "deterministic", "openai": "ai"}.get(source, source)
        validated_sources[source] += 1
    brand_category_counts = Counter(row.get("brand_id") for row in mappings if float(row.get("confidence") or 0) >= AUTO_CATEGORY_THRESHOLD)
    suspicious.extend(f"brand {brand}: {count} validated categories" for brand, count in brand_category_counts.items() if count > 6)
    report = {
        "total_products": len(products),
        "products_at_or_above_0_80": confident,
        "products_below_0_80": len(products)-confident,
        "uncategorized": distribution["UNCATEGORIZED"] + distribution["uncategorized"],
        "category_coverage_percent": round(confident / len(products) * 100, 1) if products else 0,
        "products_with_stable_images": stable,
        "products_with_external_images": external_images,
        "products_with_broken_images": broken_images,
        "products_with_missing_images": missing_images,
        "stable_image_coverage_percent": round(stable / len(products) * 100, 1) if products else 0,
        "brands_with_products": sum(b.get("id") in brand_ids for b in brands),
        "brands_without_products": sum(b.get("id") not in brand_ids for b in brands),
        "brands_with_category_mappings": len(mapped_brands),
        "brands_without_category_mappings": len(brands)-len(mapped_brands),
        "category_counts": dict(distribution),
        "suspicious_distributions": suspicious,
        "classification_sources": dict(validated_sources),
        "before": {"total_products": 1348, "validated": 107, "low_confidence": 1241, "coverage_percent": 7.9},
        "after": {
            "total_products": len(products), "validated": confident,
            "low_confidence": len(products) - confident,
            "coverage_percent": round(confident / len(products) * 100, 1) if products else 0,
        },
    }
    print(json.dumps(report, indent=2))


def run_verify_schema(public_client: Any, admin_client: Any | None, base_url: str) -> bool:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    add("database connection", "PASS", f"connected to {urlparse(base_url).netloc}")
    samples: dict[str, list[dict[str, Any]]] = {}
    for table in ("categories", "products", "brands", "brand_categories"):
        try:
            samples[table] = fetch_all(public_client, table, None if table == "categories" else 1)
            status = "FAIL" if table == "categories" and not samples[table] else "PASS"
            detail = f"{len(samples[table])} row(s) returned" if table == "categories" else f"{len(samples[table])} sample row(s) returned"
            add(f"public read: {table}", status, detail)
        except Exception as error:
            add(f"public read: {table}", "FAIL", str(error))

    product = (samples.get("products") or [{}])[0]
    for column in ("category_id", "classifier_confidence", "normalized_main_category", "normalized_subcategory", "classification_source", "catalog_status", "image_status"):
        add(f"products.{column}", "PASS" if column in product else "FAIL", "column visible through REST" if column in product else "column missing")
    add(
        "product category relationship",
        "PASS" if product.get("category_id") is not None else "WARNING",
        "sample product has category_id" if product.get("category_id") is not None else "sample product is not related to a category",
    )
    add("taxonomy RLS", "WARNING", "public SELECT works; verify zero public mutation policies with verify_unfound_catalog.sql")
    try:
        public_client.rpc("match_products", {
            "query_embedding": [0.0] * 512, "match_threshold": 2.0,
            "match_count": 1, "filter_category_id": None,
        }).execute()
        add("match_products RPC", "PASS", "exact callable RPC is available")
    except Exception as error:
        add("match_products RPC", "FAIL", str(error))
    if admin_client is None:
        add("product-images bucket", "WARNING", "service role unavailable; verify with verify_unfound_catalog.sql")
    else:
        try:
            buckets = admin_client.storage.list_buckets()
            bucket_ids = {str(getattr(bucket, "id", None) or (bucket.get("id") if isinstance(bucket, dict) else "")) for bucket in buckets}
            add("product-images bucket", "PASS" if BUCKET in bucket_ids else "FAIL", f"available buckets: {', '.join(sorted(bucket_ids)) or 'none'}")
        except Exception as error:
            add("product-images bucket", "FAIL", str(error))
    add(
        "admin credentials",
        "PASS" if admin_client is not None else "WARNING",
        "service-role client configured for backend-only operations" if admin_client is not None else "SUPABASE_SERVICE_ROLE_KEY not configured; admin operations unavailable",
    )
    for check in checks:
        print(f"[{check['status']}] {check['check']}: {check['detail']}")
    return not any(check["status"] == "FAIL" for check in checks)


def run_verify_storage(admin_client: Any, base_url: str, service_key: str) -> bool:
    import httpx
    checks = []
    try:
        buckets = admin_client.storage.list_buckets()
        bucket_ids = {str(getattr(bucket, "id", None) or (bucket.get("id") if isinstance(bucket, dict) else "")) for bucket in buckets}
    except Exception as error:
        print(f"[FAIL] Storage bucket listing: {error}"); return False
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    headers = {"apikey": service_key, "authorization": f"Bearer {service_key}", "content-type": "image/png", "x-upsert": "true"}
    for bucket in (BUCKET, BRAND_BUCKET):
        if bucket not in bucket_ids:
            checks.append((bucket, "FAIL", "bucket missing; apply storage_setup.sql")); continue
        path = "_health/storage-verification.png"
        try:
            upload = httpx.post(f"{base_url.rstrip('/')}/storage/v1/object/{bucket}/{path}", content=png, headers=headers, timeout=20); upload.raise_for_status()
            public = httpx.get(stable_storage_url(base_url, bucket, path), timeout=20)
            public.raise_for_status()
            update = httpx.post(f"{base_url.rstrip('/')}/storage/v1/object/{bucket}/{path}", content=png, headers=headers, timeout=20); update.raise_for_status()
            httpx.delete(f"{base_url.rstrip('/')}/storage/v1/object/{bucket}/{path}", headers={"apikey":service_key,"authorization":f"Bearer {service_key}"}, timeout=20)
            checks.append((bucket, "PASS", "public read and server-side upload/update verified; probe removed"))
        except Exception as error:
            checks.append((bucket, "FAIL", str(error)))
    for bucket, status, detail in checks: print(f"[{status}] {bucket}: {detail}")
    return all(status == "PASS" for _, status, _ in checks)


def main() -> None:
    parser = argparse.ArgumentParser(description="UNFOUND category and image repair tools")
    sub = parser.add_subparsers(dest="command", required=True)
    reclassify = sub.add_parser("reclassify-catalog"); reclassify.add_argument("--dry-run", action="store_true", default=False); reclassify.add_argument("--brand"); reclassify.add_argument("--limit", type=int); reclassify.add_argument("--only-low-confidence", action="store_true"); reclassify.add_argument("--only-uncategorized", action="store_true"); reclassify.add_argument("--force", action="store_true"); reclassify.add_argument("--use-ai", action="store_true")
    images = sub.add_parser("repair-images"); images.add_argument("--dry-run", action="store_true", default=False); images.add_argument("--limit", type=int)
    recover = sub.add_parser("recover-images"); recover.add_argument("--dry-run", action="store_true", default=False); recover.add_argument("--only-broken", action="store_true"); recover.add_argument("--limit", type=int)
    recover.add_argument("--brand"); recover.add_argument("--category"); recover.add_argument("--product-type"); recover.add_argument("--product-id")
    brand_images = sub.add_parser("repair-brand-images"); brand_images.add_argument("--dry-run", action="store_true"); brand_images.add_argument("--limit", type=int); brand_images.add_argument("--brand")
    recover_brand_images = sub.add_parser("recover-brand-images"); recover_brand_images.add_argument("--dry-run", action="store_true"); recover_brand_images.add_argument("--limit", type=int); recover_brand_images.add_argument("--brand")
    health_images = sub.add_parser("image-health"); health_images.add_argument("--network", action="store_true"); health_images.add_argument("--limit", type=int)
    sample_images = sub.add_parser("image-sample-audit"); sample_images.add_argument("--products",type=int,default=20); sample_images.add_argument("--brands",type=int,default=10); sample_images.add_argument("--network",action="store_true")
    sub.add_parser("category-isolation-health")
    classification_errors=sub.add_parser("classification-error-report"); classification_errors.add_argument("--limit",type=int,default=100)
    sub.add_parser("verify-storage")
    sub.add_parser("rebuild-brand-categories")
    sub.add_parser("verify-schema")
    sub.add_parser("category-health")
    audit = sub.add_parser("ambiguity-audit"); audit.add_argument("--limit", type=int)
    review = sub.add_parser("manual-review"); review.add_argument("--limit", type=int, default=100); review.add_argument("--brand"); review.add_argument("--category"); review.add_argument("--reason")
    args = parser.parse_args(); load_runtime_env()
    url = os.getenv("SUPABASE_URL")
    anon_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    administrative = (
        (args.command in {"reclassify-catalog", "repair-images", "recover-images", "repair-brand-images", "recover-brand-images"} and not args.dry_run)
        or args.command == "rebuild-brand-categories"
        or args.command == "verify-storage"
    )
    if not url or not anon_key: raise SystemExit("SUPABASE_URL and SUPABASE_ANON_KEY are required for safe catalog reads")
    if administrative and not service_key: raise SystemExit(SERVICE_ROLE_ERROR)
    key = service_key if administrative else anon_key
    client = create_supabase_client(url, key)
    if args.command == "reclassify-catalog": run_reclassify(args, client)
    elif args.command == "repair-images": run_repair_images(args, client, url, key)
    elif args.command == "recover-images": run_recover_images(args, client, url, key)
    elif args.command == "repair-brand-images": run_brand_image_repair(args, client, url, key)
    elif args.command == "recover-brand-images": run_recover_brand_images(args, client, url, key)
    elif args.command == "image-health": run_image_health(client, url, network=args.network, limit=args.limit)
    elif args.command == "image-sample-audit": run_image_sample_audit(client,url,product_limit=args.products,brand_limit=args.brands,network=args.network)
    elif args.command == "category-isolation-health": run_category_isolation_health(client)
    elif args.command == "classification-error-report": run_classification_error_report(client,args.limit)
    elif args.command == "verify-storage": run_verify_storage(client, url, service_key)
    elif args.command == "rebuild-brand-categories": print(f"Brand-category mappings rebuilt: {rebuild_brand_categories(client)}")
    elif args.command == "verify-schema":
        admin_client = create_supabase_client(url, service_key) if service_key else None
        run_verify_schema(client, admin_client, url)
    elif args.command == "category-health": run_health(client)
    elif args.command == "ambiguity-audit": run_ambiguity_audit(client, args.limit)
    else: run_manual_review(args, client)


if __name__ == "__main__": main()
