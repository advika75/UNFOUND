create extension if not exists "pgcrypto";

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  brand text not null,
  price numeric(10, 2) not null check (price >= 0),
  image_url text,
  niche_score numeric(3, 2) not null default 0 check (niche_score >= 0 and niche_score <= 1),
  created_at timestamptz not null default timezone('utc', now())
);

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
  trend_score numeric(3, 2) not null default 0 check (trend_score >= 0 and trend_score <= 1)
);

alter table public.brands
  add column if not exists id uuid default gen_random_uuid(),
  add column if not exists name text,
  add column if not exists brand_name text,
  add column if not exists instagram_username text,
  add column if not exists category text,
  add column if not exists profile_url text,
  add column if not exists instagram_profile_url text,
  add column if not exists profile_picture_url text,
  add column if not exists post_url text,
  add column if not exists followers integer default 0,
  add column if not exists engagement_rate numeric(5, 2) default 0,
  add column if not exists niche_score numeric(3, 2) default 0,
  add column if not exists trend_score numeric(3, 2) default 0;

update public.brands
set followers = 0
where followers is null;

update public.brands
set engagement_rate = 0
where engagement_rate is null;

update public.brands
set niche_score = 0
where niche_score is null;

update public.brands
set trend_score = 0
where trend_score is null;

alter table public.brands
  alter column id set default gen_random_uuid(),
  alter column name set not null,
  alter column followers set default 0,
  alter column followers set not null,
  alter column engagement_rate set default 0,
  alter column engagement_rate set not null,
  alter column niche_score set default 0,
  alter column niche_score set not null,
  alter column trend_score set default 0,
  alter column trend_score set not null;

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
on public.brands (instagram_username);

alter table public.products
  add column if not exists brand_id uuid references public.brands (id) on delete restrict,
  add column if not exists product_name text,
  add column if not exists brand_name text,
  add column if not exists item_name text,
  add column if not exists product_url text,
  add column if not exists category_id bigint,
  add column if not exists embedding vector(512),
  add column if not exists likes_count integer not null default 0,
  add column if not exists comments_count integer not null default 0,
  add column if not exists source_hashtag text,
  add column if not exists scraped_at timestamptz;

update public.products
set
  brand_name = coalesce(brand_name, brand),
  item_name = coalesce(item_name, name),
  product_name = coalesce(product_name, item_name, name)
where brand_name is null
   or item_name is null
   or product_name is null;

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

alter table public.products
  alter column brand_id set not null;

create table if not exists public.interactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  product_id uuid not null references public.products (id) on delete cascade,
  action text not null check (action in ('like', 'view')),
  timestamp timestamptz not null default timezone('utc', now())
);

create index if not exists products_created_at_idx on public.products (created_at desc);
create index if not exists products_niche_score_idx on public.products (niche_score desc);
create index if not exists brands_category_idx on public.brands (category);
create index if not exists brands_niche_score_idx on public.brands (niche_score desc);
create index if not exists brands_followers_idx on public.brands (followers desc);
create index if not exists interactions_user_id_idx on public.interactions (user_id);
create index if not exists interactions_product_id_idx on public.interactions (product_id);

alter table public.products enable row level security;
alter table public.brands enable row level security;
alter table public.interactions enable row level security;

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

drop policy if exists "Users can create their own interactions" on public.interactions;
create policy "Users can create their own interactions"
on public.interactions
for insert
to authenticated
with check (auth.uid() = user_id);

drop policy if exists "Users can view their own interactions" on public.interactions;
create policy "Users can view their own interactions"
on public.interactions
for select
to authenticated
using (auth.uid() = user_id);
