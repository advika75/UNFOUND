import json
from datetime import datetime, timezone

from backend.ingestion_batches import append_audit, ingestion_quality_score, load_batch, stale_brand_rows


def test_batch_loader_deduplicates_and_enforces_approval(tmp_path):
    path = tmp_path / "batch_007.csv"
    path.write_text(
        "brand_name,instagram_handle,category_hint,priority,source,status,notes\n"
        "One,@one,tops,high,user_curated,approved,ok\n"
        "Duplicate,@one,tops,normal,user_curated,pending,no\n"
        "Two,@two,bags,normal,inventory_gap,pending,wait\n",
        encoding="utf-8",
    )
    assert len(load_batch(path)) == 2
    approved = load_batch(path, approved_only=True)
    assert [row.brand_name for row in approved] == ["One"]
    assert approved[0].batch_id == "batch_007"


def test_quality_score_has_approve_review_skip_thresholds():
    approve = ingestion_quality_score(profile_accessible=True, fashion_relevant=True, product_post_ratio=1, recent_activity=True, usable_image_ratio=1, category_relevant=True, estimated_products=10, duplicate_risk=0, niche_fit=1)
    skip = ingestion_quality_score(profile_accessible=False, fashion_relevant=False, product_post_ratio=0, recent_activity=False, usable_image_ratio=0, category_relevant=False, estimated_products=0, duplicate_risk=1, niche_fit=0)
    assert approve == {"score": 100, "recommendation": "APPROVE"}
    assert skip["recommendation"] == "SKIP"


def test_audit_and_stale_report(tmp_path):
    audit = tmp_path / "audit.jsonl"
    append_audit(audit, batch_id="b1", brand="One", post="p1", action="upsert", outcome="INSERTED")
    assert json.loads(audit.read_text())["batch"] == "b1"
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    rows = stale_brand_rows(
        [{"id": "b1", "name": "Old"}, {"id": "b2", "name": "Never"}],
        [{"brand_id": "b1", "scraped_at": "2026-05-01T00:00:00Z"}],
        stale_days=60,
        now=now,
    )
    assert {row["status"] for row in rows} == {"STALE", "NEVER_REFRESHED"}
