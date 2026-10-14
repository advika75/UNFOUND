-- Rewrites stored image URLs from Supabase Storage to Cloudflare R2, AFTER
-- backend/migrate_images_to_r2.py has copied the bytes and you've spot-checked
-- a sample of the new R2 URLs in a browser (see the run order in the report).
--
-- Scope: only products.image_url and brands.profile_picture_url -- the two
-- columns backend/migrate_images_to_r2.py's two buckets (product-images,
-- brand-images) correspond to. The database connection, auth, and every other
-- table are untouched; this does not move the database to Neon or anywhere
-- else.
--
-- Idempotent: the WHERE clause only matches rows that still start with the old
-- Supabase storage prefix, so running this twice in a row is a no-op the
-- second time (nothing left to match).
--
-- Not applied by this commit. Do not run this until:
--   1. backend/migrate_images_to_r2.py has completed a REAL (non-dry-run) pass
--      for both buckets, with every row in data/r2_migration_map.json verified,
--   2. you've opened ~10 of the resulting R2 URLs in a browser and confirmed
--      the images load correctly.
--
-- Before running, replace EVERY occurrence of the literal placeholder
--   REPLACE_WITH_R2_PUBLIC_BASE_URL
-- below with your real R2_PUBLIC_BASE_URL, with NO trailing slash (e.g.
-- https://pub-xxxxxxxxxxxx.r2.dev or your custom domain). Nothing else in
-- STEP A/B/C needs editing. The commented-out REVERT block additionally uses
--   REPLACE_WITH_SUPABASE_PROJECT_URL
-- (e.g. https://abcdefgh.supabase.co, no trailing slash) -- only needed if
-- you ever uncomment and run that block.

-- ============================================================
-- STEP A -- run this first and note the counts (before rewrite)
-- ============================================================
select
  'products' as table_name,
  count(*) filter (where image_url like 'https://%.supabase.co/storage/v1/object/public/product-images/%') as on_supabase,
  count(*) filter (where image_url like 'REPLACE_WITH_R2_PUBLIC_BASE_URL/product-images/%') as on_r2,
  count(*) filter (where image_url is not null
                    and image_url not like 'https://%.supabase.co/storage/v1/object/public/product-images/%'
                    and image_url not like 'REPLACE_WITH_R2_PUBLIC_BASE_URL/product-images/%') as other
from public.products
union all
select
  'brands' as table_name,
  count(*) filter (where profile_picture_url like 'https://%.supabase.co/storage/v1/object/public/brand-images/%') as on_supabase,
  count(*) filter (where profile_picture_url like 'REPLACE_WITH_R2_PUBLIC_BASE_URL/brand-images/%') as on_r2,
  count(*) filter (where profile_picture_url is not null
                    and profile_picture_url not like 'https://%.supabase.co/storage/v1/object/public/brand-images/%'
                    and profile_picture_url not like 'REPLACE_WITH_R2_PUBLIC_BASE_URL/brand-images/%') as other
from public.brands;

-- ============================================================
-- STEP B -- the rewrite itself
-- ============================================================
update public.products
set image_url = regexp_replace(
  image_url,
  '^https://[^/]+\.supabase\.co/storage/v1/object/public/',
  'REPLACE_WITH_R2_PUBLIC_BASE_URL/'
)
where image_url like 'https://%.supabase.co/storage/v1/object/public/product-images/%';

update public.brands
set profile_picture_url = regexp_replace(
  profile_picture_url,
  '^https://[^/]+\.supabase\.co/storage/v1/object/public/',
  'REPLACE_WITH_R2_PUBLIC_BASE_URL/'
)
where profile_picture_url like 'https://%.supabase.co/storage/v1/object/public/brand-images/%';

-- ============================================================
-- STEP C -- run the STEP A query again and compare: on_supabase should now
-- be 0 for both tables, on_r2 should equal what on_supabase was before.
-- ============================================================

-- ============================================================
-- REVERT (commented out -- uncomment and run if you need to roll back).
-- Old Supabase URLs stay valid indefinitely (migrate_images_to_r2.py never
-- deletes the Supabase originals), so this is safe to run at any time after
-- STEP B, with no re-copy needed.
-- ============================================================
-- update public.products
-- set image_url = regexp_replace(
--   image_url,
--   '^REPLACE_WITH_R2_PUBLIC_BASE_URL/',
--   'REPLACE_WITH_SUPABASE_PROJECT_URL/storage/v1/object/public/'
-- )
-- where image_url like 'REPLACE_WITH_R2_PUBLIC_BASE_URL/product-images/%';
--
-- update public.brands
-- set profile_picture_url = regexp_replace(
--   profile_picture_url,
--   '^REPLACE_WITH_R2_PUBLIC_BASE_URL/',
--   'REPLACE_WITH_SUPABASE_PROJECT_URL/storage/v1/object/public/'
-- )
-- where profile_picture_url like 'REPLACE_WITH_R2_PUBLIC_BASE_URL/brand-images/%';
