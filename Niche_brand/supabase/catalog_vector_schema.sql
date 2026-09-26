create extension if not exists "pgcrypto";
create extension if not exists "vector";

create table if not exists public.categories (
  id bigserial primary key,
  parent_id bigint references public.categories (id) on delete restrict,
  name text not null,
  slug text not null unique,
  created_at timestamptz not null default timezone('utc', now()),
  constraint categories_slug_format_chk check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
  constraint categories_not_self_parent_chk check (parent_id is null or parent_id <> id),
  constraint categories_sibling_name_unique unique (parent_id, name)
);

create index if not exists categories_parent_id_idx
on public.categories (parent_id);

-- Root categories use the natural slug key. PostgreSQL assigns IDs, preserving
-- any IDs and foreign-key references already present in a partial/live schema.
insert into public.categories (parent_id, name, slug)
values
  (null, 'Men', 'men'),
  (null, 'Women', 'women'),
  (null, 'Home Decor', 'home-decor'),
  (null, 'Uncategorized', 'uncategorized')
on conflict (slug) do update set name = excluded.name;

-- Seed intermediate groups first so a fresh database can resolve leaf parents.
with desired(parent_slug, name, slug) as (
  values
    ('men', 'Men Clothing', 'men-clothing'),
    ('men', 'Men Accessories', 'men-accessories'),
    ('women', 'Women Clothing', 'women-clothing'),
    ('women', 'Women Accessories', 'women-accessories')
)
insert into public.categories (parent_id, name, slug)
select parent.id, desired.name, desired.slug
from desired
join public.categories parent on parent.slug = desired.parent_slug
on conflict (slug) do update set
  name = excluded.name,
  parent_id = excluded.parent_id;

-- Older versions seeded explicit IDs. Advance the actual generated sequence
-- without renumbering rows or moving it backwards. Identity-backed schemas can
-- return null here, in which case PostgreSQL already manages the sequence.
do $$
declare
  category_sequence text := pg_get_serial_sequence('public.categories', 'id');
  maximum_id bigint;
  sequence_value bigint;
begin
  if category_sequence is not null then
    select coalesce(max(id), 0) into maximum_id from public.categories;
    execute format('select last_value from %s', category_sequence) into sequence_value;
    if maximum_id > sequence_value then
      perform setval(category_sequence, maximum_id, true);
    end if;
  end if;
end $$;

-- Resolve every leaf hierarchy edge by parent slug rather than generated IDs.
with desired(parent_slug, name, slug) as (
  values
    ('men-clothing', 'Men Jeans', 'men-jeans'),
    ('men-clothing', 'Men Shirts', 'men-shirts'),
    ('men-clothing', 'Men Jackets', 'men-jackets'),
    ('men-accessories', 'Men Bags', 'men-bags'),
    ('men-accessories', 'Men Shoes', 'men-shoes'),
    ('women-clothing', 'Women Jeans', 'women-jeans'),
    ('women-clothing', 'Women Dresses', 'women-dresses'),
    ('women-clothing', 'Women Tops', 'women-tops'),
    ('women-clothing', 'Women Jackets', 'women-jackets'),
    ('women-accessories', 'Women Bags', 'women-bags'),
    ('women-accessories', 'Women Shoes', 'women-shoes'),
    ('women-accessories', 'Women Jewelry', 'women-jewelry'),
    ('women-clothing', 'Women Bottoms', 'women-bottoms'),
    ('women-clothing', 'Women Ethnic Wear', 'women-ethnic-wear'),
    ('women-clothing', 'Women Co-ords', 'women-co-ords'),
    ('women-clothing', 'Women Activewear', 'women-activewear'),
    ('men-clothing', 'Men T-shirts', 'men-t-shirts'),
    ('men-clothing', 'Men Bottoms', 'men-bottoms'),
    ('men-clothing', 'Men Ethnic Wear', 'men-ethnic-wear'),
    ('women-accessories', 'Sunglasses', 'accessories-sunglasses'),
    ('women-accessories', 'Watches', 'accessories-watches'),
    ('women-accessories', 'Belts', 'accessories-belts'),
    ('women-accessories', 'Hair Accessories', 'accessories-hair'),
    ('women-accessories', 'Other Accessories', 'accessories-other')
)
insert into public.categories (parent_id, name, slug)
select parent.id, desired.name, desired.slug
from desired
join public.categories parent on parent.slug = desired.parent_slug
on conflict (slug) do update set
  name = excluded.name,
  parent_id = excluded.parent_id;

