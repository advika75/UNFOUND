from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import urlparse

from dotenv import load_dotenv

from backend.supabase_compat import create_supabase_client


MIN_REASONABLE_PRICE = float(os.getenv("CATALOG_MIN_REASONABLE_PRICE", "100"))
MAX_REASONABLE_PRICE = float(os.getenv("CATALOG_MAX_REASONABLE_PRICE", "500000"))
READY_MIN_PRODUCTS = int(os.getenv("CATALOG_READY_MIN_PRODUCTS", "100"))
READY_IMAGE_COVERAGE = float(os.getenv("CATALOG_READY_IMAGE_COVERAGE", "90"))
READY_CATEGORY_COVERAGE = float(os.getenv("CATALOG_READY_CATEGORY_COVERAGE", "90"))
READY_EMBEDDING_COVERAGE = float(os.getenv("CATALOG_READY_EMBEDDING_COVERAGE", "95"))
NOT_READY_MIN_PRODUCTS = int(os.getenv("CATALOG_NOT_READY_MIN_PRODUCTS", "10"))

ISSUE_CODES = {
    "MISSING_NAME", "MISSING_IMAGE", "MISSING_CATEGORY", "MISSING_EMBEDDING",
    "INVALID_PRICE", "SUSPICIOUS_PRICE", "DUPLICATE_CANDIDATE", "MISSING_BRAND",
    "INVALID_SOURCE_URL",
}


def present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def product_name(product: dict[str, Any]) -> str:
    return str(product.get("product_name") or product.get("item_name") or product.get("name") or "").strip()


