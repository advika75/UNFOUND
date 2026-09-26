import random

from backend.app import (
    TIE_BREAK_EPSILON,
    apply_final_ranking,
    deterministic_rank_key,
    extract_query_attributes,
    gender_match_score,
    split_search_groups,
)


def candidate(name: str, category: str, similarity: float, **extra):
    return {
        "id": name,
        "product_name": name,
        "item_name": name,
        "description": extra.pop("description", ""),
        "category": category,
        "normalized_subcategory": extra.pop("subcategory", ""),
        "niche_score": extra.pop("niche_score", 0.7),
        "similarity": similarity,
        "similarity_score": similarity,
        **extra,
    }


def test_natural_language_query_understanding_extracts_constraints():
    attributes = extract_query_attributes("minimalist black oversized jacket under ₹3,000")
    assert attributes["product_type"] == "jacket"
    assert attributes["product_family"] == "outerwear"
    assert attributes["colours"] == ["black"]
    assert "minimalist" in attributes["styles"]
    assert "oversized" in attributes["fits"]
    assert attributes["max_price"] == 3000


def test_gender_signal_rewards_requested_audience_and_accepts_unisex():
    attributes = extract_query_attributes("women's co-ord set")
    ranked = apply_final_ranking(
        [
            candidate("Women's matching set", "Women Sets", 0.70, audience="women"),
            candidate("Men's matching set", "Men Sets", 0.70, audience="men"),
            candidate("Unisex matching set", "Unisex Sets", 0.70, audience="unisex"),
        ],
        category_id=None,
        sort_by=None,
        search_mode="text",
        query_attributes=attributes,
    )
    assert ranked[0]["score_breakdown"]["gender_match"] == 1
    assert ranked[1]["score_breakdown"]["gender_match"] == 0.75
    assert len(ranked) == 2


def test_gender_match_score_reads_category_audience_when_product_audience_is_empty():
    product = {"audience": "", "category": "Kurtis", "category_audience": "WOMEN"}
    assert gender_match_score(product, "women") == 1.0


def test_gender_match_score_matches_if_any_source_field_has_evidence():
    # Matching is OR-across-fields, not a priority order -- a match in the
    # product's own (explicit) audience field counts even when
    # category_audience disagrees, since either field is real evidence.
    product = {"audience": "unisex", "category": "Kurtis", "category_audience": "WOMEN"}
    assert gender_match_score(product, "unisex") == 1.0


def test_gender_match_score_returns_zero_with_no_evidence_anywhere():
    product = {"audience": "", "category": "Kurtis", "category_audience": ""}
    assert gender_match_score(product, "women") == 0.0


def test_gender_signal_falls_back_to_category_audience_when_product_audience_is_empty():
    # Real bug found evaluating hybrid retrieval: "Kurtis" is a real, unambiguous
    # women's category (categories.audience='WOMEN' in the live DB), but its
    # display NAME has no literal "women" in it, and the product's own audience
    # column is empty for over 96% of the catalog. Before category_audience was
    # wired in, a "women's kurti" query with a realistic hybrid-fused candidate
    # set (121 real kurtis, none with a populated product-level audience field)
    # collapsed from 121 candidates to 2 survivors under the gender hard filter.
    attributes = extract_query_attributes("women's kurti")
    ranked = apply_final_ranking(
        [
            candidate("Cotton Kurtis", "Kurtis", 0.70, category_audience="WOMEN"),
            candidate("Printed Kurti", "Kurtis", 0.65, category_audience="WOMEN"),
            candidate("Men's Formal Shirt", "Men Shirts", 0.60, category_audience="MEN"),
        ],
        category_id=None,
        sort_by=None,
        search_mode="text",
        query_attributes=attributes,
    )
    assert [row["product_name"] for row in ranked] == ["Cotton Kurtis", "Printed Kurti"]
    assert ranked[0]["score_breakdown"]["gender_match"] == 1.0


