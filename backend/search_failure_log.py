"""Local, anonymous search-quality logging for development review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.product_taxonomy import identify_product


ALLOWED_REVIEWS = {"", "GOOD", "PARTIAL", "BAD"}


def append_search_evaluation(path: Path, *, query: str, response: dict[str, Any], review: str = "") -> None:
    review = review.upper();
    if review not in ALLOWED_REVIEWS: raise ValueError("review must be GOOD, PARTIAL, BAD, or blank")
    row = {"created_at":datetime.now(timezone.utc).isoformat(), "query":query, "parsed_intent":response.get("query_understanding") or {}, "filters":{key:(response.get("query_understanding") or {}).get(key) for key in ("min_price","max_price","gender")}, "candidate_product_types":[identify_product(f"{item.get('product_name','')} {item.get('description','')}")[1] for item in response.get("results", [])[:20]], "top_results":[{"product_id":item.get("id"), "product":item.get("product_name"), "category":item.get("category"), "score":item.get("final_score"), "signals":item.get("score_breakdown")} for item in response.get("results", [])[:20]], "review":review}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, ensure_ascii=False) + "\n")
