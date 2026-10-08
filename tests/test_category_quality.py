import asyncio
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from backend.app import app, category_catalog
from backend.category_quality import (
    AUTO_CATEGORY_THRESHOLD,
    SERVICE_ROLE_ERROR,
    classify_product_category,
    classify_catalog_image,
    classify_with_pipeline,
    deterministic_pipeline,
    extract_product_name,
    final_ai_confidence,
    image_url_status,
    is_non_product,
    parse_ai_classification,
    product_supports_category,
    rebuild_brand_categories,
    run_recover_images,
    run_manual_review,
    run_repair_images,
    upload_stable_brand_image,
)


TAXONOMY = {
    "women": {"id": 2, "slug": "women", "parent_id": None, "audience": "WOMEN"},
    "women-formal-wear": {"id": 33, "slug": "women-formal-wear", "parent_id": 2, "audience": "WOMEN"},
    "accessories": {"id": 40, "slug": "accessories", "parent_id": None, "audience": "UNISEX"},
    "accessories-jewellery": {"id": 41, "slug": "accessories-jewellery", "parent_id": 40, "audience": "UNISEX"},
}


def product(name: str, description: str = "", **extra):
    return {"id": name, "product_name": name, "item_name": name, "description": description, **extra}


def test_real_product_examples_resolve_without_random_fallbacks() -> None:
    assert classify_product_category(product("Silver Hoop Earrings")).category_slug == "accessories-jewellery"
    assert classify_product_category(product("Black Straight Leg Jeans", "women's denim")).category_slug == "women-jeans"
    tee = classify_product_category(product("Oversized Graphic Tee", "unisex streetwear"))
    assert tee.category_slug == "unisex-streetwear"
    assert classify_product_category(product("Embroidered Kurti")).category_slug == "women-kurtis"
    assert classify_product_category(product("Leather Wallet")).category_slug == "accessories-wallets"
    assert classify_product_category(product("Minimal Analog Watch")).category_slug == "watches-minimal"
    ambiguous = classify_product_category(product("Summer Drop"))
    assert ambiguous.category_slug is None
    assert ambiguous.confidence < 0.8


def test_instagram_vision_guess_alt_text_is_not_treated_as_explicit_evidence() -> None:
    # Real bug caught in Stage 9: "dancingkind Ethnic Top" was confidently stored as
    # women-dresses (0.95) purely because Instagram's own auto-generated alt text
    # hedged between "tunic, dress" -- a computer-vision guess, not authored content.
    # The classifier must not treat this the same as a real caption/hashtag.
    ambiguous_vision_text = product(
        "dancingkind Ethnic Top",
        'Photo by DANCING KIND on June 03, 2026. May be an image of one or more '
        'people, people standing, tunic, dress and text that says "The woman I am '
        'dressing for now is not the woman I used to be.".',
    )
    decision = classify_product_category(ambiguous_vision_text)
    assert decision.category_slug != "women-dresses"
    assert decision.confidence < AUTO_CATEGORY_THRESHOLD

    # A real, authored hashtag/caption mentioning "dress" must still work normally --
    # this only discounts Instagram's own auto-generated vision-guess text.
    real_dress = product("Floral Midi Dress", "Our new floral midi dress for women #dress")
    assert classify_product_category(real_dress).category_slug == "women-dresses"


def test_metadata_enrichment_and_name_extraction() -> None:
    weak = product("New Drop", metadata={"caption": "Our silver hoops for every day", "hashtags": "#jewellery"})
    decision = deterministic_pipeline(weak)
    assert decision.category_slug == "accessories-jewellery"
    assert decision.source == "metadata"
    assert extract_product_name(weak)[:2] == ("Silver Hoop Earrings", .90)


