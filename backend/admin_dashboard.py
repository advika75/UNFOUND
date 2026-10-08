"""Pure computation for the /admin quality dashboard.

Every function here takes already-fetched rows (or already-read log/run files)
and returns JSON-serializable data -- no Supabase client and no HTTP live here,
the same separation catalog_quality.py already uses, so this stays unit
testable without a database or a running server.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.category_quality import is_generic_fallback_name

CONFIDENCE_BUCKETS = [
    (0.0, 0.5, "0.0-0.5"),
    (0.5, 0.6, "0.5-0.6"),
    (0.6, 0.7, "0.6-0.7"),
    (0.7, 0.8, "0.7-0.8"),
    (0.8, 0.9, "0.8-0.9"),
    (0.9, 1.01, "0.9-1.0"),
]


def confidence_distribution(products: list[dict[str, Any]]) -> dict[str, Any]:
    missing = 0
    buckets = {label: 0 for *_bounds, label in CONFIDENCE_BUCKETS}
    for row in products:
        raw = row.get("classifier_confidence")
        if raw is None:
            missing += 1
            continue
        value = float(raw)
        for low, high, label in CONFIDENCE_BUCKETS:
            if low <= value < high:
                buckets[label] += 1
                break
    return {"buckets": buckets, "missing_confidence": missing, "total": len(products)}


def caption_like_names(products: list[dict[str, Any]], *, limit: int = 25) -> dict[str, Any]:
    flagged = [
        {"id": row.get("id"), "product_name": row.get("product_name"), "brand_id": row.get("brand_id")}
        for row in products
        if is_generic_fallback_name(str(row.get("product_name") or ""))
    ]
    return {"count": len(flagged), "examples": flagged[:limit]}


def category_audience_breakdown(
    products: list[dict[str, Any]], category_name_by_id: dict[Any, str]
) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in products:
        category = category_name_by_id.get(row.get("category_id"), "Uncategorized")
        audience = str(row.get("audience") or "unspecified").lower()
        counts[(category, audience)] += 1
    return sorted(
        ({"category": c, "audience": a, "count": n} for (c, a), n in counts.items()),
        key=lambda row: -row["count"],
    )


def brand_category_breakdown(brands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(row.get("category") or "Uncategorized") for row in brands)
    return sorted(({"category": c, "count": n} for c, n in counts.items()), key=lambda row: -row["count"])


# Only real chronological harness runs (UTC-timestamp filenames, e.g.
# 20260923T172832Z.json) are read as a trend -- named one-off experiment
# snapshots that already live in this directory (baseline.json,
# hybrid_equal.json, rr_vector_structured.json, ...) are different,
# non-sequential configs and would corrupt an "over time" line if included.
EVAL_RUN_FILENAME_PATTERN = re.compile(r"^\d{8}T\d{6}Z\.json$")
NDCG_CHANGE_NOTE_THRESHOLD = 0.005


def read_eval_run_history(runs_dir: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    if not runs_dir.exists():
        return []
    rows = []
    for path in sorted(runs_dir.glob("*.json")):
        if not EVAL_RUN_FILENAME_PATTERN.match(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        summary = data.get("summary", {})
        rows.append(
            {
                "file": path.name,
                "generated_at": summary.get("generated_at"),
                "num_queries": summary.get("num_queries"),
                "mean_ndcg_at_10": summary.get("mean_ndcg_at_10"),
                "mean_recall_at_20": summary.get("mean_recall_at_20"),
                "mean_precision_at_10": summary.get("mean_precision_at_10"),
                "mrr": summary.get("mrr"),
                "zero_result_rate": summary.get("zero_result_rate"),
            }
        )
    rows.sort(key=lambda row: row["generated_at"] or row["file"])

    # Annotate what changed vs. the previous run -- inferred only from the
    # numbers actually in the run files, never fabricated commentary.
    annotated = []
    previous: dict[str, Any] | None = None
    for row in rows:
        notes = []
        if previous:
            if row["num_queries"] != previous["num_queries"]:
                notes.append(f"queryset size {previous['num_queries']}→{row['num_queries']}")
            delta = (row["mean_ndcg_at_10"] or 0) - (previous["mean_ndcg_at_10"] or 0)
            if abs(delta) >= NDCG_CHANGE_NOTE_THRESHOLD:
                notes.append(f"nDCG@10 {delta:+.3f}")
        annotated.append({**row, "change_note": "; ".join(notes) or None})
        previous = row
    return annotated[-limit:]


INGESTION_FAILURE_OUTCOMES = {"FAILED_IMAGE"}
INGESTION_SUCCESS_OUTCOMES = {"INSERTED", "UPDATED"}


def read_ingestion_runs(audit_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """One row per batch audit file (backend/ingestion_batches.append_audit),
    read as one "ingestion run". A file with no parseable rows is skipped
    rather than reported as a zero-activity run."""
    if not audit_dir.exists():
        return []
    runs = []
    for path in sorted(audit_dir.glob("*.jsonl")):
        outcomes: Counter[str] = Counter()
        timestamps = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            outcomes[row.get("outcome") or "UNKNOWN"] += 1
            if row.get("timestamp"):
                timestamps.append(row["timestamp"])
        if not timestamps:
            continue
        runs.append(
            {
                "batch_id": path.stem,
                "started_at": min(timestamps),
                "finished_at": max(timestamps),
                "products_added": sum(outcomes.get(o, 0) for o in INGESTION_SUCCESS_OUTCOMES),
                "failures": sum(outcomes.get(o, 0) for o in INGESTION_FAILURE_OUTCOMES),
                "outcomes": dict(outcomes),
            }
        )
    runs.sort(key=lambda row: row["finished_at"], reverse=True)
    return runs[:limit]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def search_analytics(rows: list[dict[str, Any]], *, top_n: int = 20) -> dict[str, Any]:
    if not rows:
        return {
            "total_queries": 0,
            "top_queries": [],
            "zero_result_queries": [],
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "cache_hit_rate": None,
        }
    query_counts = Counter(row.get("query", "") for row in rows)
    zero_result_counts = Counter(row.get("query", "") for row in rows if row.get("result_count") == 0)
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    cache_flags = [bool(row["cache_hit"]) for row in rows if "cache_hit" in row]
    return {
        "total_queries": len(rows),
        "top_queries": [{"query": query, "count": count} for query, count in query_counts.most_common(top_n)],
        "zero_result_queries": [
            {"query": query, "count": count} for query, count in zero_result_counts.most_common(top_n)
        ],
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "cache_hit_rate": (sum(cache_flags) / len(cache_flags)) if cache_flags else None,
    }
