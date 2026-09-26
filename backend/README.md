# AI Fashion Discovery Backend

FastAPI backend for hierarchical category filtering, CLIP embeddings, and multimodal search.

## 1. Apply Supabase SQL

Run this file in the Supabase SQL editor:

```text
Niche_brand/supabase/catalog_vector_schema.sql
```

For persistent preferences, saves, and moodboards, also apply the additive
`Niche_brand/supabase/personalization_schema.sql` migration. The personalization
API is backend-only and requires `SUPABASE_SERVICE_ROLE_KEY`; never expose that
key through a `VITE_` environment variable.

It creates:

- `categories` with self-referencing `parent_id`
- `brands` metadata
- `products.category_id`
- `products.brand_id`
- `products.source` and `products.product_url` duplicate key
- `products.embedding vector(512)`
- HNSW cosine index on product embeddings

## 2. Install on a clean machine

```bash
cd /path/to/My_project
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

Fill `backend/.env` with `SUPABASE_URL` and a publishable/anon key in
`SUPABASE_KEY` (or `SUPABASE_ANON_KEY`). `OPENAI_API_KEY` is only required by
the optional OpenAI-assisted classification commands. `DATABASE_URL` is only
required by direct PostgreSQL maintenance/backfill commands. Never put a
service-role key or OpenAI key in the Vite frontend environment.

## 3. Run API

```bash
source backend/.venv/bin/activate
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Run this command from the project root because the application imports the
`backend` package. From inside `backend/`, the equivalent command is:

```bash
cd ..
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

The first startup downloads `sentence-transformers/clip-ViT-B-32` into the
standard Hugging Face cache. Later startups reuse that model cache.

## 4. Search

Text search:

```bash
curl -X POST http://127.0.0.1:8000/api/discover \
  -F "text_query=black linen womens dress" \
  -F "category_id=13"
```

Image search:

```bash
curl -X POST http://127.0.0.1:8000/api/discover \
  -F "image_file=@/path/to/image.jpg"
```

Send exactly one of `text_query` or `image_file`.

Recommendations:

```bash
curl -X POST http://127.0.0.1:8000/api/recommend \
  -F "text_query=black linen womens dress"
```

The recommendation endpoint returns `similar_products` from pgvector product matches and `similar_brands` aggregated from the matched products.

## 8. Similarity architecture

Discovery is a two-stage server-side search. First, the API encodes text or an
uploaded image with `sentence-transformers/clip-ViT-B-32` using normalized
512-dimensional vectors. Supabase `match_products` uses cosine distance and its
HNSW index to return up to `DISCOVER_CANDIDATE_LIMIT` candidates. The backend
then fetches metadata only for those candidate IDs, applies explicit filters,
and reranks the candidates before returning 16 results.

The default text formula is semantic 0.36 + lexical 0.12 + category 0.14 +
product type 0.18 + colour 0.08 + style/fit 0.05 + gender 0.03 + niche 0.04.
The default image formula is visual 0.72 + category 0.09 + product type 0.06 +
colour 0.03 + style/fit 0.03 + gender 0.02 + niche 0.05. All weights can be
overridden through the variables listed in `.env.example`.

Natural-language text searches extract product type, colour, style, fit,
gender, and an `under`/`below`/`up to` maximum price. Explicit price and gender
constraints are applied before final ranking. Results are cached in-process for
five minutes by embedding and filter combination.

Run the real-catalog evaluation set while the API is running:

```bash
python -m backend.evaluate_search_quality
python -m backend.evaluate_search_quality --image /path/to/product.jpg
```

## 5. Classify Product

```bash
python -m backend.classify_products \
  --brand "Example Brand" \
  --item "Relaxed straight jeans" \
  --description "Menswear denim with five-pocket styling" \
  --tags "men, denim, jeans"
```

## 6. Backfill Embeddings

```bash
python -m backend.embed_products
```

## 7. Canonical Product Ingestion

```bash
python -m training.scraper_pipeline --hashtags SlowFashionBrand,IndieLabel --per-hashtag-limit 12
```

This is the only product ingestion path. The legacy `scraper.instagram_scraper` module delegates to this pipeline.
