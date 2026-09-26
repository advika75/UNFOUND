-- Read-only migration verification. Run in the Supabase SQL Editor.
with required_tables(name) as (
  values ('categories'),('products'),('brands'),('brand_categories')
), required_columns(table_name,column_name) as (
  values
    ('products','category_id'),('products','classifier_confidence'),
    ('products','normalized_main_category'),('products','normalized_subcategory'),
    ('products','audience'),('products','classification_source'),
    ('products','catalog_status'),
    ('products','image_status'),('products','original_image_url'),
    ('brand_categories','source'),('brand_categories','confidence')
)
select jsonb_build_object(
  'tables', (select jsonb_object_agg(r.name, to_regclass('public.'||r.name) is not null) from required_tables r),
  'columns', (select jsonb_object_agg(c.table_name||'.'||c.column_name, exists(
    select 1 from information_schema.columns i where i.table_schema='public' and i.table_name=c.table_name and i.column_name=c.column_name
  )) from required_columns c),
  'rls', (select jsonb_object_agg(relname, relrowsecurity) from pg_class where oid in ('public.categories'::regclass,'public.brand_categories'::regclass)),
  'public_select_policies', (select coalesce(jsonb_agg(jsonb_build_object('table',tablename,'policy',policyname,'roles',roles,'command',cmd)),'[]'::jsonb)
    from pg_policies where schemaname='public' and tablename in ('categories','brand_categories') and cmd='SELECT'),
  'public_taxonomy_mutation_policy_count', (select count(*) from pg_policies
    where schemaname='public' and tablename in ('categories','brand_categories')
      and cmd in ('INSERT','UPDATE','DELETE','ALL') and roles && array['anon','authenticated']::name[]),
  'match_products_exact_signature', exists(
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname='match_products'
      and pg_get_function_identity_arguments(p.oid)='query_embedding vector, match_threshold double precision, match_count integer, filter_category_id bigint'
  ),
  'storage_buckets', (select coalesce(jsonb_object_agg(id,public),'{}'::jsonb) from storage.buckets where id in ('product-images','brand-images')),
  'category_count', (select count(*) from public.categories),
  'products_with_category_relationship', (select count(*) from public.products where category_id is not null),
  'validated_brand_category_count', (select count(*) from public.brand_categories where confidence >= 0.80)
) as unfound_catalog_verification;
