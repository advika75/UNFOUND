import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.admin_dashboard import (
    brand_category_breakdown,
    caption_like_names,
    category_audience_breakdown,
    confidence_distribution,
    percentile,
    read_eval_run_history,
    read_ingestion_runs,
    search_analytics,
)
from backend.app import admin_dashboard, app


class _EmptyResult:
    def __init__(self, data: list) -> None:
        self.data = data


class _EmptySelect:
    def execute(self) -> _EmptyResult:
        return _EmptyResult([])


class _EmptyTable:
    def select(self, columns: str) -> _EmptySelect:
        return _EmptySelect()


class EmptySupabase:
    """No .range() method -- exercises the same "simple, non-paginated" branch
    fetch_all()/load_catalog() fall back to, on a catalog with nothing in it."""

    def table(self, name: str) -> _EmptyTable:
        return _EmptyTable()


def test_confidence_distribution_buckets_and_counts_missing() -> None:
    products = [
        {"classifier_confidence": 0.95},
        {"classifier_confidence": 0.82},
        {"classifier_confidence": 0.45},
        {"classifier_confidence": None},
        {},
    ]
    report = confidence_distribution(products)
    assert report["total"] == 5
    assert report["missing_confidence"] == 2
    assert report["buckets"]["0.9-1.0"] == 1
    assert report["buckets"]["0.8-0.9"] == 1
    assert report["buckets"]["0.0-0.5"] == 1
    assert sum(report["buckets"].values()) + report["missing_confidence"] == report["total"]


def test_confidence_distribution_handles_empty_catalog() -> None:
    report = confidence_distribution([])
    assert report["total"] == 0
    assert report["missing_confidence"] == 0
    assert all(count == 0 for count in report["buckets"].values())


def test_caption_like_names_flags_bare_category_words_only() -> None:
    products = [
        {"id": "1", "product_name": "Kurtis", "brand_id": "b1"},
        {"id": "2", "product_name": "Oversized Black Cotton Shirt", "brand_id": "b2"},
        {"id": "3", "product_name": "  jewellery  ", "brand_id": "b3"},
    ]
    report = caption_like_names(products)
    assert report["count"] == 2
    flagged_ids = {row["id"] for row in report["examples"]}
    assert flagged_ids == {"1", "3"}


def test_category_audience_breakdown_resolves_ids_and_defaults_unspecified() -> None:
    products = [
        {"category_id": 1, "audience": "WOMEN"},
        {"category_id": 1, "audience": "WOMEN"},
        {"category_id": 2, "audience": None},
        {"category_id": 999, "audience": "MEN"},
    ]
    category_name_by_id = {1: "Tops", 2: "Bags"}
    rows = category_audience_breakdown(products, category_name_by_id)
    by_key = {(row["category"], row["audience"]): row["count"] for row in rows}
    assert by_key[("Tops", "women")] == 2
    assert by_key[("Bags", "unspecified")] == 1
    assert by_key[("Uncategorized", "men")] == 1


def test_brand_category_breakdown_counts_and_defaults() -> None:
    brands = [{"category": "Women"}, {"category": "Women"}, {"category": None}]
    rows = brand_category_breakdown(brands)
    by_key = {row["category"]: row["count"] for row in rows}
    assert by_key["Women"] == 2
    assert by_key["Uncategorized"] == 1


def test_read_eval_run_history_only_reads_timestamped_files_and_sorts_chronologically(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "baseline.json").write_text(json.dumps({"summary": {"generated_at": "2020-01-01T00:00:00Z", "mean_ndcg_at_10": 0.9}}))
    (runs_dir / "20260921T154107Z.json").write_text(
        json.dumps({"summary": {"generated_at": "2026-09-21T15:41:07Z", "num_queries": 20, "mean_ndcg_at_10": 0.5}})
    )
    (runs_dir / "20260923T172832Z.json").write_text(
        json.dumps({"summary": {"generated_at": "2026-09-23T17:28:32Z", "num_queries": 25, "mean_ndcg_at_10": 0.6}})
    )
    rows = read_eval_run_history(runs_dir)
    assert [row["file"] for row in rows] == ["20260921T154107Z.json", "20260923T172832Z.json"]
    assert rows[0]["change_note"] is None
    assert "queryset size 20→25" in rows[1]["change_note"]
    assert "nDCG@10 +0.100" in rows[1]["change_note"]