def test_generic_fallback_name_is_not_trusted_as_independent_evidence() -> None:
    # Real bug fixed in Stage 9: when clean_product_name()/fallback_product_name()
    # can't find a real title and falls back to a bare category word ("Jewellery"),
    # Stage A previously trusted that bare word alone as if it were a deliberately
    # authored, confident product title -- confidently reclassifying a product using
    # only its own already-weak fallback name as "evidence", with no other real
    # signal anywhere. This is circular: the name IS the guess, not proof of it.
    no_other_evidence = product("Jewellery", "Photo shared by someone. Beautiful piece for you.")
    decision = deterministic_pipeline(no_other_evidence)
    assert decision.confidence < AUTO_CATEGORY_THRESHOLD

    # A bare fallback name with genuine independent corroboration elsewhere (a real
    # hashtag, not just the name itself) must still resolve normally.
    corroborated = product("Jewellery", "Handmade with love.", metadata={"hashtags": "#jewellery"})
    corroborated_decision = deterministic_pipeline(corroborated)
    assert corroborated_decision.category_slug == "accessories-jewellery"
    assert corroborated_decision.confidence >= AUTO_CATEGORY_THRESHOLD

    # A real, deliberately authored name must be unaffected.
    real_name = product("Silver Hoop Earrings", "Our new drop is live")
    assert deterministic_pipeline(real_name).category_slug == "accessories-jewellery"


def test_ai_structured_unknown_and_invalid_taxonomy_are_rejected() -> None:
    unknown = parse_ai_classification({"category_slug": None, "confidence": .35, "reason": "Insufficient", "evidence": []}, product("Mood"), TAXONOMY)
    assert unknown.decision.category_slug is None
    invalid = parse_ai_classification({"category_slug": "invented", "confidence": .99}, product("Mood"), TAXONOMY)
    assert invalid.decision.category_slug is None
    wrong_parent = parse_ai_classification({"category_slug": "women-formal-wear", "main_category_slug": "accessories", "confidence": .99}, product("Blazer"), TAXONOMY)
    assert wrong_parent.decision.confidence < .8


def test_ai_confidence_requires_grounded_evidence() -> None:
    item = product("Midnight Edit", "oversized black blazer for women", metadata={"hashtags": "#formalwear"})
    grounded = final_ai_confidence(item, "women-formal-wear", .96, ["oversized black blazer", "formalwear"])
    vague = final_ai_confidence(item, "women-formal-wear", .96, ["elegant mood"])
    assert grounded >= .80
    assert vague < .80


def test_deterministic_result_prevents_ai_call() -> None:
    class AI:
        @property
        def chat(self): return pytest.fail("AI was called for an obvious product")
    outcome = classify_with_pipeline(product("Silver Earrings"), TAXONOMY, ai_client=AI())
    assert outcome.decision.category_slug == "accessories-jewellery"
    assert not outcome.ai_called


def test_cached_ai_response_is_reused(monkeypatch) -> None:
    item = product("Midnight Edit", "dark tailored evening piece", metadata={})
    monkeypatch.setattr("backend.category_quality.call_ai_classifier", lambda *_args: pytest.fail("AI cache was ignored"))
    from backend.category_quality import classification_context, context_fingerprint
    fingerprint = context_fingerprint(classification_context(item), TAXONOMY)
    item["metadata"] = {"category_ai_cache": {"fingerprint": fingerprint, "result": {
        "category_slug": "women-formal-wear", "main_category_slug": "women", "audience": "women",
        "confidence": .95, "reason": "explicit tailoring", "evidence": ["tailored", "evening piece"],
        "suggested_name": "Black Blazer", "name_confidence": .9, "non_product": False,
    }}}
    outcome = classify_with_pipeline(item, TAXONOMY, ai_client=object())
    assert outcome.cache_hit and outcome.decision.confidence >= .8


def test_non_product_detection_remains_unresolved() -> None:
    item = product("Community Update", "We are hiring: job opening, apply now")
    assert is_non_product(item)[0]
    outcome = classify_with_pipeline(item, TAXONOMY, ai_client=None)
    assert outcome.non_product and outcome.decision.category_slug is None


