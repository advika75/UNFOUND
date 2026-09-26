-- Apply after lexical_search_migration.sql. Adds one new function; nothing existing is touched.
--
-- Replaces a client-side scan: backend/app.py used to download the WHOLE products table (select *,
-- including the 512-dim embedding, ~12 MB over 2 pages) on nearly every text search, only to keep
-- the rows whose text matches the requested product type. This does that filter in the database
-- and returns at most row_limit rows without the embedding.
--
-- The predicate reproduces backend.app.product_search_text() + product_taxonomy.type_match() exactly:
-- a word-boundary regex over the lower-cased concatenation of name, item_name, description, (category:
-- always '' on raw product rows), subcategory, normalized_main_category, normalized_subcategory,
-- audience and the metadata colour/color/style/fit keys. type_pattern comes from
-- product_taxonomy.type_regex(). Verified: identical ordered results for all 47 taxonomy types on the
-- live catalog. It cannot use an index (regex over a concatenation); at this table size the scan is a
-- few ms and never reads the embedding column. Add a trigram/FTS prefilter if the catalog grows ~100x.
--
-- Returns raw product rows as jsonb (minus embedding/search_vector), i.e. exactly the row shape the
-- old select * produced, so format_product() output is unchanged. Ordered by physical row position to
-- match the old unordered LIMIT/OFFSET paging.

drop function if exists public.exact_type_products(text, uuid[], int);

create function public.exact_type_products(
  type_pattern text,
  exclude_ids uuid[] default '{}',
  row_limit int default 100
)
returns setof jsonb
language sql stable
as $$
  select to_jsonb(p) - 'embedding' - 'search_vector'
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
  order by p.ctid
  limit row_limit;
$$;
