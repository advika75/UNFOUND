create extension if not exists vector;
create extension if not exists "pgcrypto";

alter table public.brands
  add column if not exists brand_name text,
  add column if not exists instagram_username text,
  add column if not exists instagram_profile_url text,
  add column if not exists profile_picture_url text;

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
  add column if not exists brand_id uuid,
  add column if not exists brand_name text,
  add column if not exists product_name text,
  add column if not exists item_name text,
  add column if not exists product_url text,
  add column if not exists category_id bigint,
  add column if not exists embedding vector(512),
  add column if not exists likes_count integer not null default 0,
  add column if not exists comments_count integer not null default 0,
  add column if not exists source_hashtag text,
  add column if not exists scraped_at timestamptz;

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
    execute 'update public.products set item_name = coalesce(item_name, name), product_name = coalesce(product_name, item_name, name) where item_name is null or product_name is null';
  end if;
end $$;

update public.products
set product_name = coalesce(product_name, item_name)
where product_name is null;

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
  drop constraint if exists products_brand_id_fkey;

alter table public.products
  add constraint products_brand_id_fkey
  foreign key (brand_id)
  references public.brands (id)
  on delete restrict;

alter table public.products
  alter column brand_id set not null;

create index if not exists products_brand_id_idx
on public.products (brand_id);