def test_text_hybrid_ranking_prioritizes_exact_type_and_colour():
    attributes = extract_query_attributes("black oversized cargo pants")
    ranked = apply_final_ranking(
        [
            candidate("White oversized shirt", "Women Tops", 0.82),
            candidate("Black utility cargo trousers", "Women Bottoms", 0.73),
            candidate("Blue straight jeans", "Women Jeans", 0.78),
        ],
        category_id=None,
        sort_by=None,
        search_mode="text",
        query_attributes=attributes,
    )
    assert ranked[0]["product_name"] == "Black utility cargo trousers"
    assert ranked[0]["score_breakdown"]["product_type_match"] == 1
    assert ranked[0]["score_breakdown"]["colour_match"] == 1


def test_image_ranking_keeps_visual_similarity_dominant():
    ranked = apply_final_ranking(
        [candidate("Closest visual", "Jackets", 0.91), candidate("Niche alternative", "Jackets", 0.70, niche_score=1)],
        category_id=None,
        sort_by=None,
        search_mode="image",
        query_attributes={},
    )
    assert ranked[0]["product_name"] == "Closest visual"
    assert ranked[0]["score_breakdown"]["visual_similarity"] == 0.91


def test_explicit_product_type_does_not_return_unrelated_fallbacks():
    ranked = apply_final_ranking(
        [candidate("Floral summer dress", "Women Dresses", 0.82)],
        category_id=None,
        sort_by=None,
        search_mode="text",
        query_attributes=extract_query_attributes("minimalist watch"),
    )
    assert ranked == []


def test_specific_crop_top_wins_over_generic_top_and_isolated_from_dresses():
    attributes = extract_query_attributes("stylish black crop tops under ₹2500")
    assert attributes["product_family"] == "tops"
    assert attributes["product_type"] == "crop-top"
    ranked = apply_final_ranking([
        candidate("Black cropped top", "Women Tops", .68, price=2200),
        candidate("Black evening dress", "Women Dresses", .94, price=2100),
        candidate("Black casual top", "Women Tops", .75, price=1800),
    ], category_id=None, sort_by=None, search_mode="text", query_attributes=attributes)
    assert [row["product_name"] for row in ranked] == ["Black cropped top"]
    exact, similar = split_search_groups(ranked, attributes)
    assert [row["product_name"] for row in exact] == ["Black cropped top"]
    assert similar == []


def test_explicit_audience_and_type_constraints_are_hard_gates():
    attributes = extract_query_attributes("women kurta")
    ranked = apply_final_ranking([
        candidate("Women's cotton kurta", "Women Ethnic", .55, audience="women"),
        candidate("Men's cotton kurta", "Men Ethnic", .99, audience="men"),
        candidate("Women's festive dress", "Women Dresses", .99, audience="women"),
    ], category_id=None, sort_by=None, search_mode="text", query_attributes=attributes)
    assert [row["product_name"] for row in ranked] == ["Women's cotton kurta"]


def test_kurta_and_jewellery_family_gates_reject_unrelated_products():
    for query, correct, wrong in [
        ("kurta under ₹3000", candidate("Cotton kurta", "Women Ethnic", .65, price=2500), candidate("Maxi dress", "Women Dresses", .92, price=2000)),
        ("minimal silver jewellery", candidate("Silver hoop earrings", "Jewellery", .67), candidate("Silver handbag", "Bags", .91)),
    ]:
        ranked = apply_final_ranking([wrong, correct], category_id=None, sort_by=None, search_mode="text", query_attributes=extract_query_attributes(query))
        assert [row["product_name"] for row in ranked] == [correct["product_name"]]


# --- Stage 5: taxonomy gap fix -------------------------------------------------

def test_previously_unrecognized_product_families_are_now_extracted():
    # These are real catalog categories (Niche_brand/supabase/unfound_experience_schema.sql)
    # that PRODUCT_FAMILIES had no entry for at all, so every structured signal fell
    # back to a neutral 0.5 for these queries and only weak semantic similarity ranked.
    assert extract_query_attributes("floral lingerie set")["product_family"] == "lingerie"
    assert extract_query_attributes("pack of 3 underwear")["product_family"] == "underwear"
    assert extract_query_attributes("oversized hoodie")["product_family"] == "hoodies"
    assert extract_query_attributes("cotton ankle socks")["product_family"] == "socks"


