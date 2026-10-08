from backend.product_titles import constructed_title, looks_like_caption_or_alt_text


def test_flags_instagram_alt_text_leakage():
    # The exact bug report: Instagram's auto-generated alt text, identified as
    # unreliable in the classification audit, stored verbatim as a product name.
    assert looks_like_caption_or_alt_text(
        "Photo by ARTEES™ on June 09, 2026. May be an image of baseball, "
        "basketball jersey, sportswear, American football helmet"
    )


def test_flags_photo_by_caption_prefix():
    assert looks_like_caption_or_alt_text("Photo by BEFOREJULY on September 27, 2025.")
    assert looks_like_caption_or_alt_text("Photo shared by Frankie. on June 15, 2026 tagging @charlotte.")


def test_flags_question_phrased_captions():
    assert looks_like_caption_or_alt_text("What's one summer wardrobe staple you have")
    assert looks_like_caption_or_alt_text("Do you own one of these yet?")
    assert looks_like_caption_or_alt_text("Which colourway is your favourite?")


def test_flags_emoji_only_and_hashtag_soup_names():
    assert looks_like_caption_or_alt_text("\U0001F338\U0001FA77\U0001F90D")  # emoji only
    assert looks_like_caption_or_alt_text("\U0001F338\U0001FA77\U0001F90D CottonComfort IndianWear SummerStyle")


def test_flags_names_over_the_word_limit():
    long_caption = "This latest drop was all about joyful details easy colour and pieces that feel good"
    assert len(long_caption.split()) > 12
    assert looks_like_caption_or_alt_text(long_caption)


def test_does_not_flag_a_genuinely_good_product_name():
    # The explicit requirement: a real product name must pass through untouched.
    good_names = [
        "Women's Ribbed Crop Top",
        "Embroidered Kurti",
        "ARTEES Black Jersey",
        "Oversized Denim Jacket",
        "Gold Pendant Necklace",
        "Classic Cotton Shirt",
    ]
    for name in good_names:
        assert not looks_like_caption_or_alt_text(name), name


def test_does_not_flag_empty_or_missing_name():
    assert not looks_like_caption_or_alt_text(None)
    assert not looks_like_caption_or_alt_text("")
    assert not looks_like_caption_or_alt_text("   ")


def test_constructed_title_builds_brand_colour_type():
    row = {
        "brand_name": "artees.corner",
        "description": "Photo by ARTEES on June 09. May be an image of black jersey.",
        "product_name": "Photo by ARTEES on June 09.",
    }
    assert constructed_title(row) == "Artees Corner Black"


def test_constructed_title_leaves_an_already_good_brand_name_untouched():
    # "BYUTIFY" is a real, intentionally all-caps display name, not a raw handle --
    # must not get re-title-cased into "Byutify".
    row = {"brand_name": "BYUTIFY", "description": "black party top for a night out"}
    assert constructed_title(row) == "BYUTIFY Black Party Top"


def test_constructed_title_strips_trailing_domain_style_suffix():
    row = {"brand_name": "bowberry.in", "description": "elegant black kurti for festive occasions"}
    title = constructed_title(row)
    assert title is not None
    assert "In" not in title.split()
    assert title.startswith("Bowberry")


def test_constructed_title_returns_none_when_not_enough_signal():
    # Brand alone, no colour or identifiable product type anywhere in the text --
    # nothing trustworthy enough to build a name from; caller must flag for review
    # rather than invent one.
    row = {"brand_name": "somebrand", "description": "a truly wonderful find you will love"}
    assert constructed_title(row) is None


def test_constructed_title_never_uses_the_bad_name_itself_as_a_source():
    # The caption text (which triggered the rewrite) may itself contain a colour or
    # product word (Instagram's alt text often does) -- constructed_title is allowed
    # to use it since it's the best signal available, but must never simply echo the
    # original bad name back as-is.
    row = {
        "brand_name": "reeia",
        "product_name": "Photo by Reeia on March 24, 2026. May be an image of a red dress.",
        "description": "Photo by Reeia on March 24, 2026. May be an image of a red dress.",
    }
    title = constructed_title(row)
    assert title == "Reeia Red Dress"
    assert title != row["product_name"]
