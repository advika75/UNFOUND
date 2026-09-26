-- Apply after lexical_search_migration.sql (needs products.search_vector and pg_trgm).
-- Adds one new function only. Nothing existing is altered or dropped.
--
-- Mirrors public.match_products' exact output shape (same column list, same
-- "similarity" column name) so the two retrievers are interchangeable from
-- Python: format_product() and everything downstream already knows how to
-- read this shape regardless of which retriever produced it.
--
-- FTS-first, trigram-fallback: runs websearch_to_tsquery + ts_rank_cd first;
-- only if that returns fewer than fallback_threshold rows does it backfill
-- the remainder (up to match_count) with trigram similarity on product_name/
-- brand, excluding ids already found by FTS. This is for misspelled queries
-- and exact brand-name lookups the FTS dictionary wouldn't stem correctly.

drop function if exists public.lexical_search_products(text, bigint, int, int);

create function public.lexical_search_products(
  query_text text,
  filter_category_id bigint default null,
  match_count int default 100,
  fallback_threshold int default 5
)
returns table (
  id uuid,
  brand_id uuid,
  brand_name text,
  category text,
  item_name text,
  description text,
  image_url text,
  product_url text,
  price numeric,
  source text,
  niche_score numeric,
  likes_count integer,
  comments_count integer,
  source_hashtag text,
  scraped_at timestamptz,
  brand_follower_count integer,
  brand_niche_score numeric,
  brand_profile_picture_url text,
  brand_instagram_profile_url text,
  category_id bigint,
  similarity float
)
language sql stable
as $$
  with recursive category_scope as (
    select c.id
    from public.categories c
    where filter_category_id is not null
      and c.id = filter_category_id
    union all
    select child.id
    from public.categories child
    join category_scope parent on child.parent_id = parent.id
  ),
  fts_ranked as (
    select
      p.id, p.brand_id, p.brand_name, c.name as category, p.item_name, p.description,
      p.image_url, p.product_url, p.price, p.source, p.niche_score, p.likes_count,
      p.comments_count, p.source_hashtag, p.scraped_at,
      b.followers as brand_follower_count, b.niche_score as brand_niche_score,
      b.profile_picture_url as brand_profile_picture_url,
      coalesce(b.instagram_profile_url, b.profile_url) as brand_instagram_profile_url,
      p.category_id,
      ts_rank_cd(p.search_vector, websearch_to_tsquery('english', query_text)) as similarity
    from public.products p
    left join public.brands b on b.id = p.brand_id
    left join public.categories c on c.id = p.category_id
    where coalesce(p.catalog_status, 'ACTIVE') = 'ACTIVE'
      and p.search_vector @@ websearch_to_tsquery('english', query_text)
      and (filter_category_id is null or p.category_id in (select id from category_scope))
    order by similarity desc
    limit match_count
  ),
  fts_total as (
    select count(*) as n from fts_ranked
  ),
  trigram_ranked as (
    select
      p.id, p.brand_id, p.brand_name, c.name as category, p.item_name, p.description,
      p.image_url, p.product_url, p.price, p.source, p.niche_score, p.likes_count,
      p.comments_count, p.source_hashtag, p.scraped_at,
      b.followers as brand_follower_count, b.niche_score as brand_niche_score,
      b.profile_picture_url as brand_profile_picture_url,
      coalesce(b.instagram_profile_url, b.profile_url) as brand_instagram_profile_url,
      p.category_id,
      greatest(word_similarity(query_text, p.product_name), word_similarity(query_text, p.brand)) as similarity
    from public.products p
    left join public.brands b on b.id = p.brand_id
    left join public.categories c on c.id = p.category_id
    where coalesce(p.catalog_status, 'ACTIVE') = 'ACTIVE'
      and (filter_category_id is null or p.category_id in (select id from category_scope))
      and p.id not in (select id from fts_ranked)
      -- word_similarity (not the whole-string similarity()/% operator) because
      -- product_name is almost always "{brand} {type}" -- comparing a short
      -- misspelled query against that whole string dilutes similarity below
      -- any reasonable threshold even for an obvious typo. Verified against
      -- real data: similarity('nacklace','lamania.galicia Necklace')=0.26
      -- (would never match at the standard 0.3 cutoff) vs.
      -- word_similarity('nacklace','lamania.galicia Necklace')=0.56.
      and greatest(word_similarity(query_text, p.product_name), word_similarity(query_text, p.brand)) > 0.35
      and (select n from fts_total) < fallback_threshold
    order by similarity desc
    limit greatest(match_count - (select n from fts_total), 0)
  )
  select * from fts_ranked
  union all
  select * from trigram_ranked;
$$;