def test_occasion_labels_stay_a_soft_style_signal_not_a_hard_filtered_family():
    # Regression guard: "formal wear"/"gym wear" are occasion labels that span many
    # garment shapes, not a single product type like "dress" or "jeans" -- no product's
    # raw text literally contains the phrase "formal wear". Treating them as a
    # hard-filtered product_family (as first attempted) zeroed out every result for
    # this query. They must extract as a style/aesthetic term instead (soft-scored).
    formal = extract_query_attributes("women's formal wear")
    assert formal["product_family"] is None
    assert "formal" in formal["styles"]
    ranked = apply_final_ranking(
        [candidate("Embellished evening gown", "Women Dresses", 0.6, audience="women")],
        category_id=None, sort_by=None, search_mode="text", query_attributes=formal,
    )
    assert len(ranked) == 1  # not zeroed out by a hard family-match gate

    gym = extract_query_attributes("cute gym wear")
    assert gym["product_family"] is None
    assert "gym" in gym["styles"]
    assert "cute" in gym["aesthetic_terms"]


# --- Stage 6: similarity path reuses the same structured signals as search ----

def test_source_product_text_yields_the_same_structured_attributes_as_a_query():
    # related_products (backend/app.py) derives query_attributes from the source
    # product's own name/description with this exact function -- this is the
    # mechanism the Stage 6 fix relies on to stop the similarity path from being
    # pure embedding distance plus an unrelated category_id check.
    attributes = extract_query_attributes("Imported Super Baggy Jeans")
    assert attributes["product_family"] == "bottoms"
    assert attributes["product_type"] == "jeans"


def test_compound_type_falls_back_to_family_when_the_exact_phrase_never_appears():
    # Real bug caught in Stage 7 manual testing: "party dress" resolves to the
    # compound type "party-dress", whose only term is the literal phrase "party
    # dress" -- scraped Instagram captions essentially never say that verbatim, so
    # the type-level hard filter zeroed out every candidate (0 results) even though
    # genuine party-appropriate dresses exist in the broader "dresses" family.
    attributes = extract_query_attributes("party dress")
    assert attributes["product_type"] == "party-dress"
    assert attributes["product_family"] == "dresses"
    ranked = apply_final_ranking(
        [candidate("Sequin evening gown for a night out", "Women Dresses", 0.7, audience="women")],
        category_id=None, sort_by=None, search_mode="text", query_attributes=attributes,
    )
    assert len(ranked) == 1  # falls back to the family match, not zeroed out


def test_image_mode_similarity_demotes_wrong_type_despite_matching_category_id():
    # Reproduces the real bug found in Stage 6: a jeans product and an unrelated
    # handbag were both miscategorized under the same category_id in the catalog, so
    # category_match alone couldn't tell them apart. With query_attributes now
    # derived from the source product, product_type_match correctly discriminates.
    attributes = extract_query_attributes("Imported Super Baggy Jeans")
    ranked = apply_final_ranking(
        [
            candidate("Mini crossbody bag", "Women Bags", 0.66, category_id=16),
            candidate("Baggy jeans restock", "Women Bags", 0.94, category_id=16),
        ],
        category_id=16, sort_by=None, search_mode="image", query_attributes=attributes,
    )
    assert ranked[0]["product_name"] == "Baggy jeans restock"
    assert ranked[0]["score_breakdown"]["product_type_match"] == 1
    assert ranked[1]["score_breakdown"]["product_type_match"] == 0


# --- deterministic tie-break: ranking must not be decidable at the scale of ONNX/torch noise --------

