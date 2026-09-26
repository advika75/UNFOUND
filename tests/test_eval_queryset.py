import json

import pytest

from backend.eval.queryset import (
    QueryCase,
    QuerySetError,
    load_queryset,
    parse_queryset,
    validate_product_ids_exist,
)


def _entry(**overrides):
    defaults = dict(
        id="q001",
        query="black oversized top",
        intent={"product_type": "top", "colour": "black", "style": "oversized", "audience": "women"},
        relevant={"prod-1": 2, "prod-2": 1},
    )
    defaults.update(overrides)
    return defaults


def test_parse_queryset_builds_query_case_objects():
    cases = parse_queryset([_entry()])
    assert cases == [
        QueryCase(
            id="q001",
            query="black oversized top",
            intent={"product_type": "top", "colour": "black", "style": "oversized", "audience": "women"},
            relevant={"prod-1": 2, "prod-2": 1},
            notes=None,
        )
    ]


def test_parse_queryset_accepts_optional_notes_and_missing_intent():
    cases = parse_queryset([{"id": "q002", "query": "necklace", "relevant": {"prod-1": 2}, "notes": "thin inventory"}])
    assert cases[0].intent == {}
    assert cases[0].notes == "thin inventory"


def test_parse_queryset_rejects_non_list_root():
    with pytest.raises(QuerySetError, match="must be a JSON array"):
        parse_queryset({"id": "q001"})


def test_parse_queryset_rejects_empty_list():
    with pytest.raises(QuerySetError, match="must not be empty"):
        parse_queryset([])


def test_parse_queryset_rejects_missing_id():
    with pytest.raises(QuerySetError, match="missing a non-empty string 'id'"):
        parse_queryset([_entry(id="")])


def test_parse_queryset_rejects_duplicate_ids():
    with pytest.raises(QuerySetError, match="duplicate query id 'q001'"):
        parse_queryset([_entry(), _entry()])


def test_parse_queryset_rejects_missing_query_text():
    with pytest.raises(QuerySetError, match="missing a non-empty string 'query'"):
        parse_queryset([_entry(query="  ")])


def test_parse_queryset_rejects_empty_relevant_map():
    with pytest.raises(QuerySetError, match="non-empty 'relevant' object"):
        parse_queryset([_entry(relevant={})])


@pytest.mark.parametrize("bad_grade", [0, 3, -1, "2", True, 1.5])
def test_parse_queryset_rejects_invalid_relevance_grades(bad_grade):
    with pytest.raises(QuerySetError, match="invalid relevance grade"):
        parse_queryset([_entry(relevant={"prod-1": bad_grade})])


def test_parse_queryset_rejects_non_string_notes():
    with pytest.raises(QuerySetError, match="'notes' that is not a string"):
        parse_queryset([_entry(notes=123)])


def test_load_queryset_reads_json_file(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps([_entry()]), encoding="utf-8")
    cases = load_queryset(path)
    assert len(cases) == 1 and cases[0].id == "q001"


def test_load_queryset_rejects_malformed_json(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(QuerySetError, match="not valid JSON"):
        load_queryset(path)


def test_validate_product_ids_exist_passes_when_all_ids_known():
    cases = parse_queryset([_entry()])
    validate_product_ids_exist(cases, valid_product_ids={"prod-1", "prod-2"})


def test_validate_product_ids_exist_fails_loudly_naming_offending_ids():
    cases = parse_queryset([_entry(relevant={"prod-1": 2, "ghost-id": 1})])
    with pytest.raises(QuerySetError) as excinfo:
        validate_product_ids_exist(cases, valid_product_ids={"prod-1"})
    assert "q001" in str(excinfo.value)
    assert "ghost-id" in str(excinfo.value)


def test_validate_product_ids_exist_reports_multiple_offending_queries():
    cases = parse_queryset([
        _entry(id="q001", relevant={"ghost-a": 2}),
        _entry(id="q002", relevant={"ghost-b": 1}),
    ])
    with pytest.raises(QuerySetError) as excinfo:
        validate_product_ids_exist(cases, valid_product_ids=set())
    message = str(excinfo.value)
    assert "q001" in message and "ghost-a" in message
    assert "q002" in message and "ghost-b" in message
