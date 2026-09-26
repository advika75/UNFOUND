-- Guest-ready personalization layer. Existing catalog/auth tables are untouched.
-- profile_id is a private client-generated UUID today; auth_user_id can be
-- attached to the same profile when account authentication is enabled.

create table if not exists public.personalization_profiles (
  profile_id uuid primary key,
  auth_user_id uuid unique references auth.users(id) on delete set null,
  favourite_categories text[] not null default '{}',
  favourite_brand_ids uuid[] not null default '{}',
  preferred_styles text[] not null default '{}',
  preferred_colours text[] not null default '{}',
  min_price numeric,
  max_price numeric,
  gender_preference text,
  updated_at timestamptz not null default timezone('utc', now()),
  constraint personalization_price_range check (
    min_price is null or max_price is null or min_price <= max_price
  )
);

create table if not exists public.personalization_saved_products (
  profile_id uuid not null references public.personalization_profiles(profile_id) on delete cascade,
  product_id uuid not null references public.products(id) on delete cascade,
  created_at timestamptz not null default timezone('utc', now()),
  primary key (profile_id, product_id)
);

create table if not exists public.personalization_saved_brands (
  profile_id uuid not null references public.personalization_profiles(profile_id) on delete cascade,
  brand_id uuid not null references public.brands(id) on delete cascade,
  created_at timestamptz not null default timezone('utc', now()),
  primary key (profile_id, brand_id)
);

create table if not exists public.personalization_moodboards (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references public.personalization_profiles(profile_id) on delete cascade,
  name text not null check (length(trim(name)) between 1 and 120),
  description text not null default '',
  cover_image_url text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.personalization_moodboard_products (
  moodboard_id uuid not null references public.personalization_moodboards(id) on delete cascade,
  product_id uuid not null references public.products(id) on delete cascade,
  position integer not null default 0,
  created_at timestamptz not null default timezone('utc', now()),
  primary key (moodboard_id, product_id)
);

create table if not exists public.personalization_moodboard_brands (
  moodboard_id uuid not null references public.personalization_moodboards(id) on delete cascade,
  brand_id uuid not null references public.brands(id) on delete cascade,
  position integer not null default 0,
  created_at timestamptz not null default timezone('utc', now()),
  primary key (moodboard_id, brand_id)
);

create index if not exists personalization_saved_products_profile_idx
  on public.personalization_saved_products(profile_id, created_at desc);
create index if not exists personalization_saved_brands_profile_idx
  on public.personalization_saved_brands(profile_id, created_at desc);
create index if not exists personalization_moodboards_profile_idx
  on public.personalization_moodboards(profile_id, updated_at desc);

alter table public.personalization_profiles enable row level security;
alter table public.personalization_saved_products enable row level security;
alter table public.personalization_saved_brands enable row level security;
alter table public.personalization_moodboards enable row level security;
alter table public.personalization_moodboard_products enable row level security;
alter table public.personalization_moodboard_brands enable row level security;

-- Access is intentionally backend-only while profiles are guest capability IDs.
-- The FastAPI server uses SUPABASE_SERVICE_ROLE_KEY; no anon policies are added.
