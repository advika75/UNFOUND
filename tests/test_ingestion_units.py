from training.scraper_pipeline import (
    ProductCandidate,
    ScrapedInstagramPost,
    dedupe_posts,
    extract_price,
    fallback_category,
    is_likely_product_post,
    clean_product_name,
    curated_brand,
    extract_fashion_metadata,
    load_curated_brand_list,
    parse_instagram_identity,
    product_payload,
    product_quality_gate,
    upsert_brand,
    validate_embedding,
)
import pytest


def _candidate(**overrides):
    defaults = dict(
        product_name="Item", brand_name="ExampleBrand", category="Women Ethnic Wear",
        category_id=21, image_url="https://cdn.example.com/a.jpg", product_url="https://instagram.com/p/1",
        description="", price=None, niche_score=0.85, classifier_confidence=0.85,
        instagram_username="examplebrand",
    )
    defaults.update(overrides)
    return ProductCandidate(**defaults)


class _FakeBrandsTable:
    def __init__(self):
        self.rows_by_username: dict[str, dict] = {}
        self.upsert_calls: list[dict] = []

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self._filter = (column, value)
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        column, value = getattr(self, "_filter", (None, None))
        rows = [row for row in self.rows_by_username.values() if row.get(column) == value]
        return type("Response", (), {"data": rows})()

    def upsert(self, payload, on_conflict=None):
        self.upsert_calls.append(dict(payload))
        row = {**self.rows_by_username.get(payload["instagram_username"], {"id": "brand-1"}), **payload}
        self.rows_by_username[payload["instagram_username"]] = row
        return type("Request", (), {"execute": lambda self=None: type("Response", (), {"data": [row]})()})()


class _FakeSupabase:
    def __init__(self):
        self.brands = _FakeBrandsTable()

    def table(self, name):
        assert name == "brands"
        return self.brands


def test_upsert_brand_sets_category_once_then_leaves_it_to_brand_categories():
    # Real bug fixed in Stage 9: category is overwritten from whichever product is
    # scraped most recently, producing brand-level mislabeling (e.g. a kurti/co-ord
    # seller displayed as "Women Jewelry" just because that was the last post
    # ingested). It should only ever be set at brand creation; brand_categories
    # (rebuild_brand_categories) is the authoritative multi-category source after that.
    supabase = _FakeSupabase()
    upsert_brand(supabase, _candidate(category="Women Ethnic Wear"))
    upsert_brand(supabase, _candidate(category="Women Jewelry"))
    assert supabase.brands.upsert_calls[0]["category"] == "Women Ethnic Wear"
    assert "category" not in supabase.brands.upsert_calls[1]


def post(description: str, *, url: str = "https://www.instagram.com/p/abc/") -> ScrapedInstagramPost:
    return ScrapedInstagramPost(
        product_url=url,
        image_url="https://images.example.com/product.jpg",
        description=description,
        brand_name="Example Label",
    )


def test_extract_price_supports_common_indian_formats() -> None:
    assert extract_price("Launch price ₹2,499") == 2499
    assert extract_price("Rs. 2499") == 2499
    assert extract_price("INR 2499") == 2499
    assert extract_price("₹ 1,999/-") == 1999
    assert extract_price("Only 2.5k this week") == 2500
    assert extract_price("Rs 3,000 onwards") == 3000
    assert extract_price("DM for price") is None


def test_category_normalization_maps_synonyms() -> None:
    assert fallback_category("silver hoop earrings")["category_slug"] == "women-jewelry"
    assert fallback_category("leather sandals")["category_slug"] == "women-shoes"
    assert fallback_category("embroidered cotton kurta")["category_slug"] == "women-ethnic-wear"
    assert fallback_category("men's oversized jacket")["category_slug"] == "men-jackets"
    assert fallback_category("handmade hair clips")["category_slug"] == "accessories-hair"


def test_post_detection_skips_irrelevant_content_deterministically() -> None:
    assert is_likely_product_post(post("New black linen dress. Shop now ₹2499"))[0] is True
    accepted, reason = is_likely_product_post(post("Monday moodboard and quote of the day"))
    assert accepted is False
    assert reason


def test_deduplication_prefers_post_id_then_brand_image() -> None:
    unique = dedupe_posts(
        [
            post("Black dress", url="https://www.instagram.com/p/ABC/?utm_source=x"),
            post("Black dress duplicate", url="https://www.instagram.com/p/ABC/"),
            post("Same image repost", url="https://www.instagram.com/p/DIFFERENT/"),
        ]
    )
    assert len(unique) == 1


