# UNFOUND — AI Fashion & Lifestyle Discovery

A simple MVP for discovering niche and underrated brands with React, Vite, Tailwind CSS, and Supabase.

UNFOUND preserves the existing CLIP/FastAPI discovery flow while adding editorial
category navigation, richer brand and product profiles, saved products,
moodboards, and interaction-driven personalization.

Apply SQL in this order:

```text
supabase/catalog_vector_schema.sql
supabase/unfound_experience_schema.sql
```

The second migration is additive: it keeps existing products, brands, category
IDs, and embeddings, then adds the expanded category hierarchy,
`brand_categories`, user preferences, saved products/brands, moodboards, and
moodboard items. Guest moodboards and preferences use browser local storage;
the Supabase tables are ready for authenticated persistence.

## Folder structure

```text
hidden-gems-discovery-engine/
├── .env.example
├── index.html
├── package.json
├── postcss.config.js
├── README.md
├── supabase/
│   └── schema.sql
├── tailwind.config.js
├── vite.config.js
└── src/
    ├── App.jsx
    ├── components/
    │   └── ProductCard.jsx
    ├── index.css
    ├── lib/
    │   └── supabase.js
    ├── main.jsx
    └── pages/
        └── Home.jsx
```

## Setup

1. Install dependencies:

   ```bash
   npm install
   ```

2. Create a local env file:

   ```bash
   cp .env.example .env
   ```

3. Add your Supabase values to `.env`:

   ```env
   VITE_SUPABASE_URL=your-supabase-project-url
   VITE_SUPABASE_ANON_KEY=your-supabase-anon-key
   ```

4. Run the SQL in `supabase/schema.sql` inside the Supabase SQL editor.

5. Optional but recommended: run `supabase/seed.sql` to load sample brands and products for the discovery feed.

6. Start the app:

   ```bash
   npm run dev
   ```

## Supabase auth setup

1. In Supabase, go to `Authentication` -> `Providers` -> `Email`.
2. Make sure email auth is enabled.
3. For local development, add your site URL in `Authentication` -> `URL Configuration`.
   Use `http://localhost:5173`
4. If email confirmation is enabled, new users must confirm before signing in.

The app now includes a simple email sign-up and sign-in form so likes can be stored in `interactions`.

## Extension-ready notes

- `brands` gives us a clean place for recommendation features and trend signals.
- `interactions` can support event pipelines, embeddings, and user preference models later.
- The current Supabase client setup can be reused by a future Chrome extension or an Edge Function layer.
