-- UNFOUND experience layer. Apply after catalog_vector_schema.sql.
-- Existing brands, products, category IDs and embeddings are preserved.

alter table public.categories
  add column if not exists audience text,
  add column if not exists display_order integer not null default 0;

insert into public.categories (name, slug, parent_id, audience, display_order)
values
  ('Unisex', 'unisex', null, 'UNISEX', 3),
  ('Gifting', 'gifting', null, null, 4),
  ('Gadgets', 'gadgets', null, 'UNISEX', 6),
  ('Watches', 'watches', null, 'UNISEX', 7),
  ('Accessories', 'accessories', null, 'UNISEX', 8)
on conflict (slug) do update set audience = excluded.audience, display_order = excluded.display_order;

update public.categories set audience = 'MEN', display_order = 2 where slug = 'men';
update public.categories set audience = 'WOMEN', display_order = 1 where slug = 'women';
update public.categories set display_order = 5 where slug = 'home-decor';

with desired(parent_slug, name, slug, audience, display_order) as (
  values
    ('women','Gym Wear','women-gym-wear','WOMEN',3), ('women','Kurtis','women-kurtis','WOMEN',4),
    ('women','Formal Wear','women-formal-wear','WOMEN',5), ('women','Party Wear','women-party-wear','WOMEN',6),
    ('women','Co-ord Sets','women-coord-sets','WOMEN',7), ('women','Skirts & Shorts','women-skirts-shorts','WOMEN',8),
    ('women','Lingerie','women-lingerie','WOMEN',9), ('men','Gym Wear','men-gym-wear','MEN',4),
    ('men','Underwear','men-underwear','MEN',5), ('men','Formal Wear','men-formal-wear','MEN',7),
    ('unisex','Socks','unisex-socks','UNISEX',1), ('unisex','Hoodies','unisex-hoodies','UNISEX',2),
    ('unisex','Sneakers','unisex-sneakers','UNISEX',3), ('unisex','Bags','unisex-bags','UNISEX',4),
    ('unisex','Streetwear','unisex-streetwear','UNISEX',5), ('gifting','Gifts for Her','gifts-for-her','WOMEN',1),
    ('gifting','Gifts for Him','gifts-for-him','MEN',2), ('gifting','Birthday','gifts-birthday',null,3),
    ('gifting','Anniversary','gifts-anniversary',null,4), ('gifting','Under ₹1000','gifts-under-1000',null,5),
    ('gifting','Premium Gifts','premium-gifts',null,6), ('home-decor','Furniture','home-furniture',null,1),
    ('home-decor','Lighting','home-lighting',null,2), ('home-decor','Wall Decor','home-wall-decor',null,3),
    ('home-decor','Vases','home-vases',null,4), ('home-decor','Bedroom','home-bedroom',null,5),
    ('home-decor','Desk Decor','home-desk-decor',null,6), ('gadgets','Tech Accessories','gadget-accessories','UNISEX',1),
    ('gadgets','Audio','gadget-audio','UNISEX',2), ('gadgets','Desk Gadgets','desk-gadgets','UNISEX',3),
    ('gadgets','Smart Devices','smart-devices','UNISEX',4), ('gadgets','Lifestyle Tech','lifestyle-tech','UNISEX',5),
    ('watches','Minimal','watches-minimal','UNISEX',1), ('watches','Luxury','watches-luxury','UNISEX',2),
    ('watches','Vintage','watches-vintage','UNISEX',3), ('watches','Smart Watches','smart-watches','UNISEX',4),
    ('accessories','Jewellery','accessories-jewellery','UNISEX',1), ('accessories','Bags','accessories-bags','UNISEX',2),
    ('accessories','Sunglasses','accessories-sunglasses-all','UNISEX',3), ('accessories','Belts','accessories-belts-all','UNISEX',4),
    ('accessories','Hair Accessories','accessories-hair-all','UNISEX',5), ('accessories','Wallets','accessories-wallets','UNISEX',6)
)
insert into public.categories (parent_id, name, slug, audience, display_order)
select parent.id, desired.name, desired.slug, desired.audience, desired.display_order
from desired join public.categories parent on parent.slug = desired.parent_slug
on conflict (slug) do update set parent_id=excluded.parent_id, audience=excluded.audience, display_order=excluded.display_order;

