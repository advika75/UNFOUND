"""Deterministic, explainable discovery ranking for UNFOUND/GemMode."""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from backend.catalog_quality import has_embedding
from backend.category_quality import fetch_all
from backend.product_taxonomy import identify_product
from backend.supabase_compat import create_supabase_client

CONFIDENCE_FLOOR = float(os.getenv("DISCOVERY_CONFIDENCE_FLOOR", ".80"))


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


def personalized_score(base: dict[str, Any], row: dict[str, Any], preferences: dict[str, Any] | None) -> float:
    if not preferences: return float(base["gem_score"])
    context = preferences
    preferences = context.get("preferences") or context
    text = _text(row).lower(); matches = 0
    terms = []
    for key in ("preferred_styles", "favorite_categories", "favourite_categories", "preferred_colours", "favorite_brands"):
        value = preferences.get(key) or []
        terms.extend(value if isinstance(value, list) else [value])
    for term in terms: matches += bool(term and str(term).lower() in text)
    if str(row.get("brand_id")) in (context.get("saved_brand_ids") or set()): matches += 2
    return round(min(100, base["gem_score"] + min(12, matches * 3)), 2)


def eligible(row: dict[str, Any], score: dict[str, Any], supabase_url: str) -> bool:
    return is_stable_product(row, supabase_url) and has_embedding(row.get("embedding")) and float(row.get("classifier_confidence") or 0) >= CONFIDENCE_FLOOR and bool(score.get("product_type")) and bool(row.get("brand_id")) and row.get("catalog_status") != "NON_PRODUCT"


def _matches(row: dict[str, Any], *, category: str | None, product_type: str | None) -> bool:
    text = _text(row)
    family, kind = identify_product(text); needle = (category or "").lower().replace("_", "-")
    if product_type and kind != product_type.lower().replace("_", "-"): return False
    audience = str(row.get("audience") or row.get("gender") or "").lower()
    lowered = text.lower().replace("-", " ")
    if needle == "women-tops":
        return audience not in {"men", "male"} and (audience in {"women", "female"} or "women" in lowered) and family in {"tops", "shirts", "tshirts"}
    if needle == "men-shirts":
        return audience not in {"women", "female"} and (audience in {"men", "male"} or "men" in lowered) and family == "shirts"
    if needle == "accessories-jewellery":
        return family == "jewellery"
    if needle and needle not in {str(family or ""), str(kind or "")} and needle.replace("-", " ") not in lowered: return False
    return True


def diversify(rows: list[dict[str, Any]], limit: int, *, max_per_brand: int = 2) -> list[dict[str, Any]]:
    output, brands = [], Counter()
    for row in rows:
        brand = str(row.get("brand_id") or row.get("brand_name") or "")
        if brands[brand] >= max_per_brand: continue
        output.append(row); brands[brand] += 1
        if len(output) >= limit: break
    return output


def score_products(products: list[dict[str, Any]], *, supabase_url: str, interactions: list[dict[str, Any]] | None = None, preferences: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    context = catalog_context(products, interactions)
    output=[]
    for row in products:
        score = product_gem_score(row, context, supabase_url)
        output.append({**row, **score, "personalized_discovery_score": personalized_score(score, row, preferences), "recommendation_reason": recommendation_reason(row, score, personalized=bool(preferences)), "discovery_eligible": eligible(row, score, supabase_url)})
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
        raw=_weighted({"quality":(quality,.35),"uniqueness":(uniqueness,.2),"niche":(niche,.2),"depth":(depth,.15),"freshness":(freshness,.1)})
        value=int(round(100*raw)); styles=Counter()
        for r in rows:
            metadata=r.get("metadata") if isinstance(r.get("metadata"),dict) else {}; values=metadata.get("style") or []
            for style in values if isinstance(values,list) else [values]: styles[str(style)] += 1
        output.append({**brand,"gem_score":value,"gem_label":gem_label(value),"gem_components":{"catalog_quality":round(quality,4),"product_uniqueness":round(uniqueness,4),"niche_factor":niche,"catalog_depth":round(depth,4),"freshness":round(freshness,4)},"eligible_product_count":len(eligible_rows),"dominant_styles":[name for name,count in styles.most_common(3) if len(rows)>=5 and count>=2],"recommendation_reason":"Independent brand with strong catalog quality and distinctive products." if quality>=.7 and uniqueness>=.6 else "A promising independent label worth watching."})
    return output


def discovery_sections(products: list[dict[str, Any]], brands: list[dict[str, Any]], *, supabase_url: str, interactions: list[dict[str, Any]] | None = None, preferences: dict[str, Any] | None = None, category: str | None = None, product_type: str | None = None, limit: int = 16) -> dict[str, Any]:
    scored=[r for r in score_products(products,supabase_url=supabase_url,interactions=interactions,preferences=preferences) if _matches(r,category=category,product_type=product_type)]
    pool=[r for r in scored if r["discovery_eligible"]]
    hidden=diversify(sorted(pool,key=lambda r:(r["gem_score"]*.75+(r["components"].get("niche_factor") or .5)*15+r["exposure_boost"]*10),reverse=True),limit)
    trending_evidence=any((interactions or []))
    trending=diversify(sorted(pool,key=lambda r:((r["components"].get("engagement") or 0)*50+(r["components"].get("freshness") or 0)*25+r["gem_score"]*.25),reverse=True),limit)
    fresh=diversify(sorted(pool,key=lambda r:((r["components"].get("freshness") or 0),r["gem_score"]),reverse=True),limit)
    new=diversify(sorted(pool,key=lambda r:(_date(r.get("scraped_at") or r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),r["gem_score"]),reverse=True),limit)
    missed=diversify(sorted(pool,key=lambda r:(r["gem_score"]*.8+r["exposure_boost"]*20),reverse=True),limit)
    scored_brands=brand_gem_scores(brands,scored)
    emerging=[b for b in scored_brands if b["eligible_product_count"]>=2 and b["gem_score"]>=60]
    emerging.sort(key=lambda b:(b["gem_score"],b["gem_components"]["freshness"]),reverse=True)
    return {"hidden_gems":hidden,"trending":trending,"trending_signal":"interactions+freshness" if trending_evidence else "conservative freshness fallback; insufficient interaction history","fresh_drops":fresh,"new_discoveries":new,"missed":missed,"emerging_brands":emerging[:limit],"scored_products":scored,"scored_brands":scored_brands,"eligible_candidates":len(pool)}


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
