"""Detects Instagram caption/alt-text leakage in stored product names, and derives a
presentable fallback from trusted structured fields when one is detected.

Read-only detection and construction live here. Writing the result back to the
database is a separate, explicit step (backend/reclassify_product_titles.py), never
automatic -- this module never touches Supabase.

looks_like_unparsed_caption is reused directly from training/scraper_pipeline.py
(the ingestion-time cleaner) rather than reimplemented: it already catches the
"Photo by X on <date>. May be an image of ..." pattern and Instagram's
engagement-count alt text. This module adds the detectors that pattern doesn't
cover -- question-phrased captions, emoji/hashtag-soup names, and simply-too-long
names -- and adds the audit-specific "{Brand} {Colour} {Type}" construction.
"""

from __future__ import annotations

import re
from typing import Any

from backend.product_taxonomy import TYPE_LABELS, identify_product
from training.scraper_pipeline import looks_like_unparsed_caption

# Same list backend/app.py uses to parse colour out of a search query -- one
# canonical source for "what counts as a colour word" across search and ingestion.
COLOURS = ("black", "white", "silver", "gold", "brown", "beige", "blue", "navy", "red", "green", "pink", "purple", "grey", "gray", "orange", "yellow")

MAX_PRESENTABLE_WORDS = 12

_QUESTION_STARTS = re.compile(
    r"^(what|how|why|which|who|when|where|do you|did you|have you|are you|is your|can you|would you)\b",
    re.IGNORECASE,
)
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flag emoji)
    "]+"
)


def _is_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?") or bool(_QUESTION_STARTS.match(stripped))


def _is_emoji_or_hashtag_soup(text: str) -> bool:
    """Emoji-only, or a run of glued-together CamelCase/hashtag-style words with no
    real sentence structure (e.g. "CottonComfort IndianWear SummerStyle")."""
    without_emoji = _EMOJI.sub("", text).strip()
    if not without_emoji:
        return True
    words = without_emoji.split()
    if not words:
        return True
    glued = sum(1 for word in words if re.search(r"[a-z][A-Z]", word))
    return glued / len(words) >= 0.5


def looks_like_caption_or_alt_text(text: str | None) -> bool:
    """True if `text` reads like a scraped caption or Instagram's auto-generated alt
    text leaked into a product-name field, rather than an authored product name."""
    candidate = (text or "").strip()
    if not candidate:
        return False
    if looks_like_unparsed_caption(candidate):
        return True
    if _is_question(candidate):
        return True
    if _is_emoji_or_hashtag_soup(candidate):
        return True
    if len(candidate.split()) > MAX_PRESENTABLE_WORDS:
        return True
    return False


def _presentable_brand(brand_name: str | None) -> str:
    """brand_name is a mix of two shapes: some are already a real display name
    ("Fashion Capital", "BYUTIFY" -- left untouched), others are a raw, all-lowercase
    Instagram handle ("bellamar.label", "_didije_", "bowberry.in"). For the handle
    shape only: split on the separators and title-case each piece, and drop a
    trailing short alphabetic segment that's a domain-style artifact (the ".in" in
    "bowberry.in"), not part of the brand's actual name."""
    name = (brand_name or "").strip()
    if not name or name != name.lower():
        return name
    pieces = [piece for piece in re.split(r"[._]+", name) if piece]
    if len(pieces) > 1 and len(pieces[-1]) <= 3 and pieces[-1].isalpha():
        pieces = pieces[:-1]
    return " ".join(piece.capitalize() for piece in pieces) if pieces else name.title()


MAX_CAPTION_PREVIEW_CHARS = 100


def display_name_for(product: dict[str, Any]) -> dict[str, Any]:
    """Display-layer-only fallback -- never writes back to product_name itself (see
    module docstring: that's a separate, explicit, not-yet-built step). Returns what
    the API/frontend should show in place of a caption-like stored name: a real,
    presentable title built from brand and category (the two fields virtually every
    product has, unlike colour/type -- see constructed_title(), which only has
    enough evidence to build a title for ~10% of flagged products), plus the
    original caption kept visible as secondary text rather than silently discarded.
    A good, already-presentable name passes through untouched.
    """
    name = str(product.get("product_name") or "")
    if not looks_like_caption_or_alt_text(name):
        return {"display_name": name, "name_is_caption_like": False, "caption_preview": None}
    brand = _presentable_brand(product.get("brand_name"))
    category = str(product.get("category") or "").strip()
    display_name = f"{brand} · {category}" if brand and category else brand or category or "Untitled piece"
    preview = name if len(name) <= MAX_CAPTION_PREVIEW_CHARS else name[:MAX_CAPTION_PREVIEW_CHARS].rstrip() + "…"
    return {"display_name": display_name, "name_is_caption_like": True, "caption_preview": preview}


def constructed_title(row: dict[str, Any]) -> str | None:
    """"{Brand} {Colour} {Product Type}" built from the row's brand and its own
    description/name text -- never from the bad name being replaced. Returns None
    when fewer than two of the three parts are found (nothing trustworthy enough
    to build a presentable name from; caller should flag for manual review rather
    than invent one)."""
    text = " ".join(str(row.get(key) or "") for key in ("description", "product_name", "item_name")).lower()
    brand = _presentable_brand(row.get("brand_name"))
    # brand already correctly cased above, and TYPE_LABELS values are already Title
    # Case -- only the raw lowercase colour word needs casing here.
    colour = next((word for word in COLOURS if re.search(rf"\b{word}\b", text)), "").title()
    _, product_type = identify_product(text)
    type_label = TYPE_LABELS.get(product_type, "") if product_type else ""
    parts = [part for part in (brand, colour, type_label) if part]
    deduped = list(dict.fromkeys(parts))
    return " ".join(deduped) if len(deduped) >= 2 else None
