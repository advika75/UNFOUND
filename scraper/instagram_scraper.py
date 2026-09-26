"""Compatibility entrypoint for Instagram product ingestion.

The project now has one canonical ingestion pipeline:

scrape_instagram()
-> save_brand_csv()
-> extract_product()
-> classify_category()
-> generate_embedding()
-> upsert_product()

Run this module if older scripts still call scraper.instagram_scraper; it delegates
to training.scraper_pipeline. Brand discovery is cached in scraper/data/brands.csv for
audit/debugging only; product ingestion does not read from that CSV and no brand
seed SQL is generated.
"""

from __future__ import annotations

from training.scraper_pipeline import main


if __name__ == "__main__":
    main()
