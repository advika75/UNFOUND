# UNFOUND image recovery runbook

Run commands from the repository root. Never place service-role or admin secrets in `Niche_brand/.env`.

1. Generate an admin key locally, then paste it into `backend/.env` as `ADMIN_API_KEY`:

   `python -c "import secrets; print(secrets.token_urlsafe(48))"`

2. Confirm `SUPABASE_SERVICE_ROLE_KEY` exists only in `backend/.env`.

3. Apply `Niche_brand/supabase/storage_setup.sql` in the Supabase SQL editor.

4. Verify public reads and server-only upload/update access:

   `python -m backend.category_quality verify-storage`

5. Establish a local manual Instagram session (no password CLI arguments):

   `python -m training.scraper_pipeline instagram-login`

6. Check the stored session:

   `python -m training.scraper_pipeline instagram-session-check`

7. Inspect ten current images without writes:

   `python -m backend.category_quality repair-images --dry-run --limit 10`

   For expired Instagram assets, preview source-post recovery:

   `python -m backend.category_quality recover-images --only-broken --dry-run --limit 10`

8. After reviewing the dry-run, migrate ten working external images:

   `python -m backend.category_quality repair-images --limit 10`

   Then recover up to ten expired Instagram assets from their source posts:

   `python -m backend.category_quality recover-images --only-broken --limit 10`

9. Recheck health:

   `python -m backend.category_quality image-health --network`

10. Open Home, Search, Brands, brand profile, category tiles, Stylish Tops, Modern Ethnic, New Discoveries, and Fresh Drops at `http://127.0.0.1:5173`.

11. Only after manual pilot review, run larger bounded batches. A full pass is intentionally never automatic:

   `python -m backend.category_quality repair-images`

   `python -m backend.category_quality recover-images --only-broken`

   `python -m backend.category_quality repair-brand-images`

12. Verify Fresh Drops diagnostics:

   `curl http://127.0.0.1:8000/api/discovery/feeds`

13. Recalculate training readiness:

   `python -m backend.training_data report`

14. Add exact Instagram URLs/handles to `data/curated_brands.csv`, then preview:

   `python -m training.scraper_pipeline ingest-curated-brands --file data/curated_brands.csv --dry-run`

Category-specific recovery supports `--category tops`, `--product-type kurta`, `--brand NAME`, and `--product-id UUID`.
