import math

import pytest

from backend.eval.metrics import (
    mrr,
    ndcg_at_k,
    precision_at_k,
    reciprocal_rank,
    recall_at_k,
    zero_result_rate,
)


# --- ndcg_at_k: hand-computed cases -----------------------------------------
#
# DCG(gains) = sum over rank i=1.. of gain_i / log2(i + 1)
# nDCG@k = DCG(actual top-k gains) / DCG(ideal top-k gains)

def test_ndcg_at_k_perfect_ranking_is_one():
    # relevant = {a: 2, b: 1}, results already in ideal order [a, b]
    # DCG   = 2/log2(2) + 1/log2(3) = 2/1 + 1/1.584962... = 2 + 0.630930 = 2.630930
    # IDCG  = same ranking is already ideal -> IDCG == DCG
    # nDCG@10 = 2.630930 / 2.630930 = 1.0
    relevant = {"a": 2, "b": 1}
    assert ndcg_at_k(["a", "b"], relevant, k=10) == pytest.approx(1.0)


def test_ndcg_at_k_swapped_ranking_hand_computed():
    # relevant = {a: 2, b: 1}, results in swapped (non-ideal) order [b, a]
    # DCG  = 1/log2(2) + 2/log2(3) = 1/1 + 2/1.584962... = 1 + 1.261860 = 2.261860
    # IDCG = 2/log2(2) + 1/log2(3) = 2 + 0.630930 = 2.630930   (ideal order is [a, b])
    # nDCG@10 = 2.261860 / 2.630930 = 0.859719 (to 6 dp)
    relevant = {"a": 2, "b": 1}
    result = ndcg_at_k(["b", "a"], relevant, k=10)
    expected_dcg = 1 / math.log2(2) + 2 / math.log2(3)
    expected_idcg = 2 / math.log2(2) + 1 / math.log2(3)
    assert result == pytest.approx(expected_dcg / expected_idcg)
    assert result == pytest.approx(0.8597187, abs=1e-6)


def test_ndcg_at_k_truncates_ideal_ranking_to_k():
    # relevant = {a: 2, b: 1, c: 1}, results = [c, a, b, x], k=2
    # Actual top-2 gains:  c=1 (rank1), a=2 (rank2) -> same numbers as the swapped case above
    # Ideal top-2 gains:   best 2 of [2, 1, 1] sorted desc = [2, 1] -> same IDCG as above
    # So this must equal the same 0.8597187 value, even though a third relevant
    # product (c... here b) exists outside the k=2 window entirely.
    relevant = {"a": 2, "b": 1, "c": 1}
    result = ndcg_at_k(["c", "a", "b", "x"], relevant, k=2)
    assert result == pytest.approx(0.8597187, abs=1e-6)


def test_ndcg_at_k_gives_irrelevant_products_zero_gain():
    # a single relevant product buried at rank 3 behind two irrelevant ones:
    # DCG = 0/log2(2) + 0/log2(3) + 2/log2(4) = 1.0 ; IDCG (a at rank 1) = 2/log2(2) = 2.0
    relevant = {"a": 2}
    assert ndcg_at_k(["x", "y", "a"], relevant, k=3) == pytest.approx(0.5)


def test_ndcg_at_k_empty_results_is_zero():
    assert ndcg_at_k([], {"a": 2}, k=10) == 0.0


def test_ndcg_at_k_rejects_empty_relevant():
    with pytest.raises(ValueError, match="non-empty"):
        ndcg_at_k(["a"], {}, k=10)


# --- recall_at_k -------------------------------------------------------------

def test_recall_at_k_counts_any_grade_as_relevant():
    relevant = {"a": 2, "b": 1, "c": 1}
    assert recall_at_k(["a", "x", "b"], relevant, k=3) == pytest.approx(2 / 3)


def test_recall_at_k_full_when_all_relevant_found():
    relevant = {"a": 2, "b": 1}
    assert recall_at_k(["a", "b", "z"], relevant, k=3) == 1.0


def test_recall_at_k_respects_k_cutoff():
    relevant = {"a": 2, "b": 1}
    assert recall_at_k(["z", "a", "b"], relevant, k=1) == 0.0


# --- precision_at_k ------------------------------------------------------------

def test_precision_at_k_divides_by_k_not_by_results_returned():
    # only 1 result returned, k=10 -> still divided by 10, not by 1
    relevant = {"a": 2}
    assert precision_at_k(["a"], relevant, k=10) == pytest.approx(0.1)


def test_precision_at_k_counts_hits_in_window():
    relevant = {"a": 2, "c": 1}
    assert precision_at_k(["a", "b", "c", "d"], relevant, k=4) == pytest.approx(0.5)


def test_precision_at_k_zero_when_nothing_relevant_in_window():
    relevant = {"z": 2}
    assert precision_at_k(["a", "b"], relevant, k=2) == 0.0


# --- reciprocal_rank / mrr -----------------------------------------------------

def test_reciprocal_rank_first_position():
    assert reciprocal_rank(["a", "b"], {"a": 2}) == 1.0


def test_reciprocal_rank_third_position():
    assert reciprocal_rank(["x", "y", "a"], {"a": 1}) == pytest.approx(1 / 3)


def test_reciprocal_rank_zero_when_absent():
    assert reciprocal_rank(["x", "y"], {"a": 2}) == 0.0


def test_mrr_averages_reciprocal_ranks():
    assert mrr([1.0, 0.5, 0.0]) == pytest.approx(0.5)


def test_mrr_rejects_empty_input():
    with pytest.raises(ValueError, match="non-empty"):
        mrr([])


# --- zero_result_rate ------------------------------------------------------------

def test_zero_result_rate_counts_empty_lists():
    assert zero_result_rate([["a"], [], [], ["b", "c"]]) == pytest.approx(0.5)


def test_zero_result_rate_all_nonempty_is_zero():
    assert zero_result_rate([["a"], ["b"]]) == 0.0


def test_zero_result_rate_rejects_empty_input():
    with pytest.raises(ValueError, match="non-empty"):
        zero_result_rate([])