def valid_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def has_embedding(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return len(value) == 512
    if isinstance(value, str):
        stripped = value.strip(" []")
        return bool(stripped) and len(stripped.split(",")) == 512
    return False


def price_status(value: Any) -> str:
    if value is None or value == "":
        return "MISSING"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "INVALID"
    if price <= 0:
        return "INVALID"
    if price < MIN_REASONABLE_PRICE or price > MAX_REASONABLE_PRICE:
        return "SUSPICIOUS"
    return "VALID"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def duplicate_groups(products: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    signals = (
        ("same_instagram_post", lambda p: p.get("instagram_post_id")),
        ("same_source_url", lambda p: p.get("source_url")),
        ("same_product_url", lambda p: p.get("product_url")),
        ("same_brand_normalized_name", lambda p: f"{p.get('brand_id')}:{normalize_name(product_name(p))}" if p.get("brand_id") and product_name(p) else None),
        ("same_image_url", lambda p: p.get("image_url")),
    )
    groups: list[dict[str, Any]] = []
    seen_memberships: set[tuple[str, tuple[str, ...]]] = set()
    rows = list(products)
    for reason, key_fn in signals:
        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = key_fn(row)
            if present(key):
                by_key[str(key).strip().lower()].append(row)
        for key, matches in by_key.items():
            if len(matches) < 2:
                continue
            ids = tuple(sorted(str(item.get("id") or item.get("product_url") or id(item)) for item in matches))
            membership = (reason, ids)
            if membership in seen_memberships:
                continue
            seen_memberships.add(membership)
            groups.append({
                "brand": matches[0].get("brand_name"),
                "brand_id": matches[0].get("brand_id"),
                "reason": reason,
                "match_value": key,
                "products": matches,
            })
    return groups


def product_warnings(product: dict[str, Any], duplicate_ids: set[str] | None = None) -> list[str]:
    warnings: list[str] = []
    if not product_name(product):
        warnings.append("MISSING_NAME")
    if not valid_http_url(product.get("image_url")):
        warnings.append("MISSING_IMAGE")
    if not present(product.get("category_id")):
        warnings.append("MISSING_CATEGORY")
    if not has_embedding(product.get("embedding")):
        warnings.append("MISSING_EMBEDDING")
    status = price_status(product.get("price"))
    if status == "INVALID":
        warnings.append("INVALID_PRICE")
    elif status == "SUSPICIOUS":
        warnings.append("SUSPICIOUS_PRICE")
    if not present(product.get("brand_id")):
        warnings.append("MISSING_BRAND")
    source_url = product.get("product_url") or product.get("source_url")
    if not valid_http_url(source_url):
        warnings.append("INVALID_SOURCE_URL")
    if duplicate_ids and str(product.get("id")) in duplicate_ids:
        warnings.append("DUPLICATE_CANDIDATE")
    return warnings


def catalog_quality_score(product: dict[str, Any]) -> int:
    score = 0
    score += 20 if product_name(product) else 0
    score += 20 if valid_http_url(product.get("image_url")) else 0
    score += 15 if present(product.get("category_id")) else 0
    score += 15 if present(product.get("brand_id")) else 0
    score += 20 if has_embedding(product.get("embedding")) else 0
    score += 5 if valid_http_url(product.get("product_url") or product.get("source_url")) else 0
    if product.get("price") in (None, ""):
        score += 5
    elif price_status(product.get("price")) == "VALID":
        score += 5
    return min(score, 100)


def image_status(url: Any, *, network_check: bool = False, timeout: float = 5.0) -> str:
    if not present(url):
        return "MISSING"
    if not valid_http_url(url):
        return "BROKEN"
    if not network_check:
        return "UNKNOWN"
    import httpx
    try:
        response = httpx.head(str(url), timeout=timeout, follow_redirects=True)
        if response.status_code in {403, 405}:
            response = httpx.get(str(url), headers={"Range": "bytes=0-1023"}, timeout=timeout, follow_redirects=True)
        return "VALID" if response.status_code < 400 else "BROKEN"
    except httpx.TimeoutException:
        return "TIMEOUT"
    except Exception:
        return "UNKNOWN"


def readiness_status(summary: dict[str, Any]) -> str:
    if summary["total_products"] < NOT_READY_MIN_PRODUCTS or summary["image_coverage_percent"] < 50 or summary["embedding_coverage_percent"] < 50:
        return "NOT_READY"
    if (
        summary["total_products"] >= READY_MIN_PRODUCTS
        and summary["image_coverage_percent"] >= READY_IMAGE_COVERAGE
        and summary["category_coverage_percent"] >= READY_CATEGORY_COVERAGE
        and summary["embedding_coverage_percent"] >= READY_EMBEDDING_COVERAGE
    ):
        return "READY"
    return "NEEDS_ATTENTION"


def build_catalog_report(brands: Iterable[dict[str, Any]], products: Iterable[dict[str, Any]], failures: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    brand_rows, product_rows, failure_rows = list(brands), list(products), list(failures)
    duplicates = duplicate_groups(product_rows)
    duplicate_ids = {str(row.get("id")) for group in duplicates for row in group["products"] if row.get("id") is not None}
    issues = []
    for row in product_rows:
        warnings = product_warnings(row, duplicate_ids)
        if warnings:
            issues.append({"product": row, "warnings": warnings, "catalog_quality_score": catalog_quality_score(row)})
    product_brand_ids = {row.get("brand_id") for row in product_rows if row.get("brand_id")}
    total = len(product_rows)
    count = lambda predicate: sum(1 for row in product_rows if predicate(row))
    percentage = lambda value: round((value / total * 100), 1) if total else 0.0
    with_images = count(lambda row: valid_http_url(row.get("image_url")))
    with_price = count(lambda row: price_status(row.get("price")) in {"VALID", "SUSPICIOUS"})
    with_category = count(lambda row: present(row.get("category_id")))
    with_embedding = count(lambda row: has_embedding(row.get("embedding")))
    summary = {
        "total_brands": len(brand_rows), "total_products": total,
        "brands_with_products": sum(1 for row in brand_rows if row.get("id") in product_brand_ids),
        "brands_without_products": sum(1 for row in brand_rows if row.get("id") not in product_brand_ids),
        "products_with_images": with_images, "products_without_images": total - with_images,
        "products_with_price": with_price, "products_without_price": total - with_price,
        "products_with_category": with_category, "products_without_category": total - with_category,
        "products_with_embedding": with_embedding, "products_without_embedding": total - with_embedding,
        "possible_duplicates": len(duplicates),
        "suspicious_price_count": count(lambda row: price_status(row.get("price")) == "SUSPICIOUS"),
        "products_with_issues": len(issues),
        "image_coverage_percent": percentage(with_images), "price_coverage_percent": percentage(with_price),
        "category_coverage_percent": percentage(with_category), "embedding_coverage_percent": percentage(with_embedding),
    }
    summary["catalog_status"] = readiness_status(summary)
    return {**summary, "issues": issues, "duplicate_groups": duplicates, "brand_health": build_brand_health(brand_rows, product_rows, failure_rows)}


def build_brand_health(brands: list[dict[str, Any]], products: list[dict[str, Any]], failures: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    by_brand: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for product in products:
        by_brand[product.get("brand_id")].append(product)
    failed_ids = {row.get("brand_id") for row in failures or [] if row.get("brand_id")}
    output = []
    for brand in brands:
        rows = by_brand.get(brand.get("id"), [])
        count = len(rows)
        images = sum(valid_http_url(row.get("image_url")) for row in rows)
        embeddings = sum(has_embedding(row.get("embedding")) for row in rows)
        categories = sum(present(row.get("category_id")) for row in rows)
        avg = round(sum(catalog_quality_score(row) for row in rows) / count, 1) if count else 0.0
        coverage = min(images, embeddings, categories) / count * 100 if count else 0
        if brand.get("id") in failed_ids and count == 0:
            health = "FAILED"
        elif count == 0:
            health = "EMPTY"
        elif count >= 3 and coverage >= 90 and avg >= 85:
            health = "HEALTHY"
        else:
            health = "PARTIAL"
        output.append({
            "brand_id": brand.get("id"), "brand_name": brand.get("name") or brand.get("brand_name"),
            "product_count": count, "products_with_images": images, "products_with_embeddings": embeddings,
            "products_with_categories": categories, "average_catalog_quality_score": avg,
            "last_product_ingested_at": max((row.get("scraped_at") or row.get("created_at") or "" for row in rows), default=None),
            "ingestion_health": health,
        })
    return output


def load_catalog(supabase: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    def fetch_all(table_name: str, page_size: int = 1000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            request = supabase.table(table_name).select("*")
            if not hasattr(request, "range"):
                return request.execute().data or []
            page = request.range(start, start + page_size - 1).execute().data or []
            rows.extend(page)
            if len(page) < page_size:
                return rows
            start += page_size

    brands = fetch_all("brands")
    products = fetch_all("products")
    try:
        failures = fetch_all("ingestion_failures")
    except Exception:
        failures = []
    return brands, products, failures


def print_report(report: dict[str, Any]) -> None:
    print("=" * 37, "GEMMODE CATALOG HEALTH", "=" * 37, sep="\n")
    print(f"Brands:                  {report['total_brands']}")
    print(f"Products:                {report['total_products']}")
    print(f"Brands with products:    {report['brands_with_products']}")
    print(f"Brands without products: {report['brands_without_products']}")
    print(f"Image coverage:          {report['image_coverage_percent']}%")
    print(f"Category coverage:       {report['category_coverage_percent']}%")
    print(f"Embedding coverage:      {report['embedding_coverage_percent']}%")
    print(f"Price coverage:          {report['price_coverage_percent']}%")
    print(f"Possible duplicates:     {report['possible_duplicates']}")
    print(f"Products with issues:    {report['products_with_issues']}")
    print(f"\nCATALOG STATUS: {report['catalog_status']}\n" + "=" * 37)


def main() -> None:
    parser = argparse.ArgumentParser(description="GemMode catalog quality validator")
    parser.add_argument("command", choices=["catalog-health", "issues", "brands-health", "failed-brands"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv("backend/.env", override=True)
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_ANON_KEY are required in backend/.env")
    brands, products, failures = load_catalog(create_supabase_client(url, key))
    report = build_catalog_report(brands, products, failures)
    value = {"issues": report["issues"]} if args.command == "issues" else report["brand_health"] if args.command == "brands-health" else failures if args.command == "failed-brands" else report
    if args.json or args.command != "catalog-health":
        print(json.dumps(value, indent=2, default=str))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
