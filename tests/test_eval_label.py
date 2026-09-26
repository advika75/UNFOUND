
import pytest

from backend.eval.label import (
    apply_grade,
    build_parser,
    filter_catalog_rows,
    find_entry,
    format_result_line,
    label_results,
    load_raw_queryset,
    save_raw_queryset,
    upsert_relevant,
)


def _product(product_id, **overrides):
    row = {
        "id": product_id,
        "product_name": f"Product {product_id}",
        "brand_name": "TestBrand",
        "category": "Women Tops",
        "audience": "WOMEN",
        "price": 999.0,
        "image_url": "https://example.com/x.jpg",
    }
    row.update(overrides)
    return row


def _raw_row(row_id, **overrides):
    row = {
        "id": row_id,
        "catalog_status": "ACTIVE",
        "product_name": "Black Corset Top",
        "category": "Women Tops",
        "description": "black oversized crop top",
        "audience": "WOMEN",
        "subcategory": "",
        "normalized_main_category": "",
        "normalized_subcategory": "",
        "metadata": {},
    }
    row.update(overrides)
    return row


# --- apply_grade --------------------------------------------------------------

def test_apply_grade_sets_exact_match():
    assert apply_grade({}, "p1", "2") == {"p1": 2}


def test_apply_grade_sets_adjacent_match():
    assert apply_grade({}, "p1", "1") == {"p1": 1}


def test_apply_grade_zero_removes_entry_since_absence_is_irrelevant():
    assert apply_grade({"p1": 2}, "p1", "0") == {}


def test_apply_grade_zero_on_ungraded_product_is_a_noop():
    assert apply_grade({"other": 1}, "p1", "0") == {"other": 1}


def test_apply_grade_unrecognized_key_is_a_noop():
    assert apply_grade({"p1": 2}, "p1", "x") == {"p1": 2}


def test_apply_grade_does_not_mutate_input():
    original = {"p1": 1}
    apply_grade(original, "p1", "2")
    assert original == {"p1": 1}


# --- format_result_line --------------------------------------------------------

def test_format_result_line_includes_all_required_display_fields():
    line = format_result_line(_product("p1"), index=1, total=30, current_grade=None)
    assert "id=p1" in line
    assert "Product p1" in line
    assert "brand=TestBrand" in line
    assert "category=Women Tops" in line
    assert "audience=WOMEN" in line
    assert "999" in line
    assert "https://example.com/x.jpg" in line


def test_format_result_line_shows_current_grade_when_present():
    line = format_result_line(_product("p1"), index=1, total=30, current_grade=2)
    assert "current grade: 2" in line


def test_format_result_line_handles_missing_price_and_category():
    line = format_result_line(_product("p1", price=None, category=""), index=1, total=1, current_grade=None)
    assert "no price" in line
    assert "(none)" in line


# --- label_results: scripted key sequences, no real terminal -----------------

def test_label_results_grades_each_product_in_order():
    results = [_product("p1"), _product("p2"), _product("p3")]
    keys = iter(["2", "1", "0"])
    updated = label_results({}, results, read_key=lambda: next(keys), show=lambda _msg: None)
    assert updated == {"p1": 2, "p2": 1}


def test_label_results_quit_stops_early_and_keeps_prior_grades():
    results = [_product("p1"), _product("p2"), _product("p3")]
    keys = iter(["2", "q"])
    updated = label_results({}, results, read_key=lambda: next(keys), show=lambda _msg: None)
    assert updated == {"p1": 2}


def test_label_results_skip_leaves_product_ungraded():
    results = [_product("p1"), _product("p2")]
    keys = iter(["s", "2"])
    updated = label_results({}, results, read_key=lambda: next(keys), show=lambda _msg: None)
    assert updated == {"p2": 2}


def test_label_results_reprompts_on_unrecognized_key():
    results = [_product("p1")]
    keys = iter(["z", "2"])
    updated = label_results({}, results, read_key=lambda: next(keys), show=lambda _msg: None)
    assert updated == {"p1": 2}


def test_label_results_preserves_existing_grades_not_touched_by_quit():
    results = [_product("p1"), _product("p2")]
    keys = iter(["q"])
    updated = label_results({"p2": 1}, results, read_key=lambda: next(keys), show=lambda _msg: None)
    assert updated == {"p2": 1}


# --- filter_catalog_rows: pure catalog filtering, no DB -----------------------

