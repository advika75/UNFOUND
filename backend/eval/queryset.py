"""Loading and validation for offline search-relevance query sets.

A query set is a JSON array of query cases. Each case has the shape:

    {
      "id": "q001",
      "query": "black oversized top",
      "intent": {"product_type": "top", "colour": "black",
                 "style": "oversized", "audience": "women"},
      "relevant": {"<product_id>": 2, "<product_id>": 1},
      "notes": "optional"
    }

Graded relevance in "relevant": 2 = exactly right, 1 = acceptable/adjacent,
absent (not a key) = irrelevant. Only 1 and 2 are valid grades.

This module has no Supabase/network dependency of its own: product-id
existence validation takes the set of valid ids as a plain argument, so it
can be unit-tested with a fake id set. `fetch_valid_product_ids` is the only
function here that touches the database, kept separate and thin so callers
can swap it out entirely in tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class QuerySetError(ValueError):
    """Raised when a query set file is malformed or references unknown products."""


@dataclass(frozen=True)
class QueryCase:
    id: str
    query: str
    intent: dict[str, Any] = field(default_factory=dict)
    relevant: dict[str, int] = field(default_factory=dict)
    notes: str | None = None
    judged: tuple[str, ...] = ()


_VALID_GRADES = {1, 2}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QuerySetError(message)


def parse_queryset(raw: list[Any]) -> list[QueryCase]:
    """Parse and structurally validate raw JSON data into QueryCase objects.

    Does not touch the database — only checks the shape of the data itself
    (types, required fields, valid grade values, duplicate ids).
    """
    _require(isinstance(raw, list), f"query set must be a JSON array, got {type(raw).__name__}")
    _require(len(raw) > 0, "query set must not be empty")

    cases: list[QueryCase] = []
    seen_ids: set[str] = set()

    for index, entry in enumerate(raw):
        _require(isinstance(entry, dict), f"entry {index} must be a JSON object, got {type(entry).__name__}")

        case_id = entry.get("id")
        _require(isinstance(case_id, str) and case_id.strip() != "", f"entry {index} is missing a non-empty string 'id'")
        _require(case_id not in seen_ids, f"duplicate query id '{case_id}'")
        seen_ids.add(case_id)

        query = entry.get("query")
        _require(isinstance(query, str) and query.strip() != "", f"query '{case_id}' is missing a non-empty string 'query'")

        intent = entry.get("intent", {})
        _require(isinstance(intent, dict), f"query '{case_id}' has 'intent' that is not an object")

        judged_raw = entry.get("judged", [])
        _require(isinstance(judged_raw, list) and all(isinstance(j, str) and j.strip() for j in judged_raw), f"query '{case_id}' has 'judged' that is not a list of product id strings")

        relevant = entry.get("relevant")
        # A pooled-judgment query may legitimately have zero relevant products (everything judged 0);
        # that is only representable when 'judged' proves someone actually looked.
        _require(isinstance(relevant, dict) and (len(relevant) > 0 or len(judged_raw) > 0), f"query '{case_id}' must have a non-empty 'relevant' object")
        for product_id, grade in relevant.items():
            _require(isinstance(product_id, str) and product_id.strip() != "", f"query '{case_id}' has a non-string/empty product id in 'relevant'")
            _require(isinstance(grade, int) and not isinstance(grade, bool) and grade in _VALID_GRADES, f"query '{case_id}' has invalid relevance grade {grade!r} for product '{product_id}' (must be 1 or 2)")
        if judged_raw:
            _require(set(relevant) <= set(judged_raw), f"query '{case_id}' has relevant products that are not in its 'judged' list")

        notes = entry.get("notes")
        _require(notes is None or isinstance(notes, str), f"query '{case_id}' has 'notes' that is not a string")

        cases.append(QueryCase(id=case_id, query=query, intent=dict(intent), relevant={k: int(v) for k, v in relevant.items()}, notes=notes, judged=tuple(judged_raw)))

    return cases


def validate_product_ids_exist(cases: list[QueryCase], valid_product_ids: set[str]) -> None:
    """Fail loudly, naming every offending id, if a query references an unknown product.

    Pure function: takes the known-valid id set as an argument rather than
    fetching it itself, so it is fully unit-testable without a database.
    """
    missing: dict[str, list[str]] = {}
    for case in cases:
        offending = [pid for pid in dict.fromkeys([*case.relevant, *case.judged]) if pid not in valid_product_ids]
        if offending:
            missing[case.id] = offending

    if missing:
        detail = "; ".join(f"{qid}: {', '.join(pids)}" for qid, pids in missing.items())
        raise QuerySetError(f"query set references product ids that do not exist in the database - {detail}")


def load_queryset(path: str | Path) -> list[QueryCase]:
    """Load and structurally validate a query set from a JSON file. No DB access."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuerySetError(f"{path} is not valid JSON: {exc}") from exc
    return parse_queryset(raw)


def fetch_valid_product_ids(supabase: Any) -> set[str]:
    """Fetch the set of real product ids from the database. The only DB-touching function here."""
    from backend.category_quality import fetch_all

    rows = fetch_all(supabase, "products")
    return {str(row["id"]) for row in rows if row.get("id") is not None}


def load_and_validate_queryset(path: str | Path, supabase: Any) -> list[QueryCase]:
    """Convenience wrapper: load a query set from disk and validate its product ids against the live DB."""
    cases = load_queryset(path)
    validate_product_ids_exist(cases, fetch_valid_product_ids(supabase))
    return cases
