import json

import pytest

from backend.eval.queryset import parse_queryset
from backend.eval.runner import (
    NDCG_CUTOFF,
    WORST_QUERIES_COUNT,
    evaluate_query,
    format_report,
    run_evaluation,
    write_run_json,
)


def _product(product_id, **overrides):
    row = {
        "id": product_id,
        "product_name": f"Product {product_id}",
        "brand_name": "TestBrand",
        "category": "Women Tops",
        "audience": "WOMEN",
        "price": 999.0,
    }
    row.update(overrides)
    return row


def _case(**overrides):
    defaults = dict(id="q001", query="black top", relevant={"prod-1": 2, "prod-2": 1})
    defaults.update(overrides)
    return parse_queryset([defaults])[0]


def _stub_search_fn(results_by_query_id):
    def search_fn(case, k):
        return results_by_query_id.get(case.id, [])[:k]
    return search_fn


# --- evaluate_query -----------------------------------------------------------

def test_evaluate_query_perfect_ranking_scores_maximally():
    case = _case(relevant={"prod-1": 2, "prod-2": 1})
    search_fn = _stub_search_fn({"q001": [_product("prod-1"), _product("prod-2")]})
    result = evaluate_query(case, search_fn, k=20)
    assert result.ndcg_at_10 == pytest.approx(1.0)
    assert result.recall_at_20 == pytest.approx(1.0)
    assert result.reciprocal_rank == pytest.approx(1.0)
    assert result.first_relevant_rank == 1


def test_evaluate_query_no_relevant_results_scores_zero():
    case = _case(relevant={"prod-1": 2})
    search_fn = _stub_search_fn({"q001": [_product("other-1"), _product("other-2")]})
    result = evaluate_query(case, search_fn, k=20)
    assert result.ndcg_at_10 == 0.0
    assert result.recall_at_20 == 0.0
    assert result.reciprocal_rank == 0.0
    assert result.first_relevant_rank is None


def test_evaluate_query_handles_zero_results_from_search():
    case = _case(relevant={"prod-1": 2})
    search_fn = _stub_search_fn({"q001": []})
    result = evaluate_query(case, search_fn, k=20)
    assert result.result_ids == []
    assert result.ndcg_at_10 == 0.0
    assert result.top_results == []


def test_evaluate_query_truncates_results_to_k():
    case = _case(relevant={"relevant-item": 2})
    products = [_product(f"filler-{i}") for i in range(25)] + [_product("relevant-item")]
    search_fn = _stub_search_fn({"q001": products})
    result = evaluate_query(case, search_fn, k=20)
    assert len(result.result_ids) == 20
    # relevant-item sits at index 25, beyond the k=20 cutoff -> not found
    assert result.first_relevant_rank is None


def test_evaluate_query_keeps_top_5_for_diagnosis_regardless_of_k():
    case = _case(relevant={"prod-1": 2})
    products = [_product(f"prod-{i}") for i in range(20)]
    search_fn = _stub_search_fn({"q001": products})
    result = evaluate_query(case, search_fn, k=20)
    assert len(result.top_results) == 5
    assert result.top_results[0]["id"] == "prod-0"


# --- run_evaluation -------------------------------------------------------------

def test_run_evaluation_aggregates_across_queries():
    cases = [
        _case(id="q001", query="perfect", relevant={"prod-1": 2}),
        _case(id="q002", query="miss", relevant={"prod-1": 2}),
    ]
    search_fn = _stub_search_fn({
        "q001": [_product("prod-1")],
        "q002": [_product("other")],
    })
    run = run_evaluation(cases, search_fn, k=20, queryset_path="fixture.json")
    assert run["summary"]["num_queries"] == 2
    assert run["summary"]["mean_ndcg_at_10"] == pytest.approx(0.5)
    assert run["summary"]["mrr"] == pytest.approx(0.5)
    assert run["summary"]["zero_result_rate"] == 0.0
    assert run["summary"]["queryset_path"] == "fixture.json"


def test_run_evaluation_zero_result_rate_reflects_empty_searches():
    cases = [
        _case(id="q001", query="found", relevant={"prod-1": 2}),
        _case(id="q002", query="empty", relevant={"prod-1": 2}),
    ]
    search_fn = _stub_search_fn({"q001": [_product("prod-1")], "q002": []})
    run = run_evaluation(cases, search_fn, k=20)
    assert run["summary"]["zero_result_rate"] == pytest.approx(0.5)


def test_run_evaluation_rejects_empty_case_list():
    with pytest.raises(ValueError, match="non-empty"):
        run_evaluation([], _stub_search_fn({}), k=20)


def test_run_evaluation_worst_queries_are_lowest_ndcg_first():
    cases = [
        _case(id="q_good", query="good", relevant={"prod-1": 2}),
        _case(id="q_bad", query="bad", relevant={"prod-1": 2}),
    ]
    search_fn = _stub_search_fn({
        "q_good": [_product("prod-1")],
        "q_bad": [_product("nope")],
    })
    run = run_evaluation(cases, search_fn, k=20)
    assert run["worst_queries"][0]["id"] == "q_bad"
    assert run["worst_queries"][0]["ndcg_at_10"] == 0.0


def test_run_evaluation_caps_worst_queries_at_ten():
    cases = [_case(id=f"q{i}", query=f"query {i}", relevant={"prod-1": 2}) for i in range(15)]
    search_fn = _stub_search_fn({f"q{i}": [] for i in range(15)})
    run = run_evaluation(cases, search_fn, k=20)
    assert len(run["worst_queries"]) == WORST_QUERIES_COUNT


def test_run_evaluation_output_is_json_serializable():
    cases = [_case()]
    search_fn = _stub_search_fn({"q001": [_product("prod-1")]})
    run = run_evaluation(cases, search_fn, k=20)
    json.dumps(run)  # raises if anything non-serializable slipped in


# --- format_report ----------------------------------------------------------------

def test_format_report_includes_summary_and_worst_queries():
    cases = [_case(id="q001", query="black top", relevant={"prod-1": 2})]
    search_fn = _stub_search_fn({"q001": [_product("other")]})
    run = run_evaluation(cases, search_fn, k=20)
    report = format_report(run)
    assert "Mean nDCG@10" in report
    assert "black top" in report
    assert "worst-performing queries" in report


def test_format_report_flags_zero_results_explicitly():
    cases = [_case(id="q001", query="empty query", relevant={"prod-1": 2})]
    search_fn = _stub_search_fn({"q001": []})
    run = run_evaluation(cases, search_fn, k=20)
    report = format_report(run)
    assert "(zero results returned)" in report


# --- write_run_json -----------------------------------------------------------------

def test_write_run_json_writes_timestamped_file(tmp_path):
    cases = [_case()]
    search_fn = _stub_search_fn({"q001": [_product("prod-1")]})
    run = run_evaluation(cases, search_fn, k=20)
    path = write_run_json(run, output_dir=tmp_path)
    assert path.exists()
    assert path.parent == tmp_path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["summary"]["num_queries"] == 1


def test_ndcg_cutoff_constant_matches_documented_default():
    assert NDCG_CUTOFF == 10