def test_manual_review_includes_only_low_confidence(monkeypatch, capsys) -> None:
    rows = [
        product("Mystery Edit", classifier_confidence=.25, brand_name="Studio", image_url=None),
        product("Silver Earrings", classifier_confidence=.98, brand_name="Studio"),
    ]
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda _client, table: rows if table == "products" else [])
    run_manual_review(Namespace(limit=100, brand=None, category=None, reason=None), object())
    output = capsys.readouterr().out
    assert "Mystery Edit" in output
    assert "Silver Earrings" not in output


def test_image_url_status_is_offline_safe() -> None:
    assert image_url_status(None) == "MISSING"
    assert image_url_status("not-a-url") == "INVALID_URL"
    assert image_url_status("https://example.com/item.jpg") == "VALID"
    base = "https://example.supabase.co"
    assert classify_catalog_image(f"{base}/storage/v1/object/public/product-images/x.jpg", base) == "STABLE_SUPABASE"
    assert classify_catalog_image("https://images.unsplash.com/wrong.jpg", base) == "UNSPLASH_INVALID"
    assert classify_catalog_image("bad", base) == "MALFORMED"


def test_category_endpoint_excludes_unrelated_products_and_brands(monkeypatch) -> None:
    categories = {
        "women-jeans": {"id": 12, "slug": "women-jeans", "name": "Women Jeans", "parent_id": 2},
        "accessories-jewellery": {"id": 50, "slug": "accessories-jewellery", "name": "Jewellery", "parent_id": 40},
    }
    products = [
        product("Black Straight Leg Jeans", "women's denim", brand_id="jeans-brand", brand_name="Denim Label", category_id=12, classifier_confidence=.97, audience="women"),
        product("Silver Hoop Earrings", brand_id="jewel-brand", brand_name="Silver Label", category_id=50, classifier_confidence=.98),
        product("Men Oxford Shirt", "men's shirt", brand_id="shirt-brand", brand_name="Menswear", category_id=8, classifier_confidence=.95),
        product("Minimal Analog Watch", brand_id="watch-brand", brand_name="Watch Co", category_id=60, classifier_confidence=.96),
        product("Oak Desk", "home furniture", brand_id="home-brand", brand_name="Home Co", category_id=70, classifier_confidence=.95),
    ]
    brands = [{"id": f"{name}-brand", "name": name} for name in ("jeans", "jewel", "shirt", "watch", "home")]
    monkeypatch.setattr("backend.app.category_map", lambda _: categories)
    monkeypatch.setattr("backend.app.fetch_all", lambda _, table: products if table == "products" else brands)
    app.state.supabase = object()

    jeans = asyncio.run(category_catalog("women-jeans"))
    assert [row["product_name"] for row in jeans["products"]] == ["Black Straight Leg Jeans"]
    assert {row["id"] for row in jeans["brands"]} == {"jeans-brand"}

    jewellery = asyncio.run(category_catalog("accessories-jewellery"))
    assert [row["product_name"] for row in jewellery["products"]] == ["Silver Hoop Earrings"]
    assert {row["id"] for row in jewellery["brands"]} == {"jewel-brand"}