create table if not exists public.brands (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  brand_name text,
  instagram_username text,
  category text,
  profile_url text,
  instagram_profile_url text,
  profile_picture_url text,
  post_url text,
  followers integer not null default 0 check (followers >= 0),
  engagement_rate numeric(5, 2) not null default 0 check (engagement_rate >= 0),
  niche_score numeric(3, 2) not null default 0 check (niche_score >= 0 and niche_score <= 1),
  trend_score numeric(3, 2) not null default 0 check (trend_score >= 0 and trend_score <= 1),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

alter table public.brands
  add column if not exists category text,
  add column if not exists brand_name text,
  add column if not exists instagram_username text,
  add column if not exists profile_url text,
  add column if not exists instagram_profile_url text,
  add column if not exists profile_picture_url text,
  add column if not exists post_url text,
  add column if not exists followers integer not null default 0,
  add column if not exists engagement_rate numeric(5, 2) not null default 0,
  add column if not exists niche_score numeric(3, 2) not null default 0,
  add column if not exists trend_score numeric(3, 2) not null default 0,
  add column if not exists created_at timestamptz not null default timezone('utc', now()),
  add column if not exists updated_at timestamptz not null default timezone('utc', now());

create unique index if not exists brands_name_unique_idx
on public.brands (name);

update public.brands
set
  brand_name = coalesce(brand_name, name),
  instagram_username = coalesce(
    instagram_username,
    nullif(lower(regexp_replace(name, '[^a-zA-Z0-9._]', '', 'g')), '')
  )
where brand_name is null
   or instagram_username is null;

create unique index if not exists brands_instagram_username_unique_idx
on public.brands (instagram_username)
;

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  brand_id uuid references public.brands (id) on delete restrict,
  brand_name text not null,
  product_name text,
  item_name text not null,
  description text not null default '',
  image_url text,
  product_url text,
  likes_count integer not null default 0 check (likes_count >= 0),
  comments_count integer not null default 0 check (comments_count >= 0),
  source_hashtag text,
  scraped_at timestamptz,
  source text not null default 'instagram',
  price numeric(10, 2),
  niche_score numeric(3, 2) not null default 0 check (niche_score >= 0 and niche_score <= 1),
  category_id bigint not null references public.categories (id) on delete restrict,
  embedding vector(512),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

alter table public.products
  add column if not exists brand_id uuid references public.brands (id) on delete restrict,
  add column if not exists brand_name text,
  add column if not exists product_name text,
  add column if not exists item_name text,
  add column if not exists description text not null default '',
  add column if not exists product_url text,
  add column if not exists likes_count integer not null default 0,
  add column if not exists comments_count integer not null default 0,
  add column if not exists source_hashtag text,
  add column if not exists scraped_at timestamptz,
  add column if not exists source text not null default 'instagram',
  add column if not exists price numeric(10, 2),
  add column if not exists niche_score numeric(3, 2) not null default 0,
  add column if not exists category_id bigint references public.categories (id) on delete restrict,
  add column if not exists embedding vector(512),
  add column if not exists updated_at timestamptz not null default timezone('utc', now());

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'products'
      and column_name = 'brand'
  ) then
    execute 'update public.products set brand_name = coalesce(brand_name, brand) where brand_name is null';
  end if;

  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'products'
      and column_name = 'name'
  ) then
    execute 'update public.products set item_name = coalesce(item_name, name) where item_name is null';
  end if;

  update public.products
  set description = coalesce(description, '')
  where description is null;

  update public.products
  set product_name = coalesce(product_name, item_name)
  where product_name is null;
