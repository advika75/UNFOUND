"""Targeted, preview-first catalog acquisition and inventory reporting CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv

from backend.category_quality import fetch_all
from backend.product_taxonomy import FAMILY_LABELS, PRODUCT_FAMILIES, identify_product
from backend.supabase_compat import create_supabase_client


CATEGORY_SEEDS = {
    "kurtas": ["KurtaBrandIndia", "ContemporaryEthnicWear", "TrendyKurtiBrand"],
    "ethnic": ["IndianFashionBrand", "ContemporaryEthnicWear", "IndependentClothingIndia"],
    "tops": ["CropTopBrandIndia", "GoingOutTops", "IndieFashionIndia"],
    "streetwear": ["WomenStreetwearIndia", "IndependentClothingIndia"],
    "jewellery": ["MinimalJewelleryIndia", "StatementJewelleryIndia"],
    "bags": ["IndependentBagBrandIndia", "IndianFashionBrand"],
}
INVENTORY_TARGETS = {
    "minimum_products": int(os.getenv("INVENTORY_MIN_PRODUCTS", "20")),
    "minimum_brands": int(os.getenv("INVENTORY_MIN_BRANDS", "3")),
    "healthy_products": int(os.getenv("INVENTORY_HEALTHY_PRODUCTS", "40")),
    "healthy_brands": int(os.getenv("INVENTORY_HEALTHY_BRANDS", "5")),
}
REJECT_TERMS = {"meme", "influencer", "fanpage", "quotes", "aggregator", "giveaway only"}
COMMERCE_TERMS = {"shop", "store", "shipping", "order", "collection", "label", "brand", "handmade"}


def evaluate_brand_candidate(candidate: dict[str, Any], category: str) -> dict[str, Any]:
    text = " ".join(str(candidate.get(field) or "") for field in ("name", "handle", "bio", "recent_posts")).lower()
    rejection = next((term for term in REJECT_TERMS if term in text), None)
    commerce = sum(term in text for term in COMMERCE_TERMS)
    relevant = category.lower().replace("-", " ") in text or any(seed.lower() in re.sub(r"[^a-z]", "", text) for seed in CATEGORY_SEEDS.get(category, []))
    active = bool(candidate.get("recent_activity"))
    valid_profile = str(candidate.get("handle") or "").lstrip("@").replace(".", "").replace("_", "").isalnum()
    score = (0.35 if commerce else 0) + (0.25 if relevant else 0) + (0.2 if active else 0) + (0.2 if valid_profile else 0)
    if rejection:
        score = 0
    return {**candidate, "likely_categories": [category] if relevant else [], "reason_selected": "commerce + category + activity signals" if score >= .6 else "insufficient verified signals", "confidence": round(score, 2), "accepted": score >= .6 and not rejection}


def dedupe_brand_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set(); output = []
    for row in candidates:
        key = str(row.get("handle") or row.get("name") or "").strip().lower().lstrip("@");
        if key and key not in seen:
            seen.add(key); output.append(row)
    return output


def client(write: bool = False):
    load_dotenv("backend/.env", override=False)
    url = os.environ["SUPABASE_URL"]
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") if write else (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY"))
    if not key:
        raise RuntimeError("Required Supabase key is not configured.")
    return create_supabase_client(url, key)


def inventory_report() -> dict[str, Any]:
    db = client(); products = fetch_all(db, "products"); brands = fetch_all(db, "brands")
    counts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in products:
        family, product_type = identify_product(" ".join(str(row.get(k) or "") for k in ("product_name", "description", "normalized_subcategory", "subcategory")))
        if family and product_type and row.get("catalog_status") != "NON_PRODUCT": counts[product_type].append(row)
    lines = []
    for family, types in PRODUCT_FAMILIES.items():
        for product_type in types:
            rows = counts.get(product_type, []); brand_count = len({row.get("brand_id") for row in rows if row.get("brand_id")})
            if len(rows) >= INVENTORY_TARGETS["healthy_products"] and brand_count >= INVENTORY_TARGETS["healthy_brands"]: status = "HEALTHY"
            elif len(rows) >= INVENTORY_TARGETS["minimum_products"] and brand_count >= INVENTORY_TARGETS["minimum_brands"]: status = "ADEQUATE"
            else: status = "NEED_MORE"
            lines.append({"family": FAMILY_LABELS[family], "type": product_type, "products": len(rows), "brands": brand_count, "status": status})
    stable = sum("/storage/v1/object/public/product-images/" in str(row.get("image_url") or "") for row in products)
    category_for_family = {"Tops":"tops", "Ethnic Upperwear":"kurtas", "Sets":"ethnic", "Jewellery":"jewellery", "Bags":"bags", "Outerwear":"streetwear"}
    recommendations = [{"product_type":row["type"], "acquisition_category":category_for_family.get(row["family"], row["family"].lower()), "command":f"python -m backend.catalog_discovery discover-brands --category {category_for_family.get(row['family'], row['family'].lower())} --limit 20 --dry-run"} for row in lines if row["status"] == "NEED_MORE"]
    return {"total_brands": len(brands), "total_products": len(products), "stable_images": stable, "stable_image_coverage_percent": round(100 * stable / len(products), 1) if products else 0, "targets": INVENTORY_TARGETS, "inventory": lines, "discovery_recommendations": recommendations}


def run_acquisition(args: argparse.Namespace, *, ingest: bool) -> None:
    from training.scraper_pipeline import run_pipeline
    seeds = CATEGORY_SEEDS.get(args.category)
    if not seeds:
        raise SystemExit(f"Unknown category. Choose: {', '.join(CATEGORY_SEEDS)}")
    if ingest and not args.approve_preview:
        raise SystemExit("Refusing write: run discover-brands first, then pass --approve-preview.")
    asyncio.run(run_pipeline(seeds, per_hashtag_limit=max(1, min(args.limit, 30)), dry_run=not ingest))


def main() -> None:
    parser = argparse.ArgumentParser(description="UNFOUND targeted catalog operations")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("inventory-report"); report.add_argument("--json", action="store_true")
    discover = sub.add_parser("discover-brands"); discover.add_argument("--category", required=True); discover.add_argument("--limit", type=int, default=20); discover.add_argument("--dry-run", action="store_true", default=True)
    ingest = sub.add_parser("ingest-brands"); ingest.add_argument("--category", required=True); ingest.add_argument("--limit", type=int, default=20); ingest.add_argument("--approve-preview", action="store_true")
    args = parser.parse_args()
    if args.command == "inventory-report":
        report_data = inventory_report(); print(json.dumps(report_data, indent=2))
    else:
        run_acquisition(args, ingest=args.command == "ingest-brands")


if __name__ == "__main__":
    main()
