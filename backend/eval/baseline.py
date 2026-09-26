"""Reads the committed relevance baseline from backend/eval/BASELINE.md.

The gate's floor lives in a version-controlled file so it can only change
through a reviewed edit, never silently. The machine-readable part is the
first fenced ```json block after the "## Machine-readable baseline" heading.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).with_name("BASELINE.md")
_BLOCK = re.compile(r"## Machine-readable baseline\s+```json\s*(.*?)```", re.DOTALL)
_REQUIRED = ("queryset", "config", "metrics", "ndcg_tolerance")


class BaselineError(ValueError):
    pass


def parse_baseline(text: str) -> dict[str, Any]:
    match = _BLOCK.search(text)
    if not match:
        raise BaselineError("BASELINE.md has no '## Machine-readable baseline' json block")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline json block is invalid: {exc}") from exc
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise BaselineError(f"baseline json block is missing keys: {missing}")
    if "mean_ndcg_at_10" not in data["metrics"]:
        raise BaselineError("baseline metrics must include mean_ndcg_at_10")
    return data


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    return parse_baseline(path.read_text(encoding="utf-8"))


def ndcg_floor(baseline: dict[str, Any]) -> float:
    return float(baseline["metrics"]["mean_ndcg_at_10"]) - float(baseline["ndcg_tolerance"])


def latency_ceilings(baseline: dict[str, Any]) -> dict[str, float | None]:
    ceilings = baseline.get("latency_ceiling_ms") or {}
    return {"p50": ceilings.get("p50"), "p95": ceilings.get("p95")}