end $$;

insert into public.brands (name, brand_name, instagram_username, category, followers, niche_score)
select distinct
  p.brand_name,
  p.brand_name,
  nullif(lower(regexp_replace(p.brand_name, '[^a-zA-Z0-9._]', '', 'g')), ''),
  'Fashion',
  0,
  coalesce(p.niche_score, 0)
from public.products p
left join public.brands b
  on lower(b.name) = lower(p.brand_name)
  or lower(b.brand_name) = lower(p.brand_name)
  or lower(b.instagram_username) = lower(regexp_replace(p.brand_name, '[^a-zA-Z0-9._]', '', 'g'))
where p.brand_id is null
  and p.brand_name is not null
  and b.id is null
on conflict (instagram_username) do nothing;

update public.products p
set brand_id = b.id
from public.brands b
where p.brand_id is null
  and (
    lower(b.name) = lower(p.brand_name)
    or lower(b.brand_name) = lower(p.brand_name)
    or lower(b.instagram_username) = lower(regexp_replace(p.brand_name, '[^a-zA-Z0-9._]', '', 'g'))
  );

do $$
begin
  if not exists (select 1 from public.products where brand_id is null) then
    alter table public.products alter column brand_id set not null;
  else
    raise notice 'products.brand_id remains nullable because unmatched production rows exist';
  end if;
end $$;

create index if not exists products_category_id_idx
on public.products (category_id);

create index if not exists products_brand_id_idx
on public.products (brand_id);

create unique index if not exists products_source_product_url_unique_idx
on public.products (source, product_url);

create index if not exists products_embedding_hnsw_idx
on public.products
using hnsw (embedding vector_cosine_ops)
with (m = 16, ef_construction = 64)
where embedding is not null;

alter table public.products
  add column if not exists source_url text,
  add column if not exists classifier_confidence double precision,
  add column if not exists currency text not null default 'INR',
  add column if not exists subcategory text,
  add column if not exists instagram_post_id text,
  add column if not exists instagram_post_url text,
  add column if not exists catalog_status text not null default 'ACTIVE',
  add column if not exists metadata jsonb not null default '{}'::jsonb;

create unique index if not exists products_source_instagram_post_id_unique_idx
on public.products (source, instagram_post_id)
where instagram_post_id is not null and instagram_post_id <> '';

create index if not exists products_brand_normalized_name_idx
on public.products (brand_id, lower(product_name));

create table if not exists public.ingestion_failures (
  id uuid primary key default gen_random_uuid(),
  brand_id uuid references public.brands (id) on delete set null,
  brand_name text not null,
  stage text not null,
  message text not null,
  recoverable boolean not null default true,
  occurred_at timestamptz not null default timezone('utc', now()),
  resolved_at timestamptz
);

-- Required by unfound_experience_schema.sql. Creating it here makes the stated
-- four-file migration order work on a completely fresh database as well as a
-- database that previously used schema.sql.
create table if not exists public.interactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  product_id uuid not null references public.products (id) on delete cascade,
  action text not null,
  timestamp timestamptz not null default timezone('utc', now())
);

create index if not exists interactions_user_id_idx on public.interactions (user_id);
create index if not exists interactions_product_id_idx on public.interactions (product_id);

create index if not exists ingestion_failures_unresolved_brand_idx
on public.ingestion_failures (brand_id, occurred_at desc)
where resolved_at is null;

alter table public.ingestion_failures enable row level security;

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

alter table public.products enable row level security;
alter table public.brands enable row level security;

drop policy if exists "Products are viewable by everyone" on public.products;
create policy "Products are viewable by everyone"
on public.products
for select
to anon, authenticated
using (true);

drop policy if exists "Brands are viewable by everyone" on public.brands;
create policy "Brands are viewable by everyone"
on public.brands
for select
to anon, authenticated
using (true);
