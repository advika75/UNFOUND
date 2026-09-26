import pytest

from backend.search.rerank import RERANK_WEIGHTS, SIGNALS, audience_signal, price_signal, rerank_candidates


def cand(pid, name, similarity=0.5, **extra):
    return {"id": pid, "product_name": name, "item_name": name, "description": "", "category": extra.pop("category", ""),
            "similarity": similarity, "similarity_score": similarity, **extra}


def rank(products, attrs, **kw):
    return rerank_candidates(products, attrs, **kw)


def test_audience_mismatch_is_demoted_below_a_lower_similarity_correct_result():
    attrs = {"gender": "women"}
    out = rank([
        cand("men", "Shirt", 0.95, category="Men Shirts"),
        cand("women", "Shirt", 0.40, category_audience="WOMEN"),
    ], attrs)
    assert [r["id"] for r in out] == ["women", "men"]
    assert out[1]["score_breakdown"]["audience"] == -RERANK_WEIGHTS["audience_mismatch"]


def test_women_is_not_mistaken_for_men_substring():
    assert audience_signal(cand("a", "x", category="Women Tops"), "men") == -1.0
    assert audience_signal(cand("a", "x", category="Women Tops"), "women") == 1.0


def test_audience_with_no_evidence_is_neutral_not_penalized():
    assert audience_signal(cand("a", "Kurtis"), "women") == 0.0


def test_unisex_satisfies_either_gender():
    assert audience_signal(cand("a", "x", audience="unisex"), "men") == 1.0


def test_unspecified_signals_contribute_exactly_zero():
    out = rank([cand("a", "Black cotton kurti", 0.7, classifier_confidence=None)], {})
    breakdown = out[0]["score_breakdown"]
    for name in ("type", "audience", "colour", "style", "price", "confidence"):
        assert breakdown[name] == 0.0
    assert breakdown["fused"] == RERANK_WEIGHTS["fused"]  # single candidate normalizes to 1.0


def test_price_unset_is_neutral_and_out_of_range_is_negative():
    attrs = {"max_price": 1000}
    assert price_signal(cand("a", "x", price=0.0), attrs) == 0.0
    assert price_signal(cand("a", "x", price=None), attrs) == 0.0
    assert price_signal(cand("a", "x", price=500), attrs) == 1.0
    assert price_signal(cand("a", "x", price=5000), attrs) == -1.0
    assert price_signal(cand("a", "x", price=5000), {}) == 0.0


def test_type_exact_beats_family_only_beats_nothing():
    attrs = {"product_type": "kurti", "product_family": "ethnic-upperwear"}
    out = rank([
        cand("none", "Silver ring", 0.5),
        cand("family", "Ethnic tunic", 0.5),
        cand("exact", "Cotton kurti", 0.5),
    ], attrs)
    assert [r["id"] for r in out] == ["exact", "family", "none"]


def test_colour_and_style_fractions():
    attrs = {"colours": ["black", "white"], "styles": ["minimalist"]}
    out = rank([cand("a", "Black minimalist top", 0.5)], attrs)
    detail = out[0]["rerank_detail"]
    assert detail["colour"]["value"] == 0.5
    assert detail["style"]["value"] == 1.0


def test_debug_breakdown_sums_to_final_score():
    attrs = {"product_type": "top", "product_family": "tops", "gender": "women", "colours": ["black"],
             "max_price": 2000}
    out = rank([
        cand("a", "Black top", 0.9, category_audience="WOMEN", price=999, classifier_confidence=0.9),
        cand("b", "Red top", 0.3, category="Men Tops", price=5000, classifier_confidence=0.2),
        cand("c", "Blue skirt", 0.6),
    ], attrs)
    for row in out:
        assert sum(row["score_breakdown"].values()) == pytest.approx(row["final_score"])
        assert set(row["score_breakdown"]) == set(SIGNALS)
        detail_sum = sum(d["contribution"] for d in row["rerank_detail"].values())
        assert detail_sum == pytest.approx(row["final_score"])


def test_weights_override_changes_ordering():
    # With two candidates, min-max normalization gives the top one a full fused
    # contribution (0.35) which beats the type weight (0.25) under defaults.
    attrs = {"product_type": "top", "product_family": "tops"}
    items = [cand("sim", "Skirt", 0.9), cand("typ", "Black top", 0.1)]
    default = rank(items, attrs)
    type_heavy = rank(items, attrs, weights={**RERANK_WEIGHTS, "type": 1.0})
    assert default[0]["id"] == "sim"
    assert type_heavy[0]["id"] == "typ"


def test_empty_candidate_list():
    assert rank([], {"gender": "women"}) == []
