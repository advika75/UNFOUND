---
title: UNFOUND Search API
emoji: 🔍
colorFrom: indigo
colorTo: pink
sdk: docker
dockerfile: Dockerfile.spaces
app_port: 7860
pinned: false
---

# AI Fashion Discovery & Recommendation Platform

AI Fashion Discovery & Recommendation Platform powered by CLIP embeddings, Supabase pgvector, and multimodal search.

## Deployment note: latency

This API is deployed to Hugging Face Spaces, which run in the US/EU. Supabase is in
ap-northeast-1 (Tokyo), so every database round trip crosses that distance -- expect
~300ms per round trip and roughly ~1s total search latency, similar to running the API
locally against the same Supabase project. This is not a bug or a regression; it's the
cost of Spaces not being co-located with the database (unlike the AWS Lambda deployment
path, which runs in the same region as Supabase specifically to avoid this).

The platform helps users discover visually and semantically similar fashion products through text search, image search, category filters, and recommendation-style result ranking.

## Features

- Text search for fashion, accessories, and home decor products.
- Image search using uploaded PNG/JPEG references.
- Similar product retrieval with CLIP embeddings.
- Recommended product ranking through Supabase pgvector similarity search.
- Category filtering for clothing, accessories, and home decor.
- Canonical Instagram product ingestion pipeline with validation, retries, duplicate-safe upserts, and CLIP embeddings.
- OpenAI structured classification for scraped product metadata with a heuristic fallback.

## Architecture

- `backend/`: FastAPI service with CLIP embeddings, Supabase RPC search, and upload validation.
- `Niche_brand/`: Vite React frontend for text search, image search, category filtering, similar products, and recommendations.
- `training/`: Canonical product ingestion pipeline with Instagram scraping, product extraction, classification, CLIP embedding, and Supabase upsert.
- `scraper/`: Backward-compatible ingestion entrypoint that delegates to `training.scraper_pipeline`.
- `Niche_brand/supabase/`: SQL migrations for products, categories, pgvector indexes, and `match_products`.

## Database Setup

Run this file in the Supabase SQL editor:

```text
Niche_brand/supabase/catalog_vector_schema.sql
```

It enables `pgvector`, creates product/category/brand tables, adds vector indexes, defines duplicate-safe product upsert keys, and defines the `match_products` RPC used by the backend.

## Backend Startup

```bash
cd /Users/advikamishra/Desktop/My_project
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Required backend environment variables:

```text
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
OPENAI_API_KEY=
```

FastAPI uses the anon key for RLS-protected public reads. CLI repair, ingestion,
and Storage upload code require the service-role key. Never use the service-role
key in a `VITE_*` variable or frontend file.

## Frontend Startup

```bash
cd /Users/advikamishra/Desktop/My_project/Niche_brand
npm install
cp .env.example .env
npm run dev
```

## Scraper Pipeline

```bash
cd /Users/advikamishra/Desktop/My_project
source backend/.venv/bin/activate
python -m training.scraper_pipeline --hashtags SlowFashionBrand,IndieLabel --per-hashtag-limit 12
```

Canonical flow:

```text
scrape_instagram()
  -> save_brand_csv()
  -> extract_product()
  -> classify_category()
  -> generate_embedding()
  -> upsert_product()