def test_curated_instagram_identity_is_exact_and_deduplicated(tmp_path) -> None:
    assert parse_instagram_identity("https://www.instagram.com/Exact.Label/") == ("exact.label", "https://www.instagram.com/exact.label/")
    assert parse_instagram_identity("@exact_label")[0] == "exact_label"
    with pytest.raises(ValueError): parse_instagram_identity("https://www.instagram.com/p/POST/")
    path = tmp_path / "curated.csv"
    path.write_text("brand_name,instagram_url,category_hint,priority\nFirst,@same,tops,high\nDuplicate,@same,ethnic,low\nMissing,,tops,high\n")
    rows = load_curated_brand_list(path)
    assert len(rows) == 1 and rows[0].instagram_username == "same"


def test_curated_brand_preserves_hint_without_forcing_product_category() -> None:
    brand = curated_brand("@label", brand_name="Label", category_hint="stylish tops", priority="high")
    assert brand.source_hashtag == "curated:stylish tops:high"
    metadata = extract_fashion_metadata(post("Our bestselling black corset top is finally back. Party edit. #GoingOut"))
    assert metadata["product_type"] == "corset-top" and metadata["primary_colour"] == "black"
    assert "party" in metadata["style"]


def test_product_name_prefers_specific_fashion_identity():
    assert clean_product_name("Our bestselling black corset top is finally back.", "women-tops") == "Black Corset Top"


def test_product_payload_fills_legacy_required_name_and_brand_columns():
    # Real bug caught live in Stage 8: the products table has legacy NOT NULL
    # columns "name" and "brand" kept in parallel with product_name/brand_name
    # (same dual-column pattern upsert_brand() already handles for the brands
    # table), but product_payload never populated them -- every single live
    # insert via add-brand failed with a not-null constraint violation.
    candidate = ProductCandidate(
        product_name="Cotton Corset Top", brand_name="BYUTIFY", category="Women Ethnic Wear",
        category_id=21, image_url="https://cdn.example.com/a.jpg", product_url="https://instagram.com/p/1",
        description="Cotton corset top", price=None, niche_score=0.85, classifier_confidence=0.85,
    )
    payload = product_payload(candidate, [0.0] * 512, "brand-id-1")
    assert payload["name"] == "Cotton Corset Top"
    assert payload["brand"] == "BYUTIFY"


def test_product_payload_substitutes_zero_for_missing_price_not_null():
    # price is NOT NULL on the live table; every existing row uses 0.0 (never a
    # real null) for "no price found in caption" -- confirmed by querying the
    # table directly. product.price is legitimately None whenever no price
    # pattern matched, so the payload must translate that to the DB's convention.
    candidate = ProductCandidate(
        product_name="Kurtis", brand_name="BYUTIFY", category="Women Ethnic Wear",
        category_id=21, image_url="https://cdn.example.com/a.jpg", product_url="https://instagram.com/p/2",
        description="No price mentioned here", price=None, niche_score=0.85, classifier_confidence=0.85,
    )
    payload = product_payload(candidate, [0.0] * 512, "brand-id-1")
    assert payload["price"] == 0.0


def test_product_name_rejects_instagram_engagement_count_leakage():
    # Real bug caught live in Stage 8: a post with no real caption falls back to
    # Instagram's engagement-count alt text ("4 likes, 0 comments - byutify"), which
    # otherwise passes clean_product_name's length/marketing-word checks untouched
    # and gets stored verbatim as the product name -- the same class of defect
    # looks_like_unparsed_caption already guards against for other leakage patterns.
    for description in (
        "4 likes, 0 comments - byutify",
        "1,056 likes, 522 comments - swati",
        "15 likes, 0 comments - byutify",
    ):
        assert clean_product_name(description, "accessories-jewellery") is None


def test_product_quality_gate_holds_generic_or_duplicate_products():
    from types import SimpleNamespace
    candidate = SimpleNamespace(brand_name="Label", instagram_username="label", product_name="Product", category_id=1, classifier_confidence=.9, normalized_subcategory="women-tops")
    accepted, reasons = product_quality_gate(candidate, post("Black crop top shop now"), duplicate=True)
    assert accepted is False and {"generic_product_name", "duplicate"} <= set(reasons)


def test_clip_embedding_validation_checks_dimension_finite_values_and_norm():
    vector = [1 / (512 ** .5)] * 512
    assert validate_embedding(vector)["valid"] is True
    with pytest.raises(ValueError): validate_embedding([1.0] * 511)
    with pytest.raises(ValueError): validate_embedding([1.0] * 512)
