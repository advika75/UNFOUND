-- Additive only: creates (or replaces, if re-run) one new function; nothing
-- existing is dropped or altered. Safe alongside the existing
-- public.exact_type_products (exact_type_products_migration.sql), which stays
-- exactly as-is and keeps being used whenever AUGMENTATION_SCORING=none.
--
-- Why: exact_type_products finds candidates by a word-boundary text match and
-- has no embedding-similarity signal at all -- every row it returns ties at
-- the exact same final_score (confirmed live: 18 of 20 "kurti" candidates tied
-- at 0.7100), which makes their relative order decided entirely by the ranker's
-- tie-break rather than anything about the product.
--
-- This function adds a `similarity` column, computed in SQL as cosine
-- similarity against the caller's query_embedding (same
-- `1 - (embedding <=> query_embedding)` match_products already uses) -- but
-- the embedding column itself is never selected into the returned payload, so
-- this doesn't change exact_type_products' existing "no embedding over the
-- wire" cost profile.
--
-- Ordering: the WHERE clause (word-boundary regex over the same concatenated
-- text exact_type_products already matches on) runs over the full table with
-- no LIMIT applied yet, so every matching row gets scored -- only THEN is the
-- result ordered by similarity desc and cut to row_limit. The original design
-- ordered by p.ctid (physical row position) and limited to row_limit BEFORE
-- scoring, which for any type with more than row_limit matches (e.g. "dress"
-- has 170) meant similarity only ever reordered an arbitrary first 100 rows
-- and never got a chance to surface the true best 100. `nulls last` is
-- explicit because Postgres's default for `desc` is NULLS FIRST -- without it,
-- every row with no embedding would sort to the very top, not the bottom.
--
-- Grants: none added here, matching exact_type_products (no GRANT statement
-- exists for it anywhere in this repo's migrations either) -- both rely on
-- Postgres's default PUBLIC execute privilege on newly created functions.
-- Not granting anything beyond what the existing function already has.
--
-- NOT applied to the live database by this commit -- this is the migration
-- file only, for review and manual application.

create or replace function public.exact_type_products_scored(
  query_embedding vector(512),
  type_pattern text,
  exclude_ids uuid[] default '{}',
  row_limit int default 100
)
returns table (
  product jsonb,
  similarity float
)
language sql stable
as $$
  select
    to_jsonb(p) - 'embedding' - 'search_vector' as product,
    case when p.embedding is null then null else 1 - (p.embedding <=> query_embedding) end as similarity
  from public.products p
  where coalesce(p.catalog_status, '') <> 'NON_PRODUCT'
    and not (p.id = any(exclude_ids))
    and lower(concat_ws(' ',
          coalesce(p.product_name, ''), coalesce(p.item_name, ''), coalesce(p.description, ''), '',
          coalesce(p.subcategory, ''), coalesce(p.normalized_main_category, ''),
          coalesce(p.normalized_subcategory, ''), coalesce(p.audience, ''),
          coalesce(p.metadata ->> 'colour', ''), coalesce(p.metadata ->> 'color', ''),
          coalesce((p.metadata -> 'style')::text, ''), coalesce((p.metadata -> 'fit')::text, '')
        )) ~ type_pattern
  order by similarity desc nulls last, p.id
  limit row_limit;
$$;