def test_brand_listing_overrides_stale_category_with_validated_mapping(monkeypatch) -> None:
    # Real bug: brands.category is a raw snapshot of whichever product was scraped
    # most recently for that brand (training/scraper_pipeline.py upsert_brand), with
    # no validation at all. Confirmed live: "alpanikindia" was labeled "Women Jewelry"
    # while its actual validated products are kurtis and co-ord sets. /api/brands must
    # show the validated brand_categories primary mapping instead of that raw field.
    from backend.app import list_brands

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_a, **_k):
            return self

        def eq(self, key, value):
            self.rows = [row for row in self.rows if row.get(key) == value]
            return self

        def execute(self):
            return type("Response", (), {"data": self.rows})()

    brands_table = [
        {"id": "b1", "name": "alpanikindia", "category": "Women Jewelry", "instagram_username": "alpanikindia"},
        {"id": "b2", "name": "unclassified_brand", "category": "Women Jewelry", "instagram_username": "unclassified_brand"},
    ]
    primary_mappings = [{"brand_id": "b1", "category_id": 22, "is_primary": True}]  # only b1 has a validated mapping
    categories_table = [{"id": 22, "name": "Kurtis"}]

    class Supabase:
        def table(self, name):
            if name == "brands":
                return Query(brands_table)
            if name == "brand_categories":
                return Query(primary_mappings)
            raise AssertionError(f"unexpected table: {name}")

    monkeypatch.setattr("backend.app.fetch_all", lambda _, table: categories_table if table == "categories" else [])
    app.state.supabase = Supabase()

    result = asyncio.run(list_brands())
    by_id = {row["id"]: row for row in result["brands"]}
    assert by_id["b1"]["category"] == "Kurtis"  # validated mapping wins over the stale snapshot
    assert by_id["b2"]["category"] == "Women Jewelry"  # no validated mapping yet -> falls back, not blanked out


def test_missing_service_role_fails_with_clear_error(monkeypatch) -> None:
    monkeypatch.setattr("backend.category_quality.load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "publishable")
    monkeypatch.setattr(sys, "argv", ["category_quality", "reclassify-catalog"])
    with pytest.raises(SystemExit) as error:
        __import__("backend.category_quality", fromlist=["main"]).main()
    assert SERVICE_ROLE_ERROR in str(error.value)


def test_stable_storage_image_is_not_reuploaded(monkeypatch, capsys) -> None:
    stable = "https://example.supabase.co/storage/v1/object/public/product-images/b/p.jpg"
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda *_args, **_kwargs: [product("Dress", image_url=stable)])
    monkeypatch.setattr("backend.category_quality.upload_stable_image", lambda *_args, **_kwargs: pytest.fail("stable image was reuploaded"))
    run_repair_images(Namespace(limit=None, dry_run=False), object(), "https://example.supabase.co", "service-key")
    assert "ALREADY_STABLE" in capsys.readouterr().out


def test_brand_categories_derive_only_from_confident_products(monkeypatch) -> None:
    captured = []
    class Request:
        payload = []
        def eq(self, *_args): return self
        def execute(self):
            captured.extend(self.payload)
            return type("Response", (), {"data": self.payload})()
    class Table:
        def upsert(self, payload, on_conflict=None):
            request = Request(); request.payload = payload; return request
        def delete(self): return Request()
    class Supabase:
        def table(self, _name): return Table()
    categories = {"accessories-jewellery": {"id": 18, "slug": "accessories-jewellery"}}
    monkeypatch.setattr("backend.category_quality.category_map", lambda *_: categories)
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda *_: [
        product("Silver Hoop Earrings", brand_id="b1", category_id=18, classifier_confidence=.98),
        product("Mystery Edit", brand_id="b2", category_id=18, classifier_confidence=.45),
    ])
    assert rebuild_brand_categories(Supabase()) == 1
    assert captured == [{
        "brand_id": "b1", "category_id": 18, "source": "product_derived", "confidence": .98,
        "is_primary": True,
    }]