```

The scraper reads Instagram hashtag pages, extracts product candidates, classifies each candidate, computes `clip-ViT-B-32` embeddings locally, upserts brand metadata, and upserts products into Supabase using `(source, product_url)` duplicate detection. It does not use `brands.csv`, generated brand seed SQL, or manual SQL imports as product ingestion sources.

The Instagram scraper uses Selenium with a persistent Chrome profile at `scraper/chrome_profile` by default. On the first run, log into Instagram manually in the opened Chrome window; the same profile is reused on later runs.

`scraper/data/brands.csv` is still maintained as a local cache and audit trail of discovered Instagram brands. Each scrape appends or updates brand metadata by `instagram_username`, then continues through the product-first pipeline. The CSV is not used as the source for product ingestion, Supabase product writes, or pgvector indexing.

Optional environment variables:

```text
SELENIUM_PROFILE_DIR=scraper/chrome_profile
INGEST_HEADLESS=false
INGEST_PER_HASHTAG_LIMIT=12
INGEST_BATCH_SIZE=20
BRAND_CACHE_PATH=scraper/data/brands.csv
CHROME_MAJOR_VERSION=146
INSTAGRAM_LOGIN_TIMEOUT_SECONDS=300
```

## Smoke Test

```bash
pytest tests/test_product_ingestion_smoke.py
```

The smoke test uses fakes for Instagram, CLIP, Supabase, and search RPC. It verifies scrape -> embedding -> Supabase product upsert -> `/api/discover` result wiring without calling external services.

## Main API

```text
POST /api/discover
```

Form fields:

- `text_query`: optional text search query.
- `image_file`: optional PNG/JPEG upload, max 5MB.
- `category_id`: optional category filter.

Send exactly one of `text_query` or `image_file`.

## Building the GemMode Catalog

The canonical catalog builder is `training.scraper_pipeline`; the legacy
`scraper.instagram_scraper` command delegates to it. Apply
`Niche_brand/supabase/catalog_vector_schema.sql` before ingestion. The migration
adds the controlled category rows, product provenance/metadata columns, pgvector
index, and duplicate-protection indexes.

Required environment variables in `backend/.env`:

```text
SUPABASE_URL=                         # Supabase project URL
SUPABASE_ANON_KEY=                    # publishable key for safe reads
SUPABASE_SERVICE_ROLE_KEY=            # backend-only administrative writes
OPENAI_API_KEY=                       # optional; heuristics are used without it
```

Optional Instagram credentials (`INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD`) can
be stored in `scraper/.env`. If omitted, Chrome opens for manual login. The login
is retained in `scraper/chrome_profile/`, which is intentionally ignored by Git.
Never commit that directory, cookies, `.env` files, or service keys.

Install and test from the project root:

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
python -m pytest -q
```

Small one-brand test (20 recent posts):

```bash
python -m training.scraper_pipeline --source brands --limit-brands 1 --posts-per-brand 20
```

Ten-brand batch:

```bash
python -m training.scraper_pipeline --source brands --limit-brands 10 --posts-per-brand 20
```

Full catalog run (all Supabase brands):

```bash
python -m training.scraper_pipeline --source brands --limit-brands 0 --posts-per-brand 20
```

Add `--brand-name label_name` to select a specific brand by display name or
Instagram username. Runs are resumable: `(source, product_url)` and Instagram
post IDs are unique, while upserts update a previously seen canonical URL.
Failures are isolated per brand and product.

Verify catalog size in the Supabase SQL editor:

```sql
select count(*) as product_count from public.products;
select brand_name, count(*) as products
from public.products
group by brand_name
order by products desc;
```

Common failures:

- Login timeout: sign in to the Chrome window and rerun; do not delete the profile.
- Challenge/rate limit: stop the run, complete Instagram verification, and retry later.
- No posts found: confirm the brand has public `/p/` or `/reel/` posts and increase the scroll limit.
- Invalid/expired image URL: the product is skipped and the batch continues.
- Embedding failure: confirm `sentence-transformers`, Pillow, and CLIP model access; the failure is logged per product.
- Supabase column/RPC error: reapply `catalog_vector_schema.sql` with a privileged SQL role.

Typical output:

```text
[BRAND] Processing example_label
[POST] 20 posts found
[SKIP] Non-product post: no reliable fashion product signal
[PRODUCT] Black Linen Dress
[PRICE] ₹2499
[CATEGORY] Women Dresses
[EMBEDDING] generated
[UPSERT] inserted
Batch ingestion summary | Brands attempted: 1 | Brands successful: 1 | Posts scanned: 20 | Product posts detected: 12 | Products inserted: 10 | Products updated: 2 | Duplicates skipped: 3 | Embedding failures: 0 | Errors: 0
```

## Catalog Health

The catalog validator reads brands, products, embeddings, provenance fields, and
unresolved ingestion failures without modifying products. It reports completeness,
data-quality warnings, duplicate candidates, per-brand ingestion health, and one
overall readiness result. This score is `catalog_quality_score`; it is not Gem Score.

Run the readable health report:

```bash
python -m backend.catalog_quality catalog-health
```

Machine-readable output and detailed reports:

```bash
python -m backend.catalog_quality catalog-health --json
python -m backend.catalog_quality issues --json
python -m backend.catalog_quality brands-health --json
python -m backend.catalog_quality failed-brands --json
```

Admin API endpoints expose the same validator:

```text
GET /api/admin/catalog/health
GET /api/admin/catalog/issues?issue=MISSING_IMAGE&brand_id=<uuid>&limit=50
GET /api/admin/brands/health
```

Readiness meanings:

- `READY`: at least 100 products, image/category coverage at least 90%, and embedding coverage at least 95%.
- `NEEDS_ATTENTION`: the catalog is usable but does not meet every ready threshold.
- `NOT_READY`: fewer than 10 products or image/embedding coverage below 50%.

