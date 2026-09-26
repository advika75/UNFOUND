"""CLI for the offline relevance evaluation harness.

    python -m backend.eval.cli run --queryset <path> [--k 20] [--output-dir <dir>]
    python -m backend.eval.cli compare <run_a.json> <run_b.json>

`run` wires backend.app's real search seam (encode_text -> extract_query_attributes
-> run_match_products_rpc, the seam identified in the search investigation
report) into backend.eval.runner.run_evaluation. It needs backend/.env
(Supabase credentials) and downloads/loads the CLIP model, so it is not
covered by unit tests here -- backend/eval/runner.py already covers the
scoring logic in isolation via a stubbed search_fn.

`compare` is pure JSON-diffing: no DB, no model, no network. It reports
per-metric deltas between two run files and -- the signal that matters most --
which individual queries regressed (nDCG@10 dropped) from run A to run B. It
is fully unit-tested.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from backend.eval.queryset import load_and_validate_queryset
from backend.eval.runner import DEFAULT_K, SearchFn, print_report, run_evaluation, write_run_json

COMPARE_METRICS = (
    "mean_ndcg_at_10",
    "mean_recall_at_20",
    "mean_precision_at_10",
    "mrr",
    "zero_result_rate",
)


def _build_real_search_fn(
    supabase: Any,
    clip_model: Any,
    *,
    retrieval_mode: str = "vector",
    rrf_weights: list[float] | None = None,
    rerank_mode: str = "off",
) -> SearchFn:
    from backend.app import encode_text, extract_query_attributes, run_match_products_rpc

    def search_fn(case, k):
        query_embedding = encode_text(clip_model, case.query)
        query_attributes = extract_query_attributes(case.query)
        return run_match_products_rpc(
            supabase,
            query_embedding,
            category_id=None,
            search_mode="text",
            query_attributes=query_attributes,
            match_count=max(k, DEFAULT_K),
            result_limit=k,
            retrieval_mode=retrieval_mode,
            query_text=case.query,
            rrf_weights=rrf_weights,
            rerank_mode=rerank_mode,
        )

    return search_fn


def _run_command(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv("backend/.env", override=True)

    from sentence_transformers import SentenceTransformer

    from backend.app import CLIP_MODEL_NAME, _get_required_env
    from backend.supabase_compat import create_supabase_client

    supabase = create_supabase_client(
        _get_required_env("SUPABASE_URL"),
        os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"),
    )
    clip_model = SentenceTransformer(CLIP_MODEL_NAME)

    rrf_weights = [float(w) for w in args.rrf_weights.split(",")] if args.rrf_weights else None

    cases = load_and_validate_queryset(args.queryset, supabase)
    search_fn = _build_real_search_fn(
        supabase, clip_model, retrieval_mode=args.retrieval_mode, rrf_weights=rrf_weights,
        rerank_mode=args.rerank,
    )

    run = run_evaluation(cases, search_fn, k=args.k, queryset_path=str(args.queryset))
    print_report(run)

    output_dir = Path(args.output_dir) if args.output_dir else None
    path = write_run_json(run, output_dir=output_dir)
    print(f"\nWrote run JSON to {path}")
    return 0


def _load_run(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_runs(run_a: dict[str, Any], run_b: dict[str, Any]) -> str:
    """Pure diff of two run reports. Returns the printable comparison text."""
    lines = [
        f"{'Metric':<22}{'A':>10}{'B':>10}{'Delta':>10}",
        "-" * 52,
    ]
    for metric in COMPARE_METRICS:
        a_value = float(run_a["summary"].get(metric, 0.0))
        b_value = float(run_b["summary"].get(metric, 0.0))
        lines.append(f"{metric:<22}{a_value:>10.4f}{b_value:>10.4f}{b_value - a_value:>+10.4f}")

    a_scores = {row["id"]: row["ndcg_at_10"] for row in run_a["per_query"] if row["ndcg_at_10"] is not None}
    b_scores = {row["id"]: row["ndcg_at_10"] for row in run_b["per_query"] if row["ndcg_at_10"] is not None}
    common_ids = set(a_scores) & set(b_scores)

    regressed = sorted(
        (
            (query_id, a_scores[query_id], b_scores[query_id])
            for query_id in common_ids
            if b_scores[query_id] < a_scores[query_id]
        ),
        key=lambda row: row[2] - row[1],
    )

    lines.append("")
    lines.append(f"{len(regressed)} regressed quer{'y' if len(regressed) == 1 else 'ies'} (nDCG@10 dropped from A to B):")
    for query_id, a_value, b_value in regressed:
        lines.append(f"  {query_id}: {a_value:.3f} -> {b_value:.3f}  ({b_value - a_value:+.3f})")

    only_in_a = sorted(set(a_scores) - set(b_scores))
    only_in_b = sorted(set(b_scores) - set(a_scores))
    if only_in_a:
        lines.append(f"\nQueries only in A (missing from B): {', '.join(only_in_a)}")
    if only_in_b:
        lines.append(f"\nQueries only in B (new in B): {', '.join(only_in_b)}")

    return "\n".join(lines)


def _compare_command(args: argparse.Namespace) -> int:
    run_a = _load_run(args.run_a)
    run_b = _load_run(args.run_b)
    print(f"Comparing {args.run_a} (A) vs {args.run_b} (B)\n")
    print(compare_runs(run_a, run_b))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.eval.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the harness against a query set and score it.")
    run_parser.add_argument("--queryset", required=True, help="Path to a query set JSON file.")
    run_parser.add_argument("--k", type=int, default=DEFAULT_K, help=f"Candidate pool depth (default {DEFAULT_K}).")
    run_parser.add_argument("--output-dir", default=None, help="Directory for the run JSON (default backend/eval/runs).")
    run_parser.add_argument("--retrieval-mode", dest="retrieval_mode", choices=["vector", "hybrid"], default="vector", help="Which retrieval path to evaluate (default vector).")
    run_parser.add_argument("--rerank", choices=["off", "structured", "gated"], default="off", help="Reranking stage (default off).")
    run_parser.add_argument("--rrf-weights", dest="rrf_weights", default=None, help="Comma-separated [vector_weight,lexical_weight] for hybrid mode, e.g. '2,1'. Defaults to equal weights.")
    run_parser.set_defaults(func=_run_command)

    compare_parser = subparsers.add_parser("compare", help="Compare two run JSON files; lists regressed queries.")
    compare_parser.add_argument("run_a")
    compare_parser.add_argument("run_b")
    compare_parser.set_defaults(func=_compare_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
