import pytest

from backend.search.fusion import rrf_fuse


def test_rrf_fuse_hand_computed_ties_and_single_list_membership():
    # list1 = [a, b, c], list2 = [b, a, d], k=60, equal weights
    #   a: list1 rank1 -> 1/61 ; list2 rank2 -> 1/62  => 1/61 + 1/62
    #   b: list1 rank2 -> 1/62 ; list2 rank1 -> 1/61  => 1/62 + 1/61  (exact tie with a)
    #   c: list1 rank3 -> 1/63 ; absent from list2    => 1/63
    #   d: absent from list1   ; list2 rank3 -> 1/63  => 1/63          (exact tie with c)
    result = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]], k=60)
    expected_top = 1 / 61 + 1 / 62
    expected_bottom = 1 / 63

    assert result == [
        ("a", pytest.approx(expected_top)),
        ("b", pytest.approx(expected_top)),
        ("c", pytest.approx(expected_bottom)),
        ("d", pytest.approx(expected_bottom)),
    ]
    # ties are broken by first-seen order across the input lists, not arbitrary
    assert [item_id for item_id, _ in result] == ["a", "b", "c", "d"]


def test_rrf_fuse_weighted_lists_hand_computed():
    # list1 = [x, y] weight 2.0, list2 = [y, x] weight 1.0, k=60
    #   x: 2.0/61 (rank1 in list1) + 1.0/62 (rank2 in list2) = 2/61 + 1/62 ~= 0.048916
    #   y: 2.0/62 (rank2 in list1) + 1.0/61 (rank1 in list2) = 2/62 + 1/61 ~= 0.048651
    # x edges out y because the heavier-weighted list ranks x first.
    result = rrf_fuse([["x", "y"], ["y", "x"]], k=60, weights=[2.0, 1.0])
    scores = dict(result)
    assert scores["x"] == pytest.approx(2 / 61 + 1 / 62)
    assert scores["y"] == pytest.approx(2 / 62 + 1 / 61)
    assert result[0][0] == "x"


def test_rrf_fuse_single_list_is_just_its_own_rank_order():
    result = rrf_fuse([["a", "b"]], k=60)
    assert result == [("a", pytest.approx(1 / 61)), ("b", pytest.approx(1 / 62))]


def test_rrf_fuse_k_parameter_changes_score_not_relative_order():
    result = rrf_fuse([["a"]], k=1)
    assert result == [("a", pytest.approx(0.5))]  # 1 / (1 + 1)


def test_rrf_fuse_empty_sublist_contributes_nothing():
    result = rrf_fuse([["a", "b"], []], k=60)
    assert result == [("a", pytest.approx(1 / 61)), ("b", pytest.approx(1 / 62))]


def test_rrf_fuse_defaults_to_equal_weights():
    with_explicit = rrf_fuse([["a", "b"], ["b", "a"]], k=60, weights=[1.0, 1.0])
    with_default = rrf_fuse([["a", "b"], ["b", "a"]], k=60)
    assert with_explicit == with_default


def test_rrf_fuse_rejects_empty_ranked_lists():
    with pytest.raises(ValueError, match="non-empty"):
        rrf_fuse([], k=60)


def test_rrf_fuse_rejects_mismatched_weights_length():
    with pytest.raises(ValueError, match="weights length"):
        rrf_fuse([["a"], ["b"]], k=60, weights=[1.0])


def test_rrf_fuse_rejects_negative_k():
    with pytest.raises(ValueError, match="non-negative"):
        rrf_fuse([["a"]], k=-1)
