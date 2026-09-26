import json

import pytest

from backend.eval.pool import (
    apply_judgment,
    build_query_pool,
    format_item,
    judge_items,
    pool_stats,
    queries_to_queryset,
)
from backend.eval.queryset import QuerySetError, parse_queryset, validate_product_ids_exist
from backend.eval.runner import evaluate_query, run_evaluation


def test_pool_is_union_of_topn_across_configs():
    pool = build_query_pool("q1", {"a": ["x", "y", "z"], "b": ["y", "w"]}, seed="s", depth=2)
    assert set(pool["items"]) == {"x", "y", "w"}  # z is rank 3 in a, beyond depth 2
    assert pool["provenance"]["y"] == {"a": 2, "b": 1}


def test_pool_order_is_deterministic_and_independent_of_config_order():
    a = build_query_pool("q1", {"a": ["1", "2", "3", "4", "5"], "b": ["6", "7"]}, seed="s")
    b = build_query_pool("q1", {"b": ["6", "7"], "a": ["1", "2", "3", "4", "5"]}, seed="s")
    assert a["items"] == b["items"]


def test_pool_order_does_not_follow_any_config_rank():
    ranked = [str(i) for i in range(20)]
    pool = build_query_pool("q1", {"a": ranked}, seed="s")
    assert pool["items"] != ranked
    assert sorted(pool["items"]) == sorted(ranked)


def test_pool_shuffle_differs_by_seed_and_query():
    ranked = [str(i) for i in range(20)]
    assert build_query_pool("q1", {"a": ranked}, seed="s1")["items"] != build_query_pool("q1", {"a": ranked}, seed="s2")["items"]
    assert build_query_pool("q1", {"a": ranked}, seed="s")["items"] != build_query_pool("q2", {"a": ranked}, seed="s")["items"]


def test_blind_display_contains_no_rank_or_config_information():
    text = format_item({"id": "p1", "product_name": "Cotton Kurti", "brand_name": "B", "category": "Kurtis",
                        "price": 500.0, "final_score": 0.93, "similarity": 0.8, "score_breakdown": {"x": 1}}, 3, 20)
    assert "0.93" not in text and "similarity" not in text and "hybrid" not in text and "vector" not in text
    assert "Cotton Kurti" in text


def test_zero_is_recorded_explicitly_unlike_label_tool():
    assert apply_judgment({}, "p1", "0") == {"p1": 0}
    assert apply_judgment({}, "p1", "2") == {"p1": 2}
    assert apply_judgment({"p1": 1}, "p1", "x") == {"p1": 1}


def test_judge_items_is_resumable_and_skip_leaves_unjudged():
    keys = iter(["2", "s", "0"])
    judged, quit_now = judge_items({"a": 1}, ["a", "b", "c", "d"], {}, lambda: next(keys), show=lambda _m: None)
    assert judged == {"a": 1, "b": 2, "d": 0}  # a already judged (not re-shown), c skipped
    assert quit_now is False


def test_judge_items_quit_saves_progress():
    keys = iter(["1", "q"])
    judged, quit_now = judge_items({}, ["a", "b", "c"], {}, lambda: next(keys), show=lambda _m: None)
    assert judged == {"a": 1} and quit_now is True


def test_export_builds_relevant_from_grades_and_tracks_judged():
    pool = {"queries": {"q1": {"query": "top", "items": ["a", "b", "c"]}, "q2": {"query": "x", "items": ["d"]}}}
    labels = {"queries": {"q1": {"judged": {"a": 2, "b": 0, "c": 1}}}}
    entries = queries_to_queryset(pool, labels, {"q1": {"intent": {"product_type": "top"}, "notes": "n"}})
    assert entries == [{"id": "q1", "query": "top", "intent": {"product_type": "top"},
                        "relevant": {"a": 2, "c": 1}, "judged": ["a", "b", "c"], "notes": "n"}]


def test_exported_entry_with_no_relevant_products_is_valid_only_because_judged_proves_it():
    cases = parse_queryset([{"id": "q1", "query": "swimwear", "relevant": {}, "judged": ["a", "b"]}])
    assert cases[0].relevant == {} and cases[0].judged == ("a", "b")
    with pytest.raises(QuerySetError, match="non-empty"):
        parse_queryset([{"id": "q1", "query": "swimwear", "relevant": {}}])


def test_relevant_must_be_subset_of_judged():
    with pytest.raises(QuerySetError, match="not in its 'judged' list"):
        parse_queryset([{"id": "q1", "query": "x", "relevant": {"a": 2}, "judged": ["b"]}])


def test_judged_ids_are_validated_against_the_database():
    cases = parse_queryset([{"id": "q1", "query": "x", "relevant": {"a": 2}, "judged": ["a", "ghost"]}])
    with pytest.raises(QuerySetError, match="ghost"):
        validate_product_ids_exist(cases, {"a"})


def _stub(results):
    return lambda case, k: [{"id": pid} for pid in results.get(case.id, [])][:k]


def test_runner_counts_unjudged_results_in_top_20():
    case = parse_queryset([{"id": "q1", "query": "x", "relevant": {"a": 2}, "judged": ["a", "b"]}])[0]
    result = evaluate_query(case, _stub({"q1": ["a", "b", "c", "d"]}))
    assert result.unjudged_in_top_20 == 2 and result.judged_total == 2


def test_runner_excludes_no_relevant_queries_from_means_but_not_zero_result_rate():
    cases = parse_queryset([
        {"id": "q1", "query": "x", "relevant": {"a": 2}, "judged": ["a"]},
        {"id": "q2", "query": "swimwear", "relevant": {}, "judged": ["z"]},
    ])
    run = run_evaluation(cases, _stub({"q1": ["a"], "q2": []}))
    assert run["summary"]["num_scored"] == 1
    assert run["summary"]["excluded_no_relevant"] == ["q2"]
    assert run["summary"]["mean_ndcg_at_10"] == pytest.approx(1.0)
    assert run["summary"]["zero_result_rate"] == pytest.approx(0.5)
    json.dumps(run)


def test_pool_stats_reports_depth_and_judged_counts():
    pool = {"queries": {"q1": {"query": "a", "items": ["1", "2", "3"]}, "q2": {"query": "b", "items": ["4"]}}}
    stats = pool_stats(pool, {"q1": {"per_config_returned": {"vector": 3}}}, {"queries": {"q1": {"judged": {"1": 2, "2": 0}}}})
    assert stats["pool_items_total"] == 4 and stats["judged_total"] == 2
    assert stats["max_pool_size"] == 3 and stats["mean_pool_size"] == 2.0
    assert stats["rows"][0]["relevant"] == 1


def test_merge_pool_keeps_judged_positions_and_adds_new_items():
    from backend.eval.pool import merge_pool

    merged = merge_pool(["a", "b", "c"], {"a", "b"}, {"gated": ["c", "d", "e"]}, seed="s", qid="q1")
    assert merged["items"][:2] == ["a", "b"]  # judged items untouched, in place
    assert set(merged["items"]) == {"a", "b", "c", "d", "e"}
    assert set(merged["added"]) == {"d", "e"}
    assert merged["provenance"]["c"] == {"gated": 1}


def test_merge_pool_shuffles_new_items_in_with_old_unjudged_ones():
    from backend.eval.pool import merge_pool

    old = [f"old{i}" for i in range(10)]
    new = {"gated": [f"new{i}" for i in range(10)]}
    merged = merge_pool(old, set(), new, seed="s", qid="q1")
    tail = merged["items"]
    assert tail != old + new["gated"]  # new items are not simply appended at the end
    assert any(x.startswith("new") for x in tail[:10])