def test_brand_categories_mark_the_most_supported_category_primary(monkeypatch) -> None:
    # Real bug fixed here: rebuild_brand_categories previously tried to write
    # supporting_product_count/supporting_product_ids/average_confidence columns
    # that don't exist on the live brand_categories table (only brand_id, category_id,
    # source, confidence, is_primary do) -- every call failed outright. A brand can
    # validly have products in more than one validated category; is_primary marks
    # whichever one actually has the most supporting products, not an arbitrary pick.
    captured = []

    class Request:
        payload = []

        def eq(self, *_args):
            return self

        def execute(self):
            captured.extend(self.payload)
            return type("Response", (), {"data": self.payload})()

    class Table:
        def upsert(self, payload, on_conflict=None):
            request = Request()
            request.payload = payload
            return request

        def delete(self):
            return Request()

    class Supabase:
        def table(self, _name):
            return Table()

    categories = {
        "accessories-jewellery": {"id": 18, "slug": "accessories-jewellery"},
        "women-kurtis": {"id": 22, "slug": "women-kurtis"},
    }
    monkeypatch.setattr("backend.category_quality.category_map", lambda *_: categories)
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda *_: [
        product("Kurti One", brand_id="b1", category_id=22, classifier_confidence=.90, audience="women"),
        product("Kurti Two", brand_id="b1", category_id=22, classifier_confidence=.90, audience="women"),
        product("Necklace One", brand_id="b1", category_id=18, classifier_confidence=.95),
    ])
    assert rebuild_brand_categories(Supabase()) == 2
    by_category = {row["category_id"]: row for row in captured}
    assert by_category[22]["is_primary"] is True  # two supporting products
    assert by_category[18]["is_primary"] is False  # only one, even though higher confidence


def test_category_support_is_strict_about_audience_and_product_family() -> None:
    women_top = product("Women's Black Crop Top", audience="WOMEN", classifier_confidence=.96)
    men_shirt = product("Men's Oxford Shirt", audience="MEN", classifier_confidence=.97)
    earrings = product("Minimal Silver Hoop Earrings", audience="UNISEX", classifier_confidence=.98)
    assert product_supports_category(women_top, "women-tops", {})
    assert not product_supports_category(women_top, "men-shirts", {})
    assert product_supports_category(men_shirt, "men-shirts", {})
    assert not product_supports_category(men_shirt, "women-tops", {})
    assert product_supports_category(earrings, "accessories-jewellery", {})
    assert not product_supports_category(earrings, "women-tops", {})


def test_category_support_rejects_blank_audience_for_a_gendered_category() -> None:
    # Real bug: `if required_audience and audience and ...` silently skipped the
    # gender check whenever audience was blank (common for scraped rows), so a
    # men-shirts page could show a product with no gender evidence at all. A blank
    # audience must now be a rejection for a gendered category, not a free pass.
    blank_audience_shirt = product("Classic Cotton Shirt", audience=None, classifier_confidence=.95)
    assert not product_supports_category(blank_audience_shirt, "men-shirts", {})


def test_category_support_uses_category_audience_when_the_products_own_audience_is_blank() -> None:
    # Real-world case, not a contrived one: 96% of products have a null `audience`
    # column, but plenty of them sit in an unambiguously-gendered category (their
    # category_id maps to a categories row whose own `audience` IS set). Before
    # attach_category_audience() ran on the fetch path, this product had zero
    # gender signal (gender_match_score() doesn't read product_name/description,
    # only audience/category_audience/category/normalized_*) and was wrongly
    # excluded from its own category's page.
    from backend.product_taxonomy import attach_category_audience

    # "mens" in the description gives classify_product_category()'s own,
    # separate text-based audience check enough to confidently resolve
    # men-shirts -- isolating this test to the category_audience/gender_match_score
    # gap specifically, not conflating it with that unrelated internal gate.
    shirt = product("Classic Cotton Shirt", description="mens formal wear shirt", audience=None, classifier_confidence=.95, category_id=8)
    categories = {"men-shirts": {"id": 8, "slug": "men-shirts", "audience": "MEN"}}
    assert not product_supports_category(shirt, "men-shirts", categories)

    enriched_shirt = attach_category_audience([shirt], categories.values())[0]
    assert product_supports_category(enriched_shirt, "men-shirts", categories)
    assert not product_supports_category(enriched_shirt, "women-tops", categories)


def test_main_category_uses_only_validated_descendant_products() -> None:
    categories = {
        "women": {"id": 1, "slug": "women", "parent_id": None},
        "women-tops": {"id": 2, "slug": "women-tops", "parent_id": 1},
    }
    top = product("Women's Black Crop Top", audience="WOMEN", classifier_confidence=.96)
    shirt = product("Men's Oxford Shirt", audience="MEN", classifier_confidence=.96)
    assert product_supports_category(top, "women", categories)
    assert not product_supports_category(shirt, "women", categories)


