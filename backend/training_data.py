"""Evidence-first dataset and annotation tooling for GemMode ML experiments."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import httpx
from dotenv import load_dotenv

from backend.catalog_quality import has_embedding
from backend.category_quality import fetch_all, is_stable_storage_url
from backend.product_taxonomy import FAMILY_LABELS, TYPE_LABELS, identify_product
from backend.supabase_compat import create_supabase_client


BAD_IMAGE_STATUSES = {"BROKEN", "EXPIRED_OR_FORBIDDEN", "INVALID_URL", "RECOVERY_REQUIRED", "UNRECOVERABLE", "MISSING"}
STYLE_TERMS = ("minimal", "streetwear", "party", "formal", "casual", "ethnic", "y2k", "vintage", "quiet luxury", "boho", "sporty", "trendy")
COLOURS = ("black", "white", "silver", "gold", "brown", "beige", "blue", "navy", "red", "green", "pink", "purple", "grey", "gray", "orange", "yellow")
DEFAULT_CONFIDENCE = float(os.getenv("TRAINING_MIN_CONFIDENCE", "0.80"))
WEAK_CLASS_COUNT = int(os.getenv("TRAINING_WEAK_CLASS_COUNT", "20"))
SUFFICIENT_CLASS_COUNT = int(os.getenv("TRAINING_SUFFICIENT_CLASS_COUNT", "50"))
READY_MIN_CLASSES = int(os.getenv("TRAINING_READY_MIN_CLASSES", "3"))
READY_MIN_EXAMPLES = int(os.getenv("TRAINING_READY_MIN_EXAMPLES", "200"))


def product_text(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return " ".join(str(value or "") for value in (
        row.get("product_name"), row.get("item_name"), row.get("description"), row.get("subcategory"),
        row.get("normalized_subcategory"), metadata.get("caption"), metadata.get("alt_text"),
    )).lower()


def stable_image(row: dict[str, Any], supabase_url: str) -> bool:
    url = str(row.get("image_url") or "")
    return (
        is_stable_storage_url(url, supabase_url)
        and "/storage/v1/object/public/product-images/" in url
        and row.get("image_status") not in BAD_IMAGE_STATUSES
        and "unsplash.com" not in url
    )


def duplicate_ids(products: Iterable[dict[str, Any]]) -> set[str]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in products:
        identity = str(row.get("instagram_post_id") or row.get("product_url") or "").strip().lower()
        image = str(row.get("image_url") or "").split("?", 1)[0].strip().lower()
        if identity or image:
            groups[(str(row.get("brand_id") or ""), identity or image)].append(row)
    return {str(row.get("id")) for rows in groups.values() if len(rows) > 1 for row in rows[1:]}


def inferred_metadata(row: dict[str, Any]) -> dict[str, Any]:
    text = product_text(row); family, product_type = identify_product(text)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    styles = metadata.get("style") or [style.title() for style in STYLE_TERMS if style in text]
    if isinstance(styles, str): styles = [styles]
    colours = metadata.get("colours") or metadata.get("colors") or metadata.get("colour") or metadata.get("color") or [colour.title() for colour in COLOURS if colour in text]
    if isinstance(colours, str): colours = [colours]
    return {"family": family, "product_type": product_type, "styles": styles, "colours": colours}


def trusted_training_record(
    row: dict[str, Any], *, supabase_url: str, min_confidence: float = DEFAULT_CONFIDENCE,
    duplicates: set[str] | None = None, require_stable_image: bool = True,
) -> dict[str, Any] | None:
    confidence = float(row.get("classifier_confidence") or 0)
    inferred = inferred_metadata(row)
    if confidence < min_confidence or not inferred["product_type"]:
        return None
    if row.get("catalog_status") == "NON_PRODUCT" or str(row.get("id")) in (duplicates or set()):
        return None
    if not row.get("brand_id") or not has_embedding(row.get("embedding")):
        return None
    if require_stable_image and not stable_image(row, supabase_url):
        return None
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source = str(row.get("classification_source") or "heuristic")
    return {
        "product_id": row.get("id"), "brand_id": row.get("brand_id"),
        "name": row.get("product_name") or row.get("item_name"), "description": row.get("description") or "",
        "caption": metadata.get("caption") or row.get("description") or "", "image_url": row.get("image_url"),
        "category": row.get("normalized_main_category") or row.get("audience") or "",
        "subcategory": row.get("normalized_subcategory") or row.get("subcategory") or "",
        "product_family": FAMILY_LABELS.get(inferred["family"], inferred["family"]),
        "product_type": TYPE_LABELS.get(inferred["product_type"], inferred["product_type"]),
        "colour": inferred["colours"], "style": inferred["styles"],
        "fit": metadata.get("fit"), "material": metadata.get("material"), "audience": row.get("audience") or "",
        "label_confidence": confidence, "label_source": "ai+validated" if source in {"ai", "openai", "manual"} else source,
        "embedding": row.get("embedding"),
    }


def grouped_split(records: list[dict[str, Any]], ratios=(.70, .15, .15), seed="gemmode-v1", group_field="brand_id") -> dict[str, list[dict[str, Any]]]:
    if not math.isclose(sum(ratios), 1.0, abs_tol=1e-8): raise ValueError("Split ratios must sum to 1")
    output = {"train": [], "validation": [], "test": []}; train_cut, validation_cut = ratios[0], ratios[0] + ratios[1]
    for row in records:
        group = str(row.get(group_field) or row.get("product_id"))
        bucket = int(hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()[:12], 16) / float(16**12)
        split = "train" if bucket < train_cut else "validation" if bucket < validation_cut else "test"
        output[split].append({**row, "split": split})
    return output


def readiness_report(records: list[dict[str, Any]], *, stable_image_count: int | None = None) -> dict[str, Any]:
    types = Counter(row.get("product_type") or "Unknown" for row in records)
    categories = Counter(row.get("subcategory") or row.get("category") or "Unknown" for row in records)
    styles = Counter(style for row in records for style in (row.get("style") or []))
    class_status = {name: "SUFFICIENT" if count >= SUFFICIENT_CLASS_COUNT else "WEAK" if count >= WEAK_CLASS_COUNT else "INSUFFICIENT" for name, count in types.items()}
    sufficient = sum(value == "SUFFICIENT" for value in class_status.values())
    stable_count = len(records) if stable_image_count is None else stable_image_count
    if len(records) < READY_MIN_EXAMPLES or sufficient < READY_MIN_CLASSES or stable_count < READY_MIN_EXAMPLES:
        decision = "TRAINING_NOT_READY"
    elif len(records) >= 1000 and sufficient >= 8:
        decision = "READY_FOR_FINE_TUNING_REVIEW"
    else:
        decision = "READY_FOR_LINEAR_PROBE"
    return {"decision": decision, "usable_examples": len(records), "stable_image_examples": stable_count, "product_type_counts": dict(types), "category_counts": dict(categories), "style_counts": dict(styles), "class_status": class_status}


def write_records(records: list[dict[str, Any]], output: Path, format_name: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if format_name == "jsonl":
        output.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in records), encoding="utf-8")
        return
    fields = sorted({key for row in records for key in row})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in records: writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def download_images(records: list[dict[str, Any]], output: Path) -> None:
    image_dir = output.parent / "images"; image_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for row in records:
            response = client.get(str(row["image_url"])); response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("image/"): raise ValueError(f"Non-image response for {row['product_id']}")
            extension = {"image/png":"png", "image/webp":"webp"}.get(response.headers["content-type"].split(";", 1)[0], "jpg")
            path = image_dir / f"{row['product_id']}.{extension}"; path.write_bytes(response.content); row["local_image_path"] = str(path.relative_to(output.parent))


def relevance_metrics(rows: list[dict[str, Any]], ks=(5, 10)) -> dict[str, Any]:
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("label", "")).strip() != "": by_query[str(row["query"])].append(row)
    results = {}
    for query, candidates in by_query.items():
        candidates.sort(key=lambda row: int(row.get("rank") or 9999)); labels = [int(row["label"]) for row in candidates]
        relevant = [1 if label >= 2 else 0 for label in labels]
        first = next((index + 1 for index, value in enumerate(relevant) if value), None)
        ideal = sorted(labels, reverse=True)
        dcg = sum((2**label - 1) / math.log2(index + 2) for index, label in enumerate(labels[:10]))
        idcg = sum((2**label - 1) / math.log2(index + 2) for index, label in enumerate(ideal[:10]))
        results[query] = {**{f"precision@{k}": sum(relevant[:k]) / min(k, len(relevant)) if relevant else 0 for k in ks}, "mrr": 1 / first if first else 0, "ndcg@10": dcg / idcg if idcg else 0}
    averages = {key: round(sum(row[key] for row in results.values()) / len(results), 4) if results else None for key in ("precision@5", "precision@10", "mrr", "ndcg@10")}
    return {"queries": results, "average": averages, "labeled_query_count": len(results)}


def hard_negative_kind(query: str, product: dict[str, Any]) -> str | None:
    requested_family, requested_type = identify_product(query); candidate_family, candidate_type = identify_product(product_text(product))
    query_colours = {colour for colour in COLOURS if colour in query.lower()}; product_colours = {colour for colour in COLOURS if colour in product_text(product)}
    if requested_family and candidate_family != requested_family and query_colours & product_colours: return "same_colour_wrong_family"
    if requested_family == candidate_family and requested_type != candidate_type: return "same_family_wrong_type"
    if requested_type == candidate_type and query_colours and not query_colours & product_colours: return "right_type_wrong_colour"
    return None


def _client():
    load_dotenv("backend/.env", override=False); url = os.environ["SUPABASE_URL"]; key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    if not key: raise RuntimeError("SUPABASE_ANON_KEY or SUPABASE_KEY is required")
    return create_supabase_client(url, key), url


def _annotation_rows(api_url: str, queries: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    rows = []
    with httpx.Client(timeout=120) as client:
        for case in queries:
            payload = client.post(f"{api_url.rstrip('/')}/api/discover", data={"text_query": case["query"]}).raise_for_status().json()
            for rank, product in enumerate(payload.get("results", [])[:top_k], 1):
                scores = product.get("score_breakdown") or {}
                expected_family, _ = identify_product(case["query"]); actual_family, _ = identify_product(product_text(product))
                rows.append({"query": case["query"], "rank": rank, "product_id": product.get("id"), "product_name": product.get("product_name"), "product": product.get("product_name"), "brand": product.get("brand_name"), "expected_family":expected_family, "actual_family":actual_family, "image": product.get("image_url"), "current_score": product.get("final_score"), **{key: scores.get(key) for key in ("semantic_similarity", "product_family_match", "product_type_match", "category_match", "colour_match", "style_match", "personalization_match")}, "hard_negative_type": hard_negative_kind(case["query"], product), "human_relevance": "", "label": "", "notes":"", "review": ""})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="GemMode training-data and evaluation tooling"); sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export"); export.add_argument("--output", type=Path, required=True); export.add_argument("--category"); export.add_argument("--product-type"); export.add_argument("--brand"); export.add_argument("--min-confidence", type=float, default=DEFAULT_CONFIDENCE); export.add_argument("--limit", type=int); export.add_argument("--include-images", action="store_true"); export.add_argument("--include-embeddings", action="store_true"); export.add_argument("--format", choices=("jsonl", "csv"), default="jsonl"); export.add_argument("--include-unstable-images", action="store_true")
    report = sub.add_parser("report"); report.add_argument("--include-unstable-images", action="store_true")
    annotate = sub.add_parser("create-search-annotation-set"); annotate.add_argument("--queries", type=Path, required=True); annotate.add_argument("--top-k", type=int, default=20); annotate.add_argument("--output", type=Path, default=Path("data/search_annotations.csv")); annotate.add_argument("--api-url", default="http://127.0.0.1:8000")
    review = sub.add_parser("create-review-set"); review.add_argument("--output", type=Path, required=True); review.add_argument("--limit", type=int, default=500)
    metrics = sub.add_parser("evaluate-relevance"); metrics.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"create-search-annotation-set"}:
        rows = _annotation_rows(args.api_url, json.loads(args.queries.read_text()), args.top_k); write_records(rows, args.output, "csv" if args.output.suffix == ".csv" else "jsonl"); print(json.dumps({"rows": len(rows), "output": str(args.output)})); return
    if args.command == "evaluate-relevance":
        with args.input.open(encoding="utf-8") as handle: rows = list(csv.DictReader(handle)) if args.input.suffix == ".csv" else [json.loads(line) for line in handle if line.strip()]
        print(json.dumps(relevance_metrics(rows), indent=2)); return
    db, url = _client(); products = fetch_all(db, "products"); duplicates = duplicate_ids(products)
    if args.command == "create-review-set":
        rows = []
        for row in products:
            confidence = float(row.get("classifier_confidence") or 0); inferred = inferred_metadata(row)
            if confidence >= DEFAULT_CONFIDENCE and inferred["product_type"]: continue
            rows.append({"product_id":row.get("id"), "image":row.get("image_url"), "name":row.get("product_name"), "caption":row.get("description"), "brand":row.get("brand_name"), "current_category":row.get("normalized_subcategory"), "proposed_category":"", "current_product_type":TYPE_LABELS.get(inferred["product_type"] or "", inferred["product_type"]), "proposed_product_type":"", "style":inferred["styles"], "colour":inferred["colours"], "confidence":confidence, "approved":"", "rejected":"", "corrected":""})
            if len(rows) >= args.limit: break
        write_records(rows, args.output, "csv" if args.output.suffix == ".csv" else "jsonl"); print(json.dumps({"rows":len(rows), "output":str(args.output)})); return
    require_stable = not args.include_unstable_images
    records = [record for row in products if (record := trusted_training_record(row, supabase_url=url, min_confidence=getattr(args, "min_confidence", DEFAULT_CONFIDENCE), duplicates=duplicates, require_stable_image=require_stable))]
    stable_count = sum(stable_image(row, url) for row in products if float(row.get("classifier_confidence") or 0) >= DEFAULT_CONFIDENCE)
    if args.command == "report": print(json.dumps(readiness_report(records, stable_image_count=stable_count), indent=2)); return
    if args.category: records = [row for row in records if args.category.lower() in f"{row['category']} {row['subcategory']}".lower()]
    if args.product_type: records = [row for row in records if args.product_type.lower() == str(row["product_type"]).lower()]
    if args.brand: records = [row for row in records if args.brand.lower() in str(row["brand_id"]).lower()]
    if args.limit: records = records[:args.limit]
    split_records = [row for values in grouped_split(records).values() for row in values]
    if args.include_images: download_images(split_records, args.output)
    if not args.include_embeddings:
        for row in split_records: row.pop("embedding", None)
    write_records(split_records, args.output, args.format); print(json.dumps({"rows":len(split_records), "output":str(args.output), "readiness":readiness_report(records, stable_image_count=stable_count)["decision"]}))


if __name__ == "__main__": main()
