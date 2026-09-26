-- Apply after catalog_vector_schema.sql and lexical_search_function_migration.sql.
-- Additive column on two existing function outputs. Nothing dropped or destroyed
-- except the functions themselves, which are immediately recreated (Postgres
-- requires drop+recreate to change a `returns table` column list).
--
-- Root cause this fixes: categories.audience is already correctly populated
-- (verified: categories.slug='women-kurtis' has audience='WOMEN'), but neither
-- match_products nor lexical_search_products ever selected it, and
-- gender_match_score() in backend/app.py only checked the PRODUCT's own
-- audience column (populated for well under 4% of rows) plus a substring
-- search against the category DISPLAY NAME (which for many real categories,
-- e.g. "Kurtis", never contains the literal word "women" even though the
-- category unambiguously is women's). Both retrieval functions already join
-- public.categories -- this just also selects its audience column through.

drop function if exists public.match_products(vector, double precision, integer, bigint);

create function public.match_products(
  query_embedding vector(512),
  match_threshold float default 0.35,
  match_count int default 16,
  filter_category_id bigint default null
)
returns table (
  id uuid,
  brand_id uuid,
  brand_name text,
  category text,
  category_audience text,
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
  )
  select
    p.id,
    p.brand_id,
    p.brand_name,
    c.name as category,
    c.audience as category_audience,
    p.item_name,
    p.description,
    p.image_url,
    p.product_url,
    p.price,
    p.source,
    p.niche_score,
    p.likes_count,
    p.comments_count,
    p.source_hashtag,
    p.scraped_at,
    b.followers as brand_follower_count,
    b.niche_score as brand_niche_score,
    b.profile_picture_url as brand_profile_picture_url,
    coalesce(b.instagram_profile_url, b.profile_url) as brand_instagram_profile_url,
    p.category_id,
    1 - (p.embedding <=> query_embedding) as similarity
  from public.products p
  left join public.brands b on b.id = p.brand_id
  left join public.categories c on c.id = p.category_id
  where p.embedding is not null
    and coalesce(p.catalog_status, 'ACTIVE') = 'ACTIVE'
    and 1 - (p.embedding <=> query_embedding) >= match_threshold
    and (
      filter_category_id is null
      or p.category_id in (select id from category_scope)
    )
  order by p.embedding <=> query_embedding
  limit match_count;
$$;

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
  category_audience text,
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
      p.id, p.brand_id, p.brand_name, c.name as category, c.audience as category_audience,
      p.item_name, p.description,
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
      p.id, p.brand_id, p.brand_name, c.name as category, c.audience as category_audience,
      p.item_name, p.description,
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
      and greatest(word_similarity(query_text, p.product_name), word_similarity(query_text, p.brand)) > 0.35
      and (select n from fts_total) < fallback_threshold
    order by similarity desc
    limit greatest(match_count - (select n from fts_total), 0)
  )
  select * from fts_ranked
  union all
  select * from trigram_ranked;
$$;
