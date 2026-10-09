-- Records, as a replayable migration, a live data backfill already applied
-- directly via the Supabase REST API (service-role key) in an earlier session --
-- this file did not exist at the time; it's written now so the change has a
-- durable, reviewable record in the repo instead of living only in a since-
-- cleared scratchpad script.
--
-- Root cause: categories.audience was populated for newer categories (added by
-- unfound_experience_schema.sql, e.g. 'women-kurtis') but never backfilled for
-- the original women-*/men-* leaf categories from catalog_vector_schema.sql
-- (e.g. 'women-tops', 'men-shirts') -- the categories holding the large
-- majority of real products. Confirmed live, before this backfill, all 23 rows
-- below had audience IS NULL; that is the only prior state that ever existed
-- for them, so no separate value-by-value backup is needed to make this exact
-- and safe to revert.
--
-- Idempotent: `where audience is null` means re-running this is a no-op once
-- applied; it will never overwrite a value that was deliberately set to
-- something else afterward.
--
-- NOT applied by this commit -- this is the migration file only, for review.

update public.categories set audience = 'MEN' where id in (
  3, 4, 7, 8, 9, 10, 11, 24, 25, 26
) and slug like 'men-%' and audience is null;

update public.categories set audience = 'WOMEN' where id in (
  5, 6, 12, 13, 14, 15, 16, 17, 18, 20, 21, 22, 23
) and slug like 'women-%' and audience is null;

-- Revert: restores the exact prior state (NULL for all 23 rows). Not run by
-- this commit either -- kept here as the paired, ready-to-use rollback.
--
-- update public.categories set audience = null where id in (
--   3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 21, 22, 23, 24, 25, 26
-- );
