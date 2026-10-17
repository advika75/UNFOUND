-- Reconcile exact-type RPC signatures with backend/app.py.
-- This file is for review/manual application only; it has not been run against a database.

-- Remove the live three-argument signatures and the mistaken scored overload.
drop function if exists public.exact_type_products(text, vector, integer);
drop function if exists public.exact_type_products_scored(text, vector, integer);

-- Also remove the intended base overload so its return type can be recreated exactly.
drop function if exists public.exact_type_products(text, uuid[], integer);

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

-- Restrict execution to the requested API roles (the existing migration has no explicit grants).
revoke all on function public.exact_type_products(text, uuid[], integer) from public;
revoke all on function public.exact_type_products_scored(vector, text, uuid[], integer) from public;
grant execute on function public.exact_type_products(text, uuid[], integer) to anon, authenticated, service_role;
grant execute on function public.exact_type_products_scored(vector, text, uuid[], integer) to anon, authenticated, service_role;

-- Verification: exact_type_products returns one jsonb column containing product objects.
select *
from public.exact_type_products('\mkurti\M', '{}'::uuid[], 5);

-- Verification: scored returns product jsonb plus similarity float. NULL embedding isolates shape/signature.
select *
from public.exact_type_products_scored(null::vector(512), '\mkurti\M', '{}'::uuid[], 5);

notify pgrst, 'reload schema';