def test_brand_api_has_one_normalized_image_contract() -> None:
    from backend.app import format_brand
    url = "https://example.supabase.co/storage/v1/object/public/brand-images/brand.jpg"
    result = format_brand({"id": "b1", "name": "Studio", "profile_picture_url": url})
    assert result["image_url"] == url
    assert result["profile_picture_url"] == url


def test_expired_image_is_marked_recoverable_without_dry_run_writes(monkeypatch, capsys) -> None:
    rows = [product(
        "Earrings", source="instagram", image_url="https://cdninstagram.example/expired.jpg",
        instagram_post_url="https://www.instagram.com/p/abc123/",
    )]
    class Supabase:
        def table(self, _name): return pytest.fail("dry-run attempted a database write")
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr("backend.category_quality.image_url_status", lambda *_args, **_kwargs: "EXPIRED_OR_FORBIDDEN")
    run_repair_images(Namespace(limit=None, dry_run=True), Supabase(), "https://example.supabase.co", "anon")
    output = capsys.readouterr().out
    assert "EXPIRED_OR_FORBIDDEN" in output and "RECOVERABLE" in output


def test_wrong_seed_image_is_flagged_for_recovery(monkeypatch, capsys) -> None:
    rows = [product(
        "Earrings", source="instagram", image_url="https://images.unsplash.com/wrong.jpg",
        product_url="https://www.instagram.com/p/abc123/",
    )]
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr("backend.category_quality.image_url_status", lambda *_args, **_kwargs: "VALID")
    run_repair_images(Namespace(limit=None, dry_run=True), object(), "https://example.supabase.co", "anon")
    assert "[INVALID]" in capsys.readouterr().out