def test_read_eval_run_history_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert read_eval_run_history(tmp_path / "does-not-exist") == []


def test_read_ingestion_runs_aggregates_outcomes_and_skips_empty_files(tmp_path: Path) -> None:
    audit_dir = tmp_path / "ingestion_audit"
    audit_dir.mkdir()
    (audit_dir / "batch-a.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"timestamp": "2026-09-20T00:00:00+00:00", "outcome": "INSERTED"},
                {"timestamp": "2026-09-20T00:05:00+00:00", "outcome": "UPDATED"},
                {"timestamp": "2026-09-20T00:10:00+00:00", "outcome": "FAILED_IMAGE"},
            ]
        )
    )
    (audit_dir / "empty-batch.jsonl").write_text("")
    rows = read_ingestion_runs(audit_dir)
    assert len(rows) == 1
    run = rows[0]
    assert run["batch_id"] == "batch-a"
    assert run["products_added"] == 2
    assert run["failures"] == 1
    assert run["started_at"] == "2026-09-20T00:00:00+00:00"
    assert run["finished_at"] == "2026-09-20T00:10:00+00:00"


def test_percentile_basic() -> None:
    assert percentile([], 50) is None
    assert percentile([10.0], 95) == 10.0
    values = list(range(1, 101))  # 1..100
    assert percentile([float(v) for v in values], 50) in (50.0, 51.0)


def test_search_analytics_empty_log_returns_nulls_not_crash() -> None:
    report = search_analytics([])
    assert report["total_queries"] == 0
    assert report["top_queries"] == []
    assert report["p50_latency_ms"] is None
    assert report["cache_hit_rate"] is None


def test_search_analytics_computes_top_and_zero_result_queries_and_cache_rate() -> None:
    rows = [
        {"query": "black top", "result_count": 5, "latency_ms": 100, "cache_hit": False},
        {"query": "black top", "result_count": 5, "latency_ms": 20, "cache_hit": True},
        {"query": "xyz nonsense", "result_count": 0, "latency_ms": 90, "cache_hit": False},
    ]
    report = search_analytics(rows)
    assert report["total_queries"] == 3
    assert report["top_queries"][0] == {"query": "black top", "count": 2}
    assert report["zero_result_queries"] == [{"query": "xyz nonsense", "count": 1}]
    assert report["cache_hit_rate"] == 1 / 3


def test_admin_dashboard_route_requires_admin_key(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(admin_dashboard(None))
    assert excinfo.value.status_code == 401


def test_admin_dashboard_route_handles_a_completely_empty_catalog(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    app.state.supabase = EmptySupabase()
    result = asyncio.run(admin_dashboard("test-admin-key"))
    assert result["catalog_health"]["total_products"] == 0
    assert result["catalog_health"]["total_brands"] == 0
    assert result["confidence_distribution"] == {
        "buckets": {label: 0 for *_bounds, label in [
            (0.0, 0.5, "0.0-0.5"), (0.5, 0.6, "0.5-0.6"), (0.6, 0.7, "0.6-0.7"),
            (0.7, 0.8, "0.7-0.8"), (0.8, 0.9, "0.8-0.9"), (0.9, 1.01, "0.9-1.0"),
        ]},
        "missing_confidence": 0,
        "total": 0,
    }
    assert result["caption_like_names"] == {"count": 0, "examples": []}
    assert result["category_audience_breakdown"] == []
    assert result["brand_category_breakdown"] == []
    assert result["search_analytics"]["total_queries"] >= 0
    assert isinstance(result["relevance_runs"], list)
    assert isinstance(result["ingestion_runs"], list)
