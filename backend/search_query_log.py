"""Append-only search query log for the admin dashboard's SEARCH ANALYTICS
panel. Logs only query text, result count, latency and whether the in-memory
search cache was hit -- deliberately no user id, session id or IP, since this
is meant purely to answer "what are people searching for and is it fast",
not to track anyone. Mirrors the existing JSONL audit-log pattern in
backend/ingestion_batches.append_audit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "search_query_log.jsonl"

# A pathological query string shouldn't be able to bloat the log file.
MAX_LOGGED_QUERY_LENGTH = 200


def log_search_query(
    query_text: str,
    result_count: int,
    latency_ms: float,
    cache_hit: bool,
    *,
    log_path: Path | None = None,
) -> None:
    path = log_path or LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.time(),
        "query": query_text[:MAX_LOGGED_QUERY_LENGTH],
        "result_count": int(result_count),
        "latency_ms": round(float(latency_ms), 1),
        "cache_hit": bool(cache_hit),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_search_query_log(*, log_path: Path | None = None, max_rows: int = 20000) -> list[dict[str, Any]]:
    path = log_path or LOG_PATH
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-max_rows:]