Thresholds can be configured with `CATALOG_READY_MIN_PRODUCTS`,
`CATALOG_READY_IMAGE_COVERAGE`, `CATALOG_READY_CATEGORY_COVERAGE`,
`CATALOG_READY_EMBEDDING_COVERAGE`, `CATALOG_NOT_READY_MIN_PRODUCTS`,
`CATALOG_MIN_REASONABLE_PRICE`, and `CATALOG_MAX_REASONABLE_PRICE`.

Safe rollout commands:

```bash
# Scrape and classify one brand, but do not write or generate embeddings
python -m training.scraper_pipeline --source brands --limit-brands 1 --posts-per-brand 20 --dry-run

# Live batches
python -m training.scraper_pipeline --source brands --limit-brands 1 --posts-per-brand 20 --resume
python -m training.scraper_pipeline --source brands --limit-brands 5 --posts-per-brand 20 --resume
python -m training.scraper_pipeline --source brands --limit-brands 20 --posts-per-brand 20 --resume
python -m training.scraper_pipeline --source brands --limit-brands 0 --posts-per-brand 20 --resume
```

Inspect failures and retry one failed brand:

```bash
python -m backend.catalog_quality failed-brands --json
python -m training.scraper_pipeline --source brands --brand failed_brand_name --limit-brands 1 --posts-per-brand 20 --resume
```

Dry-run mode still requires Supabase access in brand mode so it can load the brand
list, but it performs no brand/product/failure writes. Network image checking is
available through the reusable `image_status(..., network_check=True)` utility and
is disabled by default so catalog reports and tests remain fast and offline-safe.

## Category and Image Repair

Apply these files in order in the Supabase SQL Editor:

1. `Niche_brand/supabase/catalog_vector_schema.sql`
2. `Niche_brand/supabase/unfound_experience_schema.sql`
3. `Niche_brand/supabase/category_image_quality_migration.sql`
4. `Niche_brand/supabase/verify_unfound_catalog.sql`

The quality migration adds non-destructive confidence/image-health
fields, strengthens `brand_categories`, makes taxonomy rows readable, and creates
the public `product-images` Storage bucket. Live updates and Storage uploads require
a server/service-role Supabase key; a publishable key is intentionally insufficient.

Preview and apply deterministic category repairs:

```bash
backend/.venv/bin/python -m backend.category_quality reclassify-catalog --dry-run --only-low-confidence --limit 50
backend/.venv/bin/python -m backend.category_quality reclassify-catalog --only-low-confidence
backend/.venv/bin/python -m backend.category_quality rebuild-brand-categories
```

Audit unresolved metadata, preview the staged deterministic → metadata → AI
classifier on at most 50 rows, and export the remaining review queue:

```bash
backend/.venv/bin/python -m backend.category_quality ambiguity-audit
backend/.venv/bin/python -m backend.category_quality reclassify-catalog --only-low-confidence --use-ai --dry-run --limit 50
backend/.venv/bin/python -m backend.category_quality manual-review --limit 100
```

After reviewing the AI preview, the explicitly authorized full command is:

```bash
backend/.venv/bin/python -m backend.category_quality reclassify-catalog --only-low-confidence --use-ai
```

AI responses are restricted to the live taxonomy, validated against parent and
audience relationships, and accepted only when grounded final confidence is at
least 0.80. Equivalent inputs are cached within a run; accepted AI results also
store a fingerprint in product metadata. Obvious non-product posts are retained
with `catalog_status=NON_PRODUCT` and excluded from discovery.

Scope repairs with `--brand NAME`, `--limit N`, `--only-uncategorized`, or `--force`.
Low-confidence products stay unchanged/uncategorized. Mapping rebuilds preserve
manual mappings and derive product mappings only from products at or above 0.80.

Preview image health/stable Storage copying, then apply it:

```bash
python -m backend.category_quality repair-images --dry-run
python -m backend.category_quality repair-images
```

Expired Instagram CDN URLs require the persisted authenticated Selenium session.
Preview candidates first, then revisit their original posts and store fresh images:

```bash
backend/.venv/bin/python -m backend.category_quality recover-images --dry-run --only-broken
backend/.venv/bin/python -m backend.category_quality recover-images --only-broken
```

Enable stable Storage copying for new ingestion with:

```env
SUPABASE_STORE_IMAGES=true
SUPABASE_PRODUCT_IMAGE_BUCKET=product-images
```

Category/image distribution report:

```bash
python -m backend.category_quality category-health
python -m backend.category_quality verify-schema
```
# UNFOUND
