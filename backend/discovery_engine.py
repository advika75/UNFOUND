"""Deterministic, explainable discovery ranking for UNFOUND/GemMode."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from backend.catalog_quality import has_embedding
from backend.category_quality import fetch_all
from backend.product_taxonomy import family_match, gender_match_score, identify_product, type_match
from backend.supabase_compat import create_supabase_client

CONFIDENCE_FLOOR = float(os.getenv("DISCOVERY_CONFIDENCE_FLOOR", ".80"))
# Independent of the product-rail `limit` param: brand rails are a handful of cards
# wide regardless of how many products were requested, and with the catalog's current
# ~17 brands meeting the eligibility bar at all, capping each rail at the product-rail
# default (16) would leave nothing for a second, non-overlapping rail to draw from.
BRAND_RAIL_SIZE = int(os.getenv("DISCOVERY_BRAND_RAIL_SIZE", "8"))
# No single brand may take more than this many slots in one rail (existing behavior,
# promoted to a named constant).
MAX_PRODUCTS_PER_BRAND_PER_RAIL = int(os.getenv("DISCOVERY_MAX_PER_BRAND", "2"))
# No single category may take more than this many slots in one rail either -- this
# catalog's raw product mix is heavily skewed (Women Tops alone is ~62% of it), so
# ranking by score alone with only a brand cap still lets one oversized category fill
# the whole feed. Capped low enough that even an 11-category eligible pool (today's
# real count) still gets sampled across, not dominated by whichever category has the
# most high-scoring candidates.
MAX_PRODUCTS_PER_CATEGORY_PER_RAIL = int(os.getenv("DISCOVERY_MAX_PER_CATEGORY", "3"))


def _number(value: Any) -> float | None:
    try: return float(value) if value not in (None, "") else None
    except (TypeError, ValueError): return None


def _date(value: Any) -> datetime | None:
    if not value: return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except ValueError: return None


def _text(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return " ".join(str(row.get(key) or "") for key in ("product_name", "item_name", "description", "category", "subcategory", "normalized_subcategory", "source_hashtag")) + " " + " ".join(str(metadata.get(key) or "") for key in ("style", "colour", "colors", "occasion"))


def is_stable_product(row: dict[str, Any], supabase_url: str) -> bool:
    url = str(row.get("image_url") or "")
    return bool(supabase_url) and url.startswith(f"{supabase_url.rstrip('/')}/storage/v1/object/public/product-images/") and "unsplash.com" not in url and str(row.get("image_status") or "").upper() not in {"BROKEN", "MISSING", "RECOVERY_REQUIRED", "UNRECOVERABLE"}


def gem_label(score: int) -> str | None:
    if score >= 90: return "Exceptional Gem"
    if score >= 80: return "Hidden Gem"
    if score >= 70: return "Worth Discovering"
    if score >= 60: return "Promising"
    return None


def _weighted(values: dict[str, tuple[float | None, float]]) -> float:
    available = [(value, weight) for value, weight in values.values() if value is not None]
    return sum(value * weight for value, weight in available) / sum(weight for _, weight in available) if available else 0


def catalog_context(products: list[dict[str, Any]], interactions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    types, brands = Counter(), Counter()
    for row in products:
        _, kind = identify_product(_text(row))
        if kind: types[kind] += 1
        if row.get("brand_id"): brands[str(row["brand_id"])] += 1
    events: dict[str, Counter] = defaultdict(Counter)
    now = datetime.now(timezone.utc)
    for event in interactions or []:
        product_id = str(event.get("product_id") or event.get("target_id") or "")
        if not product_id: continue
        happened = _date(event.get("created_at") or event.get("timestamp")) or now
        decay = math.exp(-max(0, (now - happened).days) / 30)
        name = str(event.get("event_type") or event.get("action") or event.get("type") or "")
        name = {"save":"save_product", "click":"product_click", "view":"discovery_impression"}.get(name, name)
        events[product_id][name] += decay
    return {"type_counts": types, "brand_counts": brands, "events": events, "total": len(products), "now": now}


def product_gem_score(row: dict[str, Any], context: dict[str, Any], supabase_url: str) -> dict[str, Any]:
    text = _text(row); _, kind = identify_product(text)
    confidence = max(0, min(1, _number(row.get("classifier_confidence")) or 0))
    metadata_fields = ("product_name", "brand_id", "category_id", "normalized_subcategory")
    completeness = sum(bool(row.get(key)) for key in metadata_fields) / len(metadata_fields)
    image = 1.0 if is_stable_product(row, supabase_url) else 0.35 if row.get("image_url") and "unsplash.com" not in str(row.get("image_url")) else 0
    embedding = 1.0 if has_embedding(row.get("embedding")) else 0
    quality = .30 * completeness + .25 * confidence + .25 * image + .20 * embedding
    count = context["type_counts"].get(kind, context["total"] or 1)
    scarcity = 1 - min(1, max(0, count - 1) / max(20, context["total"] * .08)) if kind else .35
    neighbor_similarity = _number(row.get("nearest_neighbor_similarity") or row.get("max_neighbor_similarity"))
    visual = None if neighbor_similarity is None else max(0, min(1, 1 - neighbor_similarity))
    uniqueness = _weighted({"scarcity": (scarcity, .6), "visual": (visual, .4)})
    niche = _number(row.get("niche_score") or row.get("brand_niche_score"))
    niche = max(0, min(1, niche)) if niche is not None else None
    date = _date(row.get("source_post_date") or row.get("scraped_at") or row.get("created_at"))
    freshness = None if date is None else math.exp(-max(0, (context["now"] - date).days) / 120)
    events = context["events"].get(str(row.get("id")), Counter())
    event_total = events["save_product"] * 3 + events["product_click"] * 1.5 + events["discovery_impression"] * .15
    engagement = min(1, event_total / 20) if event_total else None
    components = {"quality": quality, "uniqueness": uniqueness, "niche_factor": niche, "freshness": freshness, "engagement": engagement}
    base = _weighted({"quality": (quality, .42), "uniqueness": (uniqueness, .23), "niche": (niche, .16), "freshness": (freshness, .12), "engagement": (engagement, .07)})
    score = int(round(max(0, min(100, base * 100))))
    impressions = events["discovery_impression"]
    exposure_boost = 1 / (1 + impressions)
    return {"gem_score": score, "gem_label": gem_label(score), "components": {key: None if value is None else round(value, 4) for key, value in components.items()}, "product_family": identify_product(text)[0], "product_type": kind, "exposure_boost": round(exposure_boost, 4)}


def recommendation_reason(row: dict[str, Any], score: dict[str, Any], *, personalized: bool = False) -> str:
    parts = []
    c = score["components"]
    if c.get("quality", 0) >= .8: parts.append("strong catalog quality")
    if c.get("uniqueness", 0) >= .7: parts.append("distinctive style")
    if c.get("niche_factor") is not None and c["niche_factor"] >= .7: parts.append("independent-label appeal")
    if personalized: parts.append("matches your saved preferences")
    return ("Recommended for " + " + ".join(parts[:3]) + ".") if parts else "Worth exploring based on its catalog relevance."


def personalized_score(base: dict[str, Any], row: dict[str, Any], preferences: dict[str, Any] | None) -> tuple[float, bool]:
    """Score, and whether that score reflects a REAL preference match -- not just
    "a signed-in user happened to have a preferences object". Without this second
    value, a signed-out visitor and a signed-in user with zero matching preferences
    get the exact same number (plain gem_score) with no way to tell them apart, which
    is the same "renders a real-looking number with nothing behind it" bug as the
    similarity-score placeholder, just moved to a different field."""
    if not preferences: return float(base["gem_score"]), False
    context = preferences
    preferences = context.get("preferences") or context
    text = _text(row).lower(); matches = 0
    terms = []
    for key in ("preferred_styles", "favorite_categories", "favourite_categories", "preferred_colours", "favorite_brands"):
        value = preferences.get(key) or []
        terms.extend(value if isinstance(value, list) else [value])
    for term in terms: matches += bool(term and str(term).lower() in text)
    if str(row.get("brand_id")) in (context.get("saved_brand_ids") or set()): matches += 2
    return round(min(100, base["gem_score"] + min(12, matches * 3)), 2), matches > 0


def eligible(row: dict[str, Any], score: dict[str, Any], supabase_url: str) -> bool:
    return is_stable_product(row, supabase_url) and has_embedding(row.get("embedding")) and float(row.get("classifier_confidence") or 0) >= CONFIDENCE_FLOOR and bool(score.get("product_type")) and bool(row.get("brand_id")) and row.get("catalog_status") != "NON_PRODUCT"


_GENDER_WORDS = {"men", "women", "unisex"}


def _matches(row: dict[str, Any], *, category: str | None, product_type: str | None) -> bool:
    """Whether `row` belongs in a rail labelled `category` (e.g. "men-shirts") --
    decomposes the slug into an optional gender prefix and a family/type suffix (the
    last segment: "jewellery" out of "accessories-jewellery", "jeans" out of
    "men-jeans") and gates on each via the same functions the search path uses
    (gender_match_score, family_match, type_match), rather than a separate hand-rolled
    check that can drift from them.
    """
    text = _text(row)
    if product_type:
        _, kind = identify_product(text)
        if kind != product_type.lower().replace("_", "-"):
            return False
    needle = (category or "").lower().replace("_", "-")
    if not needle:
        return True
    parts = needle.split("-")
    gender_word = parts[0] if parts[0] in _GENDER_WORDS else None
    family_or_type = parts[-1]
    if gender_word and gender_match_score(row, gender_word) <= 0.0:
        return False
    if family_match(text, family_or_type) or type_match(text, family_or_type):
        return True
    # Neither a recognized family nor type suffix (e.g. a raw legacy category name) --
    # fall back to loose matching against the identified family/type or slug words.
    family, kind = identify_product(text)
    lowered = text.lower().replace("-", " ")
    return needle in {str(family or ""), str(kind or "")} or needle.replace("-", " ") in lowered


def diversify(
    rows: list[dict[str, Any]],
    limit: int,
    *,
    seed: str = "",
    max_per_brand: int = MAX_PRODUCTS_PER_BRAND_PER_RAIL,
    max_per_category: int = MAX_PRODUCTS_PER_CATEGORY_PER_RAIL,
) -> list[dict[str, Any]]:
    """`rows` must already be sorted by that rail's own scoring formula (each call
    site in discovery_sections ranks differently -- hidden_gems isn't trending isn't
    fresh_drops). Samples across categories via round-robin instead of taking the
    flat top-N, so one oversized category (this catalog: "Women Tops" alone is ~62%
    of all products) can't fill a whole rail just by having the most candidates --
    plus the existing per-brand cap, now a named constant instead of a bare default.

    `seed` controls the round-robin's category-visitation order: the same seed always
    produces the same order (a signed-in user's id, or a session id from the client),
    so one user's feed is stable across reloads, while a different seed -- a
    different user -- sees a different order. This does not touch which categories
    or products exist, only the order variety-sampling walks them in.
    """
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row.get("product_family") or "uncategorized")].append(row)
    category_order = sorted(by_category)
    random.Random(seed).shuffle(category_order)

    output: list[dict[str, Any]] = []
    brand_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    cursors = dict.fromkeys(category_order, 0)
    progressed = True
    while len(output) < limit and progressed:
        progressed = False
        for category in category_order:
            if len(output) >= limit:
                break
            if category_counts[category] >= max_per_category:
                continue
            items = by_category[category]
            while cursors[category] < len(items):
                row = items[cursors[category]]
                cursors[category] += 1
                brand = str(row.get("brand_id") or row.get("brand_name") or "")
                if brand_counts[brand] >= max_per_brand:
                    continue
                output.append(row)
                brand_counts[brand] += 1
                category_counts[category] += 1
                progressed = True
                break
    # The category cap exists to stop one oversized category from dominating -- it
    # must never leave a rail under-filled just because there aren't enough distinct
    # categories to spread across (this catalog's real eligible pool has only 11).
    # Second pass: same cursors, same per-brand cap, but no per-category cap.
    progressed = True
    while len(output) < limit and progressed:
        progressed = False
        for category in category_order:
            if len(output) >= limit:
                break
            items = by_category[category]
            while cursors[category] < len(items):
                row = items[cursors[category]]
                cursors[category] += 1
                brand = str(row.get("brand_id") or row.get("brand_name") or "")
                if brand_counts[brand] >= max_per_brand:
                    continue
                output.append(row)
                brand_counts[brand] += 1
                progressed = True
                break
    return output


def score_products(products: list[dict[str, Any]], *, supabase_url: str, interactions: list[dict[str, Any]] | None = None, preferences: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    context = catalog_context(products, interactions)
    output=[]
    for row in products:
        score = product_gem_score(row, context, supabase_url)
        personalized_discovery_score, personalization_matched = personalized_score(score, row, preferences)
        output.append({
            **row, **score,
            "personalized_discovery_score": personalized_discovery_score,
            "personalization_matched": personalization_matched,
            "recommendation_reason": recommendation_reason(row, score, personalized=personalization_matched),
            "discovery_eligible": eligible(row, score, supabase_url),
        })
    return output


def brand_gem_scores(brands: list[dict[str, Any]], scored_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_products:
        if row.get("brand_id"): grouped[str(row["brand_id"])].append(row)
    output=[]
    for brand in brands:
        rows=grouped.get(str(brand.get("id")), []); eligible_rows=[r for r in rows if r["discovery_eligible"]]
        quality=sum(r["components"]["quality"] for r in rows)/len(rows) if rows else 0
        uniqueness=sum(r["components"]["uniqueness"] for r in rows)/len(rows) if rows else 0
        niche=_number(brand.get("niche_score")); depth=min(1, len(eligible_rows)/8)
        freshness=max((r["components"].get("freshness") or 0 for r in rows), default=0)
        # Same per-product engagement signal product_gem_score already computes
        # (save/click/impression events), aggregated per brand -- not a new metric.
        engagement=sum(r["components"].get("engagement") or 0 for r in rows)/len(rows) if rows else 0
        raw=_weighted({"quality":(quality,.35),"uniqueness":(uniqueness,.2),"niche":(niche,.2),"depth":(depth,.15),"freshness":(freshness,.1)})
        value=int(round(100*raw)); styles=Counter()
        for r in rows:
            metadata=r.get("metadata") if isinstance(r.get("metadata"),dict) else {}; values=metadata.get("style") or []
            for style in values if isinstance(values,list) else [values]: styles[str(style)] += 1
        output.append({**brand,"gem_score":value,"gem_label":gem_label(value),"gem_components":{"catalog_quality":round(quality,4),"product_uniqueness":round(uniqueness,4),"niche_factor":niche,"catalog_depth":round(depth,4),"freshness":round(freshness,4),"engagement":round(engagement,4)},"eligible_product_count":len(eligible_rows),"dominant_styles":[name for name,count in styles.most_common(3) if len(rows)>=5 and count>=2],"recommendation_reason":"Independent brand with strong catalog quality and distinctive products." if quality>=.7 and uniqueness>=.6 else "A promising independent label worth watching."})
    return output


def discovery_sections(products: list[dict[str, Any]], brands: list[dict[str, Any]], *, supabase_url: str, interactions: list[dict[str, Any]] | None = None, preferences: dict[str, Any] | None = None, category: str | None = None, product_type: str | None = None, limit: int = 16, seed: str = "") -> dict[str, Any]:
    scored=[r for r in score_products(products,supabase_url=supabase_url,interactions=interactions,preferences=preferences) if _matches(r,category=category,product_type=product_type)]
    pool=[r for r in scored if r["discovery_eligible"]]
    hidden=diversify(sorted(pool,key=lambda r:(r["gem_score"]*.75+(r["components"].get("niche_factor") or .5)*15+r["exposure_boost"]*10),reverse=True),limit,seed=seed)
    trending_evidence=any((interactions or []))
    trending=diversify(sorted(pool,key=lambda r:((r["components"].get("engagement") or 0)*50+(r["components"].get("freshness") or 0)*25+r["gem_score"]*.25),reverse=True),limit,seed=seed)
    fresh=diversify(sorted(pool,key=lambda r:((r["components"].get("freshness") or 0),r["gem_score"]),reverse=True),limit,seed=seed)
    new=diversify(sorted(pool,key=lambda r:(_date(r.get("scraped_at") or r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),r["gem_score"]),reverse=True),limit,seed=seed)
    missed=diversify(sorted(pool,key=lambda r:(r["gem_score"]*.8+r["exposure_boost"]*20),reverse=True),limit,seed=seed)
    scored_brands=brand_gem_scores(brands,scored)
    # Shuffled (seeded) before the stable sort-by-score below: exact/near ties get a
    # session-stable-but-varied order, while real score differences are unaffected --
    # the same "sample among near-ties, don't always show the identical top-N" idea
    # as diversify(), applied to brand rails (which don't have a per-brand cap to
    # apply in the first place, since they're already one row per brand).
    brand_rng=random.Random(seed)
    scored_brands_shuffled=list(scored_brands)
    brand_rng.shuffle(scored_brands_shuffled)
    emerging=[b for b in scored_brands_shuffled if b["eligible_product_count"]>=2 and b["gem_score"]>=60]
    emerging.sort(key=lambda b:(b["gem_score"],b["gem_components"]["freshness"]),reverse=True)
    emerging=emerging[:BRAND_RAIL_SIZE]
    # A real, independent ranking -- freshness/engagement/depth/niche, deliberately
    # NOT quality/uniqueness (that axis is what "emerging" already ranks by) -- drawn
    # from the same data-sufficiency bar but excluding whatever emerging already
    # claimed, so the two brand rails never show the same names in the same order.
    emerging_ids={str(b.get("id")) for b in emerging}
    trending_brand_pool=[b for b in scored_brands_shuffled if b["eligible_product_count"]>=2 and str(b.get("id")) not in emerging_ids]
    trending_brands=sorted(
        trending_brand_pool,
        key=lambda b:(b["gem_components"]["freshness"]*.35+b["gem_components"]["engagement"]*.25+b["gem_components"]["catalog_depth"]*.25+(b["gem_components"]["niche_factor"] or .5)*.15),
        reverse=True,
    )[:BRAND_RAIL_SIZE]
    return {"hidden_gems":hidden,"trending":trending,"trending_signal":"interactions+freshness" if trending_evidence else "conservative freshness fallback; insufficient interaction history","fresh_drops":fresh,"new_discoveries":new,"missed":missed,"emerging_brands":emerging,"trending_brands":trending_brands,"scored_products":scored,"scored_brands":scored_brands,"eligible_candidates":len(pool)}


def distribution(values: list[int]) -> dict[str, Any]:
    if not values: return {"count":0}
    ordered=sorted(values)
    pct=lambda p: ordered[min(len(ordered)-1,round((len(ordered)-1)*p))]
    return {"count":len(values),"minimum":min(values),"mean":round(statistics.mean(values),2),"median":statistics.median(values),"maximum":max(values),"p10":pct(.1),"p25":pct(.25),"p50":pct(.5),"p75":pct(.75),"p90":pct(.9)}


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=("gem-score-health","discovery-quality")); args=parser.parse_args()
    load_dotenv("backend/.env",override=False); url=os.environ["SUPABASE_URL"]; key=os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    client=create_supabase_client(url,key); products=fetch_all(client,"products"); brands=fetch_all(client,"brands")
    sections=discovery_sections(products,brands,supabase_url=url)
    if args.command=="gem-score-health":
        print(json.dumps({"products":distribution([r["gem_score"] for r in sections["scored_products"]]),"brands":distribution([r["gem_score"] for r in sections["scored_brands"]]),"labels":dict(Counter(r["gem_label"] or "Unlabelled" for r in sections["scored_products"]))},indent=2))
    else:
        report={}
        for name in ("hidden_gems","trending","fresh_drops","new_discoveries","missed"):
            rows=sections[name]; report[name]={"selected":len(rows),"brand_diversity":len({r.get('brand_id') for r in rows}),"category_diversity":len({r.get('category_id') for r in rows}),"stable_image_coverage":round(sum(r['discovery_eligible'] for r in rows)/len(rows),3) if rows else 0,"duplicate_rate":round(1-len({r.get('id') for r in rows})/len(rows),3) if rows else 0}
        report["eligible_candidates"]=sections["eligible_candidates"]; print(json.dumps(report,indent=2))


if __name__ == "__main__": main()
