"""Offline relevance evaluation runner.

Ties backend/eval/queryset.py (query cases) to backend/eval/metrics.py (pure
metric functions) via a single `search_fn` callable. This module never talks
to Supabase or the search stack directly — the caller (backend/eval/cli.py
for real runs, or a stub in tests) supplies `search_fn`, which is what makes
the runner fully unit-testable without a database.

`search_fn(case, k) -> list[dict]` must return a ranked list of product
dicts, each with at least an "id" field (the same shape backend.app's
run_match_products_rpc already returns — the seam identified in the search
investigation report). Richer fields (product_name, brand_name, category,
audience, price) are used only for the human-readable worst-query diagnosis;
their absence does not break metric computation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.eval.metrics import (
    ndcg_at_k,
    precision_at_k,
    reciprocal_rank,
    recall_at_k,
    zero_result_rate,
)
from backend.eval.metrics import mrr as aggregate_mrr
from backend.eval.queryset import QueryCase

SearchFn = Callable[[QueryCase, int], list[dict[str, Any]]]

DEFAULT_K = 20
NDCG_CUTOFF = 10
RECALL_CUTOFF = 20
PRECISION_CUTOFF = 10
WORST_QUERIES_COUNT = 10
DIAGNOSIS_TOP_N = 5

RUNS_DIR = Path(__file__).with_name("runs")

_DIAGNOSIS_FIELDS = ("id", "product_name", "brand_name", "category", "audience", "price")


@dataclass
class QueryEvalResult:
    id: str
    query: str
    result_ids: list[str]
    ndcg_at_10: float | None
    recall_at_20: float | None
    precision_at_10: float | None
    reciprocal_rank: float | None
    first_relevant_rank: int | None
    top_results: list[dict[str, Any]] = field(default_factory=list)
    judged_total: int | None = None
    unjudged_in_top_20: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "result_ids": self.result_ids,
            "ndcg_at_10": self.ndcg_at_10,
            "recall_at_20": self.recall_at_20,
            "precision_at_10": self.precision_at_10,
            "reciprocal_rank": self.reciprocal_rank,
            "first_relevant_rank": self.first_relevant_rank,
            "judged_total": self.judged_total,
            "unjudged_in_top_20": self.unjudged_in_top_20,
        }


def _first_relevant_rank(result_ids: list[str], relevant: dict[str, int]) -> int | None:
    for rank, product_id in enumerate(result_ids, start=1):
        if product_id in relevant:
            return rank
    return None


def _diagnosis_row(product: dict[str, Any]) -> dict[str, Any]:
    return {field_name: product.get(field_name) for field_name in _DIAGNOSIS_FIELDS}


def evaluate_query(case: QueryCase, search_fn: SearchFn, k: int = DEFAULT_K) -> QueryEvalResult:
    """Run one query through search_fn and score it against its labeled relevance."""
    raw_results = search_fn(case, k) or []
    result_ids = [str(product["id"]) for product in raw_results][:k]

    judged = set(case.judged)
    unjudged = sum(1 for pid in result_ids[:RECALL_CUTOFF] if pid not in judged) if judged else None
    top_results = [_diagnosis_row(product) for product in raw_results[:DIAGNOSIS_TOP_N]]
    if not case.relevant:
        # Judged, but nothing in the catalog was relevant: metrics are undefined, not zero.
        return QueryEvalResult(
            id=case.id, query=case.query, result_ids=result_ids, ndcg_at_10=None, recall_at_20=None,
            precision_at_10=None, reciprocal_rank=None, first_relevant_rank=None, top_results=top_results,
            judged_total=len(judged), unjudged_in_top_20=unjudged,
        )
    return QueryEvalResult(
        id=case.id,
        query=case.query,
        result_ids=result_ids,
        ndcg_at_10=ndcg_at_k(result_ids, case.relevant, k=min(NDCG_CUTOFF, k)),
        recall_at_20=recall_at_k(result_ids, case.relevant, k=min(RECALL_CUTOFF, k)),
        precision_at_10=precision_at_k(result_ids, case.relevant, k=min(PRECISION_CUTOFF, k)),
        reciprocal_rank=reciprocal_rank(result_ids, case.relevant),
        first_relevant_rank=_first_relevant_rank(result_ids, case.relevant),
        top_results=top_results,
        judged_total=len(judged) if judged else None,
        unjudged_in_top_20=unjudged,
    )


def run_evaluation(
    cases: list[QueryCase],
    search_fn: SearchFn,
    k: int = DEFAULT_K,
    queryset_path: str = "",
) -> dict[str, Any]:
    """Evaluate every query case and return a JSON-serializable run report.

    Shape: {"summary": {...aggregate metrics...}, "per_query": [...],
    "worst_queries": [...10 lowest nDCG@10 queries, with top-5 result detail...]}.
    """
    if not cases:
        raise ValueError("cases must be non-empty")

    per_query = [evaluate_query(case, search_fn, k) for case in cases]
    excluded = [r.id for r in per_query if r.ndcg_at_10 is None]
    ndcgs = [r.ndcg_at_10 for r in per_query if r.ndcg_at_10 is not None]
    recalls = [r.recall_at_20 for r in per_query if r.recall_at_20 is not None]
    precisions = [r.precision_at_10 for r in per_query if r.precision_at_10 is not None]
    rranks = [r.reciprocal_rank for r in per_query if r.reciprocal_rank is not None]
    if not ndcgs:
        raise ValueError("no query has any relevant product; nothing to score")
    unjudged = [r.unjudged_in_top_20 for r in per_query if r.unjudged_in_top_20 is not None]

    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "k": k,
        "queryset_path": queryset_path,
        "num_queries": len(per_query),
        "num_scored": len(ndcgs),
        "excluded_no_relevant": excluded,
        "mean_ndcg_at_10": sum(ndcgs) / len(ndcgs),
        "mean_recall_at_20": sum(recalls) / len(recalls),
        "mean_precision_at_10": sum(precisions) / len(precisions),
        "mrr": aggregate_mrr(rranks),
        "zero_result_rate": zero_result_rate([r.result_ids for r in per_query]),
        "mean_unjudged_in_top_20": (sum(unjudged) / len(unjudged)) if unjudged else None,
    }

    worst = sorted((r for r in per_query if r.ndcg_at_10 is not None), key=lambda r: r.ndcg_at_10 or 0.0)[:WORST_QUERIES_COUNT]

    return {
        "summary": summary,
        "per_query": [r.to_json() for r in per_query],
        "worst_queries": [{**r.to_json(), "top_results": r.top_results} for r in worst],
    }


def format_report(run: dict[str, Any]) -> str:
    """Render a run report as a plain-text table for stdout. No external deps."""
    summary = run["summary"]
    lines = [
        f"Evaluation run - {summary['num_queries']} queries, k={summary['k']}, generated {summary['generated_at']}",
        "",
        f"{'Metric':<20}{'Value':>10}",
        "-" * 30,
        f"{'Mean nDCG@10':<20}{summary['mean_ndcg_at_10']:>10.4f}",
        f"{'Mean Recall@20':<20}{summary['mean_recall_at_20']:>10.4f}",
        f"{'Mean Precision@10':<20}{summary['mean_precision_at_10']:>10.4f}",
        f"{'MRR':<20}{summary['mrr']:>10.4f}",
        f"{'Zero-result rate':<20}{summary['zero_result_rate']:>10.4f}",
        "",
        "Per-query (id, query, ndcg@10, recall@20, first relevant rank):",
    ]
    header = f"{'id':<8}{'query':<34}{'ndcg@10':>9}{'recall@20':>11}{'first_rel':>11}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in run["per_query"]:
        if row["ndcg_at_10"] is None:
            lines.append(f"{row['id']:<8}{row['query'][:32]:<34}{'n/a (no relevant products)':>31}")
            continue
        query_text = row["query"] if len(row["query"]) <= 32 else row["query"][:29] + "..."
        lines.append(
            f"{row['id']:<8}{query_text:<34}{row['ndcg_at_10']:>9.3f}"
            f"{row['recall_at_20']:>11.3f}{str(row['first_relevant_rank']):>11}"
        )

    lines.append("")
    lines.append(f"{WORST_QUERIES_COUNT} worst-performing queries (by nDCG@10):")
    for row in run["worst_queries"]:
        lines.append("")
        lines.append(
            f"  [{row['id']}] \"{row['query']}\"  ndcg@10={row['ndcg_at_10']:.3f}"
            f"  recall@20={row['recall_at_20']:.3f}  first_relevant_rank={row['first_relevant_rank']}"
        )
        if not row["top_results"]:
            lines.append("    (zero results returned)")
        for position, product in enumerate(row["top_results"], start=1):
            lines.append(
                f"    {position}. {product.get('product_name')!r}"
                f" | brand={product.get('brand_name')}"
                f" | category={product.get('category')}"
                f" | audience={product.get('audience')}"
                f" | price={product.get('price')}"
                f" | id={product.get('id')}"
            )
    return "\n".join(lines)


def print_report(run: dict[str, Any]) -> None:
    print(format_report(run))


def write_run_json(run: dict[str, Any], output_dir: Path | None = None) -> Path:
    """Write the run report to backend/eval/runs/<UTC-timestamp>.json, returning the path."""
    output_dir = output_dir or RUNS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{timestamp}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    return path
