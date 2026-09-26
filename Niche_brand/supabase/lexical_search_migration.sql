-- Apply after catalog_vector_schema.sql and category_image_quality_migration.sql.
-- Additive only: one generated column, one GIN index, two trigram indexes.
-- Nothing existing is altered, dropped, or backfilled by a rewrite.
--
-- Schema reality check (run against the live catalog before writing this):
--   product_name, brand, description, category_id: 100% populated (1369/1369).
--   normalized_main_category / normalized_subcategory: 146/1369 (10.7%).
--   subcategory: 21/1369 (1.5%).
--   metadata with real product_type/style/colour keys: 21/1369 (1.5%) -- the
--     Stage 8 curated-brand additions only. The real keys are "primary_colour",
--     "secondary_colours", "style", "hashtags", "product_type", "product_family"
--     (NOT "colour"/"color" -- backend/app.py's product_search_text() currently
--     reads the wrong keys and finds nothing; unrelated pre-existing bug, not
--     touched here).
--   There is no stored product_type column at all; it is only ever derived at
--     query time by identify_product() in backend/product_taxonomy.py. Weight B
--     below substitutes the closest real stored classification columns instead.
--
-- Net effect: weight A (product name) has full coverage. Weights B and C are
-- real but sparse (10.7% and 1.5% respectively) until more rows are classified
-- -- this migration does not change that, it just makes what IS there
-- full-text searchable.

create extension if not exists "pg_trgm";

alter table public.products
  add column if not exists search_vector tsvector
  generated always as (
    setweight(to_tsvector('english', coalesce(product_name, '')), 'A')
    ||
    setweight(to_tsvector('english',
      coalesce(normalized_main_category, '') || ' ' ||
      coalesce(normalized_subcategory, '') || ' ' ||
      coalesce(subcategory, '') || ' ' ||
      coalesce(metadata ->> 'product_type', '') || ' ' ||
      coalesce(metadata ->> 'product_family', '')
    ), 'B')
    ||
    setweight(to_tsvector('english',
      coalesce(metadata ->> 'primary_colour', '') || ' ' ||
      coalesce((metadata -> 'secondary_colours')::text, '') || ' ' ||
      coalesce((metadata -> 'style')::text, '') || ' ' ||
      coalesce((metadata -> 'hashtags')::text, '')
    ), 'C')
    ||
    setweight(to_tsvector('english',
      coalesce(description, '') || ' ' ||
      coalesce(brand, '') || ' ' ||
      coalesce(metadata ->> 'caption', '')
    ), 'D')
  ) stored;

create index if not exists products_search_vector_idx
  on public.products using gin (search_vector);

create index if not exists products_product_name_trgm_idx
  on public.products using gin (product_name gin_trgm_ops);

create index if not exists products_brand_trgm_idx
  on public.products using gin (brand gin_trgm_ops);
