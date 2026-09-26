"""Read-only QA and before/after snapshots for a small curated ingestion pilot."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.category_quality import fetch_all, is_stable_storage_url
from backend.catalog_discovery import inventory_report
from backend.product_taxonomy import identify_product
from backend.supabase_compat import create_supabase_client


def embedding_status(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try: values = [float(item) for item in value.strip().strip("[]").split(",") if item]
        except ValueError: values = []
    elif isinstance(value, list): values = [float(item) for item in value]
    else: values = []
    finite = bool(values) and all(math.isfinite(item) for item in values); norm = math.sqrt(sum(item * item for item in values)) if finite else 0
    return {"exists":bool(values), "dimensions":len(values), "finite":finite, "l2_norm":round(norm, 6), "valid":len(values) == 512 and finite and abs(norm - 1) <= .02}


def feed_isolation(products: list[dict[str, Any]], family_names: set[str]) -> dict[str, Any]:
    mismatches = []
    for row in products:
        family, _ = identify_product(" ".join(str(row.get(key) or "") for key in ("product_name", "description", "subcategory", "normalized_subcategory")))
        if family not in family_names: mismatches.append({"id":row.get("id"), "name":row.get("product_name"), "family":family})
    return {"total":len(products), "valid":len(products)-len(mismatches), "precision":round((len(products)-len(mismatches))/len(products), 4) if products else 1.0, "mismatches":mismatches}


def build_pilot_report(products: list[dict[str, Any]], brands: list[dict[str, Any]], *, supabase_url: str, handles: set[str]) -> dict[str, Any]:
    selected_brands = [row for row in brands if str(row.get("instagram_username") or "").lower() in handles]
    ids = {row.get("id") for row in selected_brands}; selected = [row for row in products if row.get("brand_id") in ids]
    images = Counter("stable" if is_stable_storage_url(row.get("image_url"), supabase_url) else "missing" if not row.get("image_url") else "external" for row in selected)
    embeddings = [embedding_status(row.get("embedding")) for row in selected]
    categories = Counter(row.get("normalized_subcategory") or "unresolved" for row in selected)
    low_confidence = [row.get("id") for row in selected if float(row.get("classifier_confidence") or 0) < .8]
    return {"pilot_brands":len(selected_brands), "pilot_products":len(selected), "images":dict(images), "stable_image_coverage_percent":round(images["stable"] / len(selected) * 100, 1) if selected else 0, "valid_embeddings":sum(row["valid"] for row in embeddings), "invalid_embeddings":sum(not row["valid"] for row in embeddings), "category_counts":dict(categories), "low_confidence_product_ids":low_confidence, "brand_ids":sorted(str(value) for value in ids)}


def compare_inventory(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    previous = {row["type"]:row for row in before.get("inventory", [])}; output=[]
    for row in after.get("inventory", []):
        old=previous.get(row["type"], {"products":0,"brands":0}); output.append({**row, "before_products":old["products"], "before_brands":old["brands"], "product_change":row["products"]-old["products"], "brand_change":row["brands"]-old["brands"]})
    return output


def main() -> None:
    parser=argparse.ArgumentParser(description="Read-only GemMode pilot QA"); parser.add_argument("command", choices=("snapshot","verify")); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--before", type=Path); parser.add_argument("--handles", default="")
    args=parser.parse_args(); load_dotenv("backend/.env", override=False); url=os.environ["SUPABASE_URL"]; key=os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY"); db=create_supabase_client(url,key)
    inventory=inventory_report()
    if args.command == "snapshot": result=inventory
    else:
        products=fetch_all(db,"products"); brands=fetch_all(db,"brands"); handles={value.strip().lower().lstrip("@") for value in args.handles.split(",") if value.strip()}
        result={"pilot":build_pilot_report(products,brands,supabase_url=url,handles=handles),"inventory":inventory}
        if args.before: result["inventory_comparison"]=compare_inventory(json.loads(args.before.read_text()),inventory)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))


if __name__ == "__main__": main()
