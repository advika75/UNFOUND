-- Idempotent public-read/server-write Storage setup for UNFOUND catalog imagery.
-- Service-role requests bypass RLS. No anonymous INSERT/UPDATE/DELETE policy is created.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('product-images', 'product-images', true, 12582912, array['image/jpeg','image/png','image/webp']),
  ('brand-images', 'brand-images', true, 8388608, array['image/jpeg','image/png','image/webp'])
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "Public read UNFOUND product images" on storage.objects;
create policy "Public read UNFOUND product images"
on storage.objects for select
to public
using (bucket_id = 'product-images');

drop policy if exists "Public read UNFOUND brand images" on storage.objects;
create policy "Public read UNFOUND brand images"
on storage.objects for select
to public
using (bucket_id = 'brand-images');