def test_filter_catalog_rows_by_category_substring():
    rows = [_raw_row("p1", category="Women Tops"), _raw_row("p2", category="Women Jewelry")]
    matches = filter_catalog_rows(rows, category_contains="jewelry")
    assert [row["id"] for row in matches] == ["p2"]


def test_filter_catalog_rows_excludes_non_product_rows():
    rows = [_raw_row("p1", catalog_status="NON_PRODUCT"), _raw_row("p2")]
    matches = filter_catalog_rows(rows, category_contains="women tops")
    assert [row["id"] for row in matches] == ["p2"]


def test_filter_catalog_rows_by_colour_substring():
    rows = [
        _raw_row("p1", product_name="Oversized Top", description="black oversized top"),
        _raw_row("p2", product_name="Fitted Top", description="red fitted top"),
    ]
    matches = filter_catalog_rows(rows, colour="black")
    assert [row["id"] for row in matches] == ["p1"]


def test_filter_catalog_rows_respects_limit():
    rows = [_raw_row(f"p{i}") for i in range(10)]
    matches = filter_catalog_rows(rows, limit=3)
    assert len(matches) == 3


def test_filter_catalog_rows_no_filters_returns_all_active_up_to_limit():
    rows = [_raw_row("p1"), _raw_row("p2")]
    matches = filter_catalog_rows(rows)
    assert len(matches) == 2


# --- query set file I/O ---------------------------------------------------------

def test_load_raw_queryset_returns_empty_list_when_file_missing(tmp_path):
    assert load_raw_queryset(tmp_path / "missing.json") == []


def test_save_and_load_raw_queryset_round_trips(tmp_path):
    path = tmp_path / "v1.json"
    entries = [{"id": "q001", "query": "black top", "intent": {}, "relevant": {"p1": 2}}]
    save_raw_queryset(path, entries)
    assert load_raw_queryset(path) == entries


def test_find_entry_returns_none_when_absent():
    assert find_entry([{"id": "q001"}], "q999") is None


def test_find_entry_returns_matching_entry():
    entries = [{"id": "q001", "query": "a"}, {"id": "q002", "query": "b"}]
    assert find_entry(entries, "q002")["query"] == "b"


# --- upsert_relevant -------------------------------------------------------------

def test_upsert_relevant_updates_existing_entry_without_touching_intent_or_notes():
    entries = [{"id": "q001", "query": "black top", "intent": {"colour": "black"}, "relevant": {}, "notes": "keep me"}]
    updated = upsert_relevant(entries, "q001", {"p1": 2})
    assert updated[0]["relevant"] == {"p1": 2}
    assert updated[0]["intent"] == {"colour": "black"}
    assert updated[0]["notes"] == "keep me"


def test_upsert_relevant_creates_new_entry_when_query_given():
    updated = upsert_relevant([], "q002", {"p1": 1}, query="new query", intent={"colour": "red"})
    assert updated == [{"id": "q002", "query": "new query", "intent": {"colour": "red"}, "relevant": {"p1": 1}}]


def test_upsert_relevant_raises_when_creating_without_query():
    with pytest.raises(ValueError, match="does not exist"):
        upsert_relevant([], "q002", {"p1": 1})


def test_upsert_relevant_does_not_mutate_input_list():
    entries = [{"id": "q001", "query": "a", "intent": {}, "relevant": {}}]
    upsert_relevant(entries, "q001", {"p1": 2})
    assert entries[0]["relevant"] == {}


def test_upsert_relevant_can_update_query_text_on_existing_entry():
    entries = [{"id": "q001", "query": "old text", "intent": {}, "relevant": {}}]
    updated = upsert_relevant(entries, "q001", {}, query="new text")
    assert updated[0]["query"] == "new text"


# --- CLI wiring (argparse only, no DB) --------------------------------------------

def test_build_parser_search_defaults():
    args = build_parser().parse_args(["search", "--queryset", "v1.json", "--id", "q001"])
    assert args.command == "search"
    assert args.query is None
    assert args.top_n == 30


def test_build_parser_browse_requires_id_and_queryset():
    args = build_parser().parse_args([
        "browse", "--queryset", "v1.json", "--id", "q001",
        "--category", "tops", "--type", "top", "--colour", "black",
    ])
    assert args.category == "tops"
    assert args.type == "top"
    assert args.colour == "black"
    assert args.limit == 50


def test_build_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
