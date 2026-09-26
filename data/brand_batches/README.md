# Approval-gated brand batches

Keep each CSV to 10–20 candidates. Every row must have an exact Instagram URL or handle before previewing. Candidate sources are `user_curated`, `automatic_discovery`, `inventory_gap`, or `manual_research`.

```bash
# Safe preview: scrapes and scores, but never writes catalog data
python -m training.scraper_pipeline preview-brand-batch --file data/brand_batches/batch_001.csv --posts-per-brand 20

# After manually changing accepted rows to status=approved
python -m training.scraper_pipeline ingest-brand-batch --file data/brand_batches/batch_001.csv --posts-per-brand 20 --approve

# Required post-batch category and search regression checks
python -m backend.category_quality category-health
python -m backend.evaluate_search_quality --api-url http://127.0.0.1:8000

# Exact-brand incremental refresh; does not guess a similar handle
python -m training.scraper_pipeline refresh-brand --brand exact_instagram_handle --posts-per-brand 20 --dry-run
python -m training.scraper_pipeline refresh-brand --brand exact_instagram_handle --posts-per-brand 20

# Read-only stale-brand report
python -m training.scraper_pipeline stale-brands --days 60
```

Ingestion audit logs are written to `data/ingestion_audit/<batch>.jsonl` and are ignored by Git. The preview score is guidance only: database writes still require both an approved row and the explicit `--approve` flag. Re-running a batch uses the existing duplicate-safe post/image checks.

The read-only `GET /api/admin/catalog/scale` endpoint requires `X-Admin-Key` matching backend `ADMIN_API_KEY`.
