-- Optional development-only helper.
-- Production ingestion should use a server-side/service key instead of broad RLS policies.

drop policy if exists "Scraper can insert brands" on public.brands;
create policy "Scraper can insert brands"
on public.brands
for insert
to anon, authenticated
with check (true);

drop policy if exists "Scraper can update brands" on public.brands;
create policy "Scraper can update brands"
on public.brands
for update
to anon, authenticated
using (true)
with check (true);

drop policy if exists "Scraper can insert products" on public.products;
create policy "Scraper can insert products"
on public.products
for insert
to anon, authenticated
with check (true);

drop policy if exists "Scraper can update products" on public.products;
create policy "Scraper can update products"
on public.products
for update
to anon, authenticated
using (true)
with check (true);
