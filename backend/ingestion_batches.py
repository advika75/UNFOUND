"""Pure helpers for approval-gated catalog batches, scoring, audit, and staleness."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_SOURCES = {"user_curated", "automatic_discovery", "inventory_gap", "manual_research"}
ALLOWED_STATUSES = {"pending", "approved", "ingested", "skipped", "failed", "review"}
APPROVE_SCORE = 80
REVIEW_SCORE = 60


@dataclass(frozen=True)
class BatchCandidate:
    brand_name: str
    instagram_value: str
    category_hint: str
    priority: str
    source: str
    status: str
    notes: str
    batch_id: str


def load_batch(path: Path, *, approved_only: bool = False) -> list[BatchCandidate]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for number, row in enumerate(csv.DictReader(handle), 2):
            source = str(row.get("source") or "").strip().lower(); status = str(row.get("status") or "pending").strip().lower()
            if source not in ALLOWED_SOURCES: raise ValueError(f"Row {number}: invalid source {source!r}")
            if status not in ALLOWED_STATUSES: raise ValueError(f"Row {number}: invalid status {status!r}")
            if approved_only and status != "approved": continue
            value = str(row.get("instagram_url") or row.get("instagram_handle") or "").strip()
            if not value: continue
            rows.append(BatchCandidate(str(row.get("brand_name") or "").strip(), value, str(row.get("category_hint") or "").strip(), str(row.get("priority") or "normal").strip(), source, status, str(row.get("notes") or "").strip(), path.stem))
    unique = {row.instagram_value.lower().rstrip("/"): row for row in rows}
    return list(unique.values())


def ingestion_quality_score(*, profile_accessible: bool, fashion_relevant: bool, product_post_ratio: float, recent_activity: bool, usable_image_ratio: float, category_relevant: bool, estimated_products: int, duplicate_risk: float, niche_fit: float) -> dict[str, Any]:
    score = (
        15 * bool(profile_accessible) + 15 * bool(fashion_relevant) + 15 * max(0, min(1, product_post_ratio)) +
        10 * bool(recent_activity) + 15 * max(0, min(1, usable_image_ratio)) + 10 * bool(category_relevant) +
        10 * min(1, max(0, estimated_products) / 10) + 5 * (1 - max(0, min(1, duplicate_risk))) + 5 * max(0, min(1, niche_fit))
    )
    score = round(score)
    return {"score":score, "recommendation":"APPROVE" if score >= APPROVE_SCORE else "REVIEW" if score >= REVIEW_SCORE else "SKIP"}


def append_audit(path: Path, *, batch_id: str, brand: str, post: str = "", action: str, outcome: str, reason: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp":datetime.now(timezone.utc).isoformat(), "batch":batch_id, "brand":brand, "post":post, "action":action, "outcome":outcome, "reason":reason}
    with path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def stale_brand_rows(brands: list[dict[str, Any]], products: list[dict[str, Any]], *, stale_days: int, now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc); latest: dict[str, datetime] = {}
    for row in products:
        brand_id = str(row.get("brand_id") or ""); raw = row.get("scraped_at") or row.get("created_at")
        if not brand_id or not raw: continue
        try: value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError: continue
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        latest[brand_id] = max(latest.get(brand_id, value), value)
    output=[]
    for brand in brands:
        refreshed=latest.get(str(brand.get("id"))); age=(current-refreshed).days if refreshed else None
        if age is None or age >= stale_days: output.append({"brand_id":brand.get("id"), "brand":brand.get("name") or brand.get("brand_name"), "instagram_username":brand.get("instagram_username"), "last_refresh":refreshed.isoformat() if refreshed else None, "age_days":age, "status":"NEVER_REFRESHED" if age is None else "STALE"})
    return sorted(output,key=lambda row:(row["age_days"] is None,row["age_days"] or 0),reverse=True)
