from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx


DEFAULT_QUERIES = Path(__file__).with_name("search_quality_queries.json")


def result_text(product: dict[str, Any]) -> str:
    return " ".join(str(product.get(field) or "") for field in (
        "product_name", "description", "category", "subcategory"
    )).lower()


def evaluate_text_queries(api_url: str, query_file: Path) -> list[dict[str, Any]]:
    cases = json.loads(query_file.read_text(encoding="utf-8"))
    report = []
    with httpx.Client(timeout=120) as client:
        for case in cases:
            started_at = time.perf_counter()
            response = client.post(f"{api_url}/api/discover", data={"text_query": case["query"], "debug": "true"})
            response.raise_for_status()
            latency_ms = round((time.perf_counter() - started_at) * 1000, 1)
            payload = response.json()
            top_five = payload.get("best_matches", payload.get("results", []))[:5]
            rows = []
            understanding = payload.get("query_understanding", {})
            for product in top_five:
                relevant = any(term in result_text(product) for term in case["expected_terms"])
                rows.append({
                    "product_name": product.get("product_name"),
                    "category": product.get("category"),
                    "product_type": product.get("subcategory") or product.get("normalized_subcategory"),
                    "similarity": product.get("similarity_score"),
                    "final_score": product.get("final_score"),
                    "category_match": product.get("score_breakdown", {}).get("category_match"),
                    "product_family_match": product.get("score_breakdown", {}).get("product_family_match"),
                    "attribute_match": min(
                        product.get("score_breakdown", {}).get("colour_match", 1),
                        product.get("score_breakdown", {}).get("style_match", 1),
                    ),
                    "price_compliant": understanding.get("max_price") is None or (
                        product.get("price") is not None and float(product["price"]) <= float(understanding["max_price"])
                    ),
                    "relevant": relevant,
                })
            report.append({
                "query": case["query"],
                "latency_ms": latency_ms,
                "top_5": rows,
                "relevant_in_top_5": sum(row["relevant"] for row in rows),
                "product_family_precision": round(sum(float(row["product_family_match"] or 0) for row in rows) / len(rows), 3) if rows else 1.0,
                "category_precision": round(sum(float(row["category_match"] or 0) for row in rows) / len(rows), 3) if rows else 1.0,
                "attribute_precision": round(sum(float(row["attribute_match"] or 0) for row in rows) / len(rows), 3) if rows else 1.0,
                "price_compliance": round(sum(bool(row["price_compliant"]) for row in rows) / len(rows), 3) if rows else 1.0,
                "inventory_gap_correct": not payload.get("inventory_gap") or len(top_five) < 5,
            })
    return report


def evaluate_image(api_url: str, image_path: Path) -> dict[str, Any]:
    with image_path.open("rb") as image_file:
        started_at = time.perf_counter()
        response = httpx.post(
            f"{api_url}/api/discover",
            files={"image_file": (image_path.name, image_file, "image/jpeg")},
            timeout=120,
        )
    response.raise_for_status()
    latency_ms = round((time.perf_counter() - started_at) * 1000, 1)
    payload = response.json()
    return {
        "image": image_path.name,
        "latency_ms": latency_ms,
        "top_5": [
            {
                "product_name": product.get("product_name"),
                "category": product.get("category"),
                "product_type": product.get("subcategory") or product.get("normalized_subcategory"),
                "similarity": product.get("similarity_score"),
                "final_score": product.get("final_score"),
            }
            for product in payload.get("best_matches", payload.get("results", []))[:5]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate live UNFOUND search quality.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    output: dict[str, Any] = {"text_queries": evaluate_text_queries(args.api_url.rstrip("/"), args.queries)}
    if args.image:
        output["image_search"] = evaluate_image(args.api_url.rstrip("/"), args.image)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
