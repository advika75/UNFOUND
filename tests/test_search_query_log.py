from pathlib import Path

from backend.search_query_log import MAX_LOGGED_QUERY_LENGTH, log_search_query, read_search_query_log


def test_log_and_read_round_trip(tmp_path: Path) -> None:
    log_path = tmp_path / "search_query_log.jsonl"
    log_search_query("black oversized top", 16, 123.456, False, log_path=log_path)
    log_search_query("black oversized top", 16, 12.0, True, log_path=log_path)
    rows = read_search_query_log(log_path=log_path)
    assert len(rows) == 2
    assert rows[0]["query"] == "black oversized top"
    assert rows[0]["result_count"] == 16
    assert rows[0]["latency_ms"] == 123.5
    assert rows[0]["cache_hit"] is False
    assert rows[1]["cache_hit"] is True
    # No user identifier of any kind is ever written.
    assert "user_id" not in rows[0]
    assert "session" not in rows[0]
    assert "ip" not in rows[0]


def test_read_missing_log_returns_empty_list(tmp_path: Path) -> None:
    assert read_search_query_log(log_path=tmp_path / "nope.jsonl") == []


def test_read_skips_corrupt_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "search_query_log.jsonl"
    log_search_query("a query", 3, 10.0, False, log_path=log_path)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("not valid json\n")
        handle.write("\n")
    log_search_query("another query", 0, 5.0, False, log_path=log_path)
    rows = read_search_query_log(log_path=log_path)
    assert len(rows) == 2


def test_query_text_is_truncated_to_guard_against_pathological_input(tmp_path: Path) -> None:
    log_path = tmp_path / "search_query_log.jsonl"
    huge_query = "a" * (MAX_LOGGED_QUERY_LENGTH * 5)
    log_search_query(huge_query, 0, 1.0, False, log_path=log_path)
    rows = read_search_query_log(log_path=log_path)
    assert len(rows[0]["query"]) == MAX_LOGGED_QUERY_LENGTH
