-- Apply after catalog_vector_schema.sql and unfound_experience_schema.sql.
-- Additive quality metadata only; no product/category rows are changed here.

-- Remove legacy anonymous scraper write policies if they were ever applied.
-- Service-role ingestion bypasses RLS and does not require public mutation rights.
drop policy if exists "Scraper can insert brands" on public.brands;
drop policy if exists "Scraper can update brands" on public.brands;
drop policy if exists "Scraper can insert products" on public.products;
drop policy if exists "Scraper can update products" on public.products;

insert into public.categories (parent_id,name,slug)
values (null,'Uncategorized','uncategorized')
on conflict (slug) do update set name=excluded.name;

alter table public.products drop constraint if exists products_category_confidence_check;
alter table public.products drop constraint if exists products_image_status_check;
alter table public.products drop constraint if exists products_classification_source_check;
alter table public.products drop constraint if exists products_catalog_status_check;
alter table public.brand_categories drop constraint if exists brand_categories_source_check;
alter table public.brand_categories drop constraint if exists brand_categories_confidence_check;
alter table public.brand_categories drop constraint if exists brand_categories_supporting_count_check;
alter table public.brand_categories drop constraint if exists brand_categories_average_confidence_check;

alter table public.products
  add column if not exists classifier_confidence double precision,
  add column if not exists normalized_main_category text,
  add column if not exists normalized_subcategory text,
  add column if not exists audience text,
  add column if not exists classification_source text,
  add column if not exists catalog_status text not null default 'ACTIVE',
  add column if not exists original_image_url text,
  add column if not exists image_status text not null default 'UNKNOWN',
  add constraint products_category_confidence_check check (
    classifier_confidence is null or classifier_confidence between 0 and 1
  ),
  add constraint products_image_status_check check (
    image_status in ('VALID','MISSING','BROKEN','EXPIRED_OR_FORBIDDEN','TIMEOUT','INVALID_URL','RECOVERY_REQUIRED','UNRECOVERABLE','UNKNOWN')
  ),
  add constraint products_classification_source_check check (
    classification_source is null or classification_source in ('deterministic','metadata','ai','heuristic','openai','manual','legacy')
  ),
  add constraint products_catalog_status_check check (
    catalog_status in ('ACTIVE','NON_PRODUCT','MANUAL_REVIEW')
  );

alter table public.brand_categories
  add column if not exists source text not null default 'product_derived',
  add column if not exists confidence double precision,
  add column if not exists supporting_product_count integer not null default 0,
  add column if not exists supporting_product_ids jsonb not null default '[]'::jsonb,
  add column if not exists average_confidence double precision,
  add constraint brand_categories_source_check check (source in ('product_derived','manual','ai_classified')),
  add constraint brand_categories_confidence_check check (confidence is null or confidence between 0 and 1),
  add constraint brand_categories_supporting_count_check check (supporting_product_count >= 0),
  add constraint brand_categories_average_confidence_check check (average_confidence is null or average_confidence between 0 and 1);

create index if not exists products_category_confidence_idx
on public.products(category_id,classifier_confidence desc);

create index if not exists products_image_status_idx
on public.products(image_status);

-- Run once in the Supabase dashboard before image repair:
insert into storage.buckets (id,name,public,file_size_limit,allowed_mime_types)
values
  ('product-images','product-images',true,12582912,array['image/jpeg','image/png','image/webp']),
  ('brand-images','brand-images',true,12582912,array['image/jpeg','image/png','image/webp'])
on conflict (id) do update set public=true,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;

alter table public.categories enable row level security;
drop policy if exists "Categories are viewable by everyone" on public.categories;
create policy "Categories are viewable by everyone" on public.categories
for select to anon, authenticated using (true);

alter table public.brand_categories enable row level security;
drop policy if exists "Brand categories are viewable by everyone" on public.brand_categories;
create policy "Brand categories are viewable by everyone" on public.brand_categories
for select to anon, authenticated using (true);

-- Public buckets still require an explicit object SELECT policy. No anonymous
-- INSERT/UPDATE/DELETE policies are created; service-role operations bypass RLS.
drop policy if exists "Public can read UNFOUND catalog images" on storage.objects;
create policy "Public can read UNFOUND catalog images" on storage.objects
for select to anon, authenticated
using (bucket_id in ('product-images','brand-images'));