def test_deterministic_rank_key_orders_by_score_descending_when_gap_exceeds_epsilon():
    high = {"id": "b", "final_score": 0.90}
    low = {"id": "a", "final_score": 0.50}
    assert sorted([low, high], key=deterministic_rank_key) == [high, low]


def test_scores_within_epsilon_are_ordered_by_id_not_by_score_or_input_order():
    # Same near-tied pair, fed in both possible input orders: output must be identical either way,
    # and it is not the higher-final_score one that wins -- id decides, exactly as specified.
    a = {"id": "a-higher-score", "final_score": 0.50 + TIE_BREAK_EPSILON / 4}
    b = {"id": "b-lower-score", "final_score": 0.50}
    assert [p["id"] for p in sorted([a, b], key=deterministic_rank_key)] == ["a-higher-score", "b-lower-score"]
    assert [p["id"] for p in sorted([b, a], key=deterministic_rank_key)] == ["a-higher-score", "b-lower-score"]


def test_perturbing_scores_by_1e7_does_not_change_output_order():
    # The regression case this tie-break exists for: ONNX vs. torch final_score differs by up to ~1e-6,
    # two orders of magnitude below TIE_BREAK_EPSILON (1e-5) -- such noise must never flip result order.
    noise = 1e-7
    base = [{"id": pid, "final_score": score} for pid, score in
            [("p1", 0.91), ("p2", 0.77), ("p3", 0.7699995), ("p4", 0.50), ("p5", 0.31)]]
    baseline_order = [p["id"] for p in sorted(base, key=deterministic_rank_key)]
    rng = random.Random(42)
    for _ in range(20):
        perturbed = [{**p, "final_score": p["final_score"] + rng.uniform(-noise, noise)} for p in base]
        assert [p["id"] for p in sorted(perturbed, key=deterministic_rank_key)] == baseline_order


def test_gap_just_outside_epsilon_is_not_tie_broken_by_id():
    # A gap comfortably larger than epsilon must still resolve by score, even if id ordering disagrees.
    higher_score_higher_id = {"id": "z", "final_score": 0.50 + TIE_BREAK_EPSILON * 3}
    lower_score_lower_id = {"id": "a", "final_score": 0.50}
    result = sorted([lower_score_lower_id, higher_score_higher_id], key=deterministic_rank_key)
    assert [p["id"] for p in result] == ["z", "a"]


def test_missing_id_or_final_score_does_not_crash_the_sort():
    products = [{"final_score": 0.5}, {"id": "x"}, {}]
    sorted(products, key=deterministic_rank_key)  # must not raise


def test_apply_final_ranking_end_to_end_is_stable_under_realistic_onnx_scale_noise():
    # Same scenario through the real ranking pipeline: two candidates whose only difference is a
    # ~1e-7-scale semantic_similarity perturbation (the measured ONNX vs. torch final_score noise)
    # must not have their relative order decided by that perturbation.
    attrs = extract_query_attributes("top")
    a = candidate("Cotton Top A", "Women Tops", 0.7000000)
    b = candidate("Cotton Top B", "Women Tops", 0.7000004)  # ~0.22 * 4e-7 =~ 1e-7 final_score delta
    order_1 = [p["product_name"] for p in apply_final_ranking([a, b], category_id=None, sort_by=None, search_mode="text", query_attributes=attrs)]
    order_2 = [p["product_name"] for p in apply_final_ranking([b, a], category_id=None, sort_by=None, search_mode="text", query_attributes=attrs)]
    assert order_1 == order_2  # deterministic regardless of input order, not just regardless of which one is "first"


def test_rerank_candidates_uses_the_same_deterministic_tie_break():
    from backend.search.rerank import rerank_candidates

    products = [{"id": "a-higher", "similarity": 0.5 + TIE_BREAK_EPSILON / 4, "product_name": "x", "category": ""},
                {"id": "b-lower", "similarity": 0.5, "product_name": "y", "category": ""}]
    ranked = rerank_candidates(products, {})
    assert [p["id"] for p in ranked] == ["a-higher", "b-lower"]