create table if not exists public.brand_categories (
  brand_id uuid not null references public.brands(id) on delete cascade,
  category_id bigint not null references public.categories(id) on delete cascade,
  is_primary boolean not null default false,
  created_at timestamptz not null default timezone('utc',now()),
  primary key (brand_id, category_id)
);

-- Brand/category rows are intentionally not backfilled from legacy product data.
-- backend.category_quality rebuild-brand-categories creates them only after a
-- product has a validated classifier_confidence >= 0.80.

create table if not exists public.user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  avatar_url text,
  style_profile jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc',now()),
  updated_at timestamptz not null default timezone('utc',now())
);

create table if not exists public.user_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  preferences jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc',now())
);

alter table public.interactions drop constraint if exists interactions_action_check;
alter table public.interactions add constraint interactions_action_check check (
  action in ('search','view','like','save','dismiss','click','brand_view','brand_save','category_view','moodboard_create')
);

create table if not exists public.saved_products (
  user_id uuid not null references auth.users(id) on delete cascade,
  product_id uuid not null references public.products(id) on delete cascade,
  created_at timestamptz not null default timezone('utc',now()),
  primary key(user_id,product_id)
);
create table if not exists public.saved_brands (
  user_id uuid not null references auth.users(id) on delete cascade,
  brand_id uuid not null references public.brands(id) on delete cascade,
  created_at timestamptz not null default timezone('utc',now()),
  primary key(user_id,brand_id)
);
create table if not exists public.moodboards (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  cover_image_url text,
  detected_mood jsonb not null default '{}'::jsonb,
  is_public boolean not null default false,
  created_at timestamptz not null default timezone('utc',now()),
  updated_at timestamptz not null default timezone('utc',now())
);
create table if not exists public.moodboard_items (
  id uuid primary key default gen_random_uuid(),
  moodboard_id uuid not null references public.moodboards(id) on delete cascade,
  product_id uuid references public.products(id) on delete cascade,
  uploaded_image_url text,
  note text,
  position integer not null default 0,
  created_at timestamptz not null default timezone('utc',now()),
  constraint moodboard_item_source check (product_id is not null or uploaded_image_url is not null)
);

alter table public.brand_categories enable row level security;
alter table public.user_profiles enable row level security;
alter table public.user_preferences enable row level security;
alter table public.saved_products enable row level security;
alter table public.saved_brands enable row level security;
alter table public.moodboards enable row level security;
alter table public.moodboard_items enable row level security;

drop policy if exists "Brand categories are public" on public.brand_categories;
drop policy if exists "Users manage own profiles" on public.user_profiles;
drop policy if exists "Users manage own preferences" on public.user_preferences;
drop policy if exists "Users manage saved products" on public.saved_products;
drop policy if exists "Users manage saved brands" on public.saved_brands;
drop policy if exists "Users manage moodboards" on public.moodboards;
drop policy if exists "Users manage moodboard items" on public.moodboard_items;

create policy "Brand categories are public" on public.brand_categories
for select to anon, authenticated using (true);
create policy "Users manage own profiles" on public.user_profiles for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "Users manage own preferences" on public.user_preferences for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "Users manage saved products" on public.saved_products for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "Users manage saved brands" on public.saved_brands for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "Users manage moodboards" on public.moodboards for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "Users manage moodboard items" on public.moodboard_items for all using (
  exists(select 1 from public.moodboards m where m.id=moodboard_id and m.user_id=auth.uid())
) with check (
  exists(select 1 from public.moodboards m where m.id=moodboard_id and m.user_id=auth.uid())
);

create index if not exists products_category_created_idx on public.products(category_id,created_at desc);
create index if not exists brand_categories_category_idx on public.brand_categories(category_id,brand_id);
create index if not exists interactions_user_action_idx on public.interactions(user_id,action,timestamp desc);
create index if not exists moodboards_user_updated_idx on public.moodboards(user_id,updated_at desc);
