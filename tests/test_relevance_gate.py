"""Relevance + latency gate. Slow: needs live Supabase and the CLIP model.

Runs the committed baseline's config over the committed baseline's query set
and fails if nDCG@10 falls below (baseline - tolerance), both read from
backend/eval/BASELINE.md. Excluded from the default `pytest` run; CI runs it
with `pytest -m slow`. Set REQUIRE_RELEVANCE_GATE=1 (CI does) to turn a missing
credential into a failure instead of a skip, so the gate can't be skipped silently.
"""

import os
import statistics
import time
from pathlib import Path

import pytest

from backend.eval.baseline import latency_ceilings, load_baseline, ndcg_floor

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[1]


def _p95(values):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


@pytest.fixture(scope="module")
def gate_run():
    from dotenv import load_dotenv

    load_dotenv(ROOT / "backend" / ".env", override=False)
    if not (os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY"))):
        if os.getenv("REQUIRE_RELEVANCE_GATE") == "1":
            pytest.fail("REQUIRE_RELEVANCE_GATE=1 but Supabase credentials are not configured")
        pytest.skip("Supabase credentials not configured")

    from sentence_transformers import SentenceTransformer

    from backend.app import CLIP_MODEL_NAME, _get_required_env
    from backend.eval.cli import _build_real_search_fn
    from backend.eval.queryset import load_and_validate_queryset
    from backend.eval.runner import run_evaluation
    from backend.supabase_compat import create_supabase_client

    baseline = load_baseline()
    supabase = create_supabase_client(_get_required_env("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"))
    model = SentenceTransformer(CLIP_MODEL_NAME)
    config = baseline["config"]
    inner = _build_real_search_fn(
        supabase, model, retrieval_mode=config["retrieval_mode"],
        rrf_weights=config["rrf_weights"], rerank_mode=config["rerank_mode"],
    )
    latencies_ms: list[float] = []

    def timed(case, k):
        start = time.perf_counter()
        try:
            return inner(case, k)
        finally:
            latencies_ms.append((time.perf_counter() - start) * 1000)

    cases = load_and_validate_queryset(ROOT / baseline["queryset"], supabase)
    warm = cases[0]
    inner(warm, 20)  # exclude one-time model/JIT warmup from latency
    run = run_evaluation(cases, timed, k=20, queryset_path=baseline["queryset"])
    return baseline, run, latencies_ms


def test_ndcg_at_10_does_not_regress_below_baseline_floor(gate_run):
    baseline, run, _ = gate_run
    floor = ndcg_floor(baseline)
    actual = run["summary"]["mean_ndcg_at_10"]
    assert actual >= floor, (
        f"nDCG@10 {actual:.4f} fell below the floor {floor:.4f} "
        f"(baseline {baseline['metrics']['mean_ndcg_at_10']} - tolerance {baseline['ndcg_tolerance']}). "
        "If this drop is intended, update backend/eval/BASELINE.md in the same change."
    )


def test_zero_result_rate_does_not_regress(gate_run):
    baseline, run, _ = gate_run
    assert run["summary"]["zero_result_rate"] <= baseline["metrics"]["zero_result_rate"] + 0.05


def test_latency_stays_under_committed_ceiling(gate_run):
    baseline, _, latencies_ms = gate_run
    ceilings = latency_ceilings(baseline)
    if ceilings["p50"] is None and ceilings["p95"] is None:
        pytest.skip("no latency ceiling committed yet (latency_ceiling_ms is null in BASELINE.md)")
    if ceilings["p50"] is not None:
        assert statistics.median(latencies_ms) <= ceilings["p50"]
    if ceilings["p95"] is not None:
        assert _p95(latencies_ms) <= ceilings["p95"]