def test_recovery_dry_run_never_scrapes_uploads_or_writes(monkeypatch, capsys) -> None:
    rows = [product(
        "Earrings", source="instagram", image_url="https://cdninstagram.example/expired.jpg",
        image_status="RECOVERY_REQUIRED", instagram_post_url="https://www.instagram.com/p/abc123/",
    )]
    monkeypatch.setattr("backend.category_quality.fetch_all", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr("backend.category_quality.upload_stable_image", lambda *_args, **_kwargs: pytest.fail("dry-run uploaded"))
    run_recover_images(
        Namespace(limit=None, dry_run=True, only_broken=True), object(),
        "https://example.supabase.co", "anon", lambda _product: pytest.fail("dry-run scraped"),
    )
    assert "[RECOVERABLE]" in capsys.readouterr().out


def test_rls_and_storage_migration_is_read_only_for_public_users() -> None:
    sql = (Path(__file__).parents[1] / "Niche_brand/supabase/category_image_quality_migration.sql").read_text()
    assert 'for select to anon, authenticated' in sql.lower()
    assert "for insert to anon" not in sql.lower()
    assert "for update to anon" not in sql.lower()
    assert "for delete to anon" not in sql.lower()
    assert "product-images" in sql and "brand-images" in sql
    storage = (Path(__file__).parents[1] / "Niche_brand/supabase/storage_setup.sql").read_text().lower()
    assert "on conflict (id) do update" in storage
    assert "for select" in storage and "for insert" not in storage and "for update" not in storage and "for delete" not in storage


def test_category_migrations_seed_by_slug_without_generated_ids() -> None:
    root = Path(__file__).parents[1] / "Niche_brand/supabase"
    catalog = (root / "catalog_vector_schema.sql").read_text()
    quality = (root / "category_image_quality_migration.sql").read_text()
    experience = (root / "unfound_experience_schema.sql").read_text()
    assert "insert into public.categories (id" not in catalog.lower()
    assert "insert into public.categories (id" not in quality.lower()
    assert "on conflict (id)" not in catalog.lower()
    assert catalog.lower().count("on conflict (slug)") >= 3
    assert "('uncategorized')" not in catalog.lower()  # seed includes name, never a magic ID tuple
    assert "values (32" not in quality.lower()
    assert "on conflict (slug)" in quality.lower()
    assert experience.lower().count("on conflict (slug)") >= 2
    assert "pg_get_serial_sequence('public.categories', 'id')" in catalog
    assert "if maximum_id > sequence_value" in catalog
    assert "create table if not exists public.interactions" in catalog.lower()
    assert "drop function if exists public.match_products(vector, double precision, integer, bigint)" in catalog.lower()


def test_production_code_has_no_uncategorized_magic_id() -> None:
    root = Path(__file__).parents[1]
    scraper = (root / "training/scraper_pipeline.py").read_text()
    app_source = (root / "backend/app.py").read_text()
    frontend = (root / "Niche_brand/src/App.jsx").read_text()
    compact_frontend = "".join(frontend.split())
    assert '"uncategorized": {"id": 32' not in scraper
    assert "CATEGORY_NAMES_BY_ID" not in app_source
    # The category filter is sent to /api/discover by slug, built from a
    # data-driven field table (SEARCH_FILTER_FIELDS) rather than a one-off
    # literal append -- still never a raw numeric category_id.
    assert 'formKey:"category_slug"' in compact_frontend
    assert 'formKey:"category_id"' not in compact_frontend
    assert 'body.append("category_id",category)' not in compact_frontend


def _photo_bytes(size: tuple[int, int] = (200, 200)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, color=(30, 60, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_upload_stable_brand_image_rejects_instagrams_default_avatar(monkeypatch) -> None:
    # Stage 4: Instagram serves a shared default silhouette to any account with no
    # custom avatar. Confirmed by comparing scraped bytes across several brands --
    # distinct CDN URLs, byte-identical content. Must be rejected, not "recovered".
    import hashlib

    default_bytes = _photo_bytes((150, 150))
    monkeypatch.setattr(
        "backend.category_quality.INSTAGRAM_DEFAULT_AVATAR_SHA256",
        hashlib.sha256(default_bytes).hexdigest(),
    )

    class FakeResponse:
        content = default_bytes
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("httpx.get", lambda *_a, **_k: FakeResponse())
    with pytest.raises(ValueError, match="no custom"):
        upload_stable_brand_image(
            "https://cdn.example.com/default.jpg",
            brand_id="brand-1",
            base_url="https://x.supabase.co",
            key="service-key",
        )


def test_upload_stable_brand_image_accepts_a_real_distinct_photo(monkeypatch) -> None:
    photo_bytes = _photo_bytes((200, 200))

    class FakeGetResponse:
        content = photo_bytes
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self) -> None:
            return None

    class FakePostResponse:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("httpx.get", lambda *_a, **_k: FakeGetResponse())
    monkeypatch.setattr("httpx.post", lambda *_a, **_k: FakePostResponse())
    url = upload_stable_brand_image(
        "https://cdn.example.com/real.jpg",
        brand_id="brand-1",
        base_url="https://x.supabase.co",
        key="service-key",
    )
    assert url == "https://x.supabase.co/storage/v1/object/public/brand-images/brand-1/profile.jpg"


def test_upload_stable_brand_image_rejects_below_realistic_avatar_size(monkeypatch) -> None:
    # Instagram's public web view only ever exposes a fixed ~100-150px avatar via
    # plain DOM scraping -- 96px is the floor that still guards against tiny
    # default/broken icons while admitting genuine small avatars.
    tiny_bytes = _photo_bytes((50, 50))

    class FakeResponse:
        content = tiny_bytes
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("httpx.get", lambda *_a, **_k: FakeResponse())
    with pytest.raises(ValueError, match="too small"):
        upload_stable_brand_image(
            "https://cdn.example.com/tiny.jpg",
            brand_id="brand-1",
            base_url="https://x.supabase.co",
            key="service-key",
        )
