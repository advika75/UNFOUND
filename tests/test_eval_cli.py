import json

import pytest

from backend.eval.cli import (
    _compare_command,
    _run_command,
    build_parser,
    compare_runs,
    main,
)


def _run(mean_ndcg=0.5, per_query=None, **summary_overrides):
    summary = {
        "mean_ndcg_at_10": mean_ndcg,
        "mean_recall_at_20": 0.6,
        "mean_precision_at_10": 0.4,
        "mrr": 0.7,
        "zero_result_rate": 0.1,
    }
    summary.update(summary_overrides)
    return {"summary": summary, "per_query": per_query or [], "worst_queries": []}


# --- build_parser / dispatch (no DB, no model) --------------------------------

def test_build_parser_run_subcommand_defaults():
    args = build_parser().parse_args(["run", "--queryset", "qs.json"])
    assert args.command == "run"
    assert args.queryset == "qs.json"
    assert args.k == 20
    assert args.output_dir is None
    assert args.func is _run_command


def test_build_parser_run_subcommand_accepts_overrides():
    args = build_parser().parse_args(["run", "--queryset", "qs.json", "--k", "30", "--output-dir", "out/"])
    assert args.k == 30
    assert args.output_dir == "out/"


def test_build_parser_compare_subcommand():
    args = build_parser().parse_args(["compare", "a.json", "b.json"])
    assert args.command == "compare"
    assert args.run_a == "a.json"
    assert args.run_b == "b.json"
    assert args.func is _compare_command


def test_build_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


# --- compare_runs: pure diffing logic ------------------------------------------

def test_compare_runs_reports_metric_deltas():
    run_a = _run(mean_ndcg=0.50)
    run_b = _run(mean_ndcg=0.60)
    output = compare_runs(run_a, run_b)
    assert "mean_ndcg_at_10" in output
    assert "0.5000" in output and "0.6000" in output
    assert "+0.1000" in output


def test_compare_runs_lists_regressed_queries_worst_first():
    run_a = _run(per_query=[
        {"id": "q1", "ndcg_at_10": 0.9},
        {"id": "q2", "ndcg_at_10": 0.8},
        {"id": "q3", "ndcg_at_10": 0.5},
    ])
    run_b = _run(per_query=[
        {"id": "q1", "ndcg_at_10": 0.9},   # unchanged
        {"id": "q2", "ndcg_at_10": 0.3},   # regressed hard
        {"id": "q3", "ndcg_at_10": 0.4},   # regressed lightly
    ])
    output = compare_runs(run_a, run_b)
    assert "2 regressed queries" in output
    q2_line = next(line for line in output.splitlines() if line.strip().startswith("q2"))
    q3_line = next(line for line in output.splitlines() if line.strip().startswith("q3"))
    # q2's regression (-0.5) is worse than q3's (-0.1); q2 must be listed first
    assert output.index(q2_line) < output.index(q3_line)
    assert "q1" not in " ".join(line for line in output.splitlines() if "->" in line)


def test_compare_runs_reports_no_regressions_cleanly():
    run_a = _run(per_query=[{"id": "q1", "ndcg_at_10": 0.5}])
    run_b = _run(per_query=[{"id": "q1", "ndcg_at_10": 0.7}])
    output = compare_runs(run_a, run_b)
    assert "0 regressed queries" in output


def test_compare_runs_flags_singular_regressed_query():
    run_a = _run(per_query=[{"id": "q1", "ndcg_at_10": 0.9}])
    run_b = _run(per_query=[{"id": "q1", "ndcg_at_10": 0.1}])
    output = compare_runs(run_a, run_b)
    assert "1 regressed query " in output or "1 regressed query:" in output.replace("\n", ":")


def test_compare_runs_flags_queries_only_in_one_run():
    run_a = _run(per_query=[{"id": "q1", "ndcg_at_10": 0.5}])
    run_b = _run(per_query=[{"id": "q2", "ndcg_at_10": 0.5}])
    output = compare_runs(run_a, run_b)
    assert "only in A" in output and "q1" in output
    assert "only in B" in output and "q2" in output


# --- _compare_command / main(): file IO + stdout, still no DB -----------------

def test_compare_command_reads_files_and_prints(tmp_path, capsys):
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_a.write_text(json.dumps(_run(mean_ndcg=0.4)), encoding="utf-8")
    path_b.write_text(json.dumps(_run(mean_ndcg=0.6)), encoding="utf-8")

    exit_code = main(["compare", str(path_a), str(path_b)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Comparing" in output
    assert "mean_ndcg_at_10" in output
