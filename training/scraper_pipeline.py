from __future__ import annotations

import argparse
import asyncio
import csv
import inspect
import json
import logging
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable

from dotenv import load_dotenv

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("TRANSFORMERS_NO_FLAX", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

from backend.supabase_compat import create_supabase_client
from backend.product_taxonomy import TYPE_LABELS, identify_product
from backend.ingestion_batches import append_audit, ingestion_quality_score, load_batch, stale_brand_rows


def safe_load_env(path: str | None = None, *, override: bool = False) -> bool:
    """Avoid blocking on an iCloud placeholder; never reads or prints secrets."""
    if path:
        candidate = Path(path)
        try:
            stat = candidate.stat()
            if stat.st_size and stat.st_blocks == 0:
                return False
        except OSError:
            return False
    return bool(load_dotenv(path, override=override) if path else load_dotenv(override=override))


safe_load_env()
safe_load_env("backend/.env", override=True)
safe_load_env("scraper/.env", override=True)

LOGGER = logging.getLogger("product_ingestion")
AUTO_OPENAI = object()

MODEL_NAME = os.getenv("CLIP_MODEL_NAME", "clip-ViT-B-32")
BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "20"))
DEFAULT_HASHTAGS = ["SlowFashionBrand", "IndieLabel", "ArtisanJewelry"]
DEFAULT_SOURCE = "instagram"
BRAND_CACHE_PATH = Path(os.getenv("BRAND_CACHE_PATH", "scraper/data/brands.csv"))
DEFAULT_SCROLL_LIMIT = int(os.getenv("INGEST_SCROLL_LIMIT", os.getenv("SCROLL_ATTEMPTS", "8")))
SCROLL_STALL_LIMIT = int(os.getenv("INGEST_SCROLL_STALL_LIMIT", "3"))
SELENIUM_PROFILE_DIR = Path(os.getenv("SELENIUM_PROFILE_DIR", "scraper/chrome_profile"))
SELENIUM_HEADLESS = os.getenv("INGEST_HEADLESS", "false").lower() == "true"
MANUAL_LOGIN_TIMEOUT_SECONDS = int(os.getenv("INSTAGRAM_LOGIN_TIMEOUT_SECONDS", "300"))
POST_SELECTOR_CANDIDATES = [
    "a[href*='/p/']",
    "a[href*='/reel/']",
    "article a[href]",
    "main a[href]",
]
BRAND_CACHE_FIELDNAMES = [
    "brand_name",
    "instagram_username",
    "instagram_profile_url",
    "profile_url",
    "profile_picture_url",
    "followers",
    "niche_score",
    "source_hashtag",
    "scraped_at",
]

CATEGORY_BY_SLUG: dict[str, dict[str, str]] = {
    "men-jeans": {"name": "Men Jeans"},
    "men-shirts": {"name": "Men Shirts"},
    "men-jackets": {"name": "Men Jackets"},
    "women-jeans": {"name": "Women Jeans"},
    "women-dresses": {"name": "Women Dresses"},
    "women-tops": {"name": "Women Tops"},
    "women-jackets": {"name": "Women Jackets"},
    "women-bags": {"name": "Women Bags"},
    "women-shoes": {"name": "Women Shoes"},
    "women-jewelry": {"name": "Women Jewelry"},
    "home-decor": {"name": "Home Decor"},
    "women-bottoms": {"name": "Women Bottoms"},
    "women-ethnic-wear": {"name": "Women Ethnic Wear"},
    "women-co-ords": {"name": "Women Co-ords"},
    "women-activewear": {"name": "Women Activewear"},
    "men-t-shirts": {"name": "Men T-shirts"},
    "men-bottoms": {"name": "Men Bottoms"},
    "men-ethnic-wear": {"name": "Men Ethnic Wear"},
    "accessories-sunglasses": {"name": "Sunglasses"},
    "accessories-watches": {"name": "Watches"},
    "accessories-belts": {"name": "Belts"},
    "accessories-hair": {"name": "Hair Accessories"},
    "accessories-other": {"name": "Other Accessories"},
    "uncategorized": {"name": "Uncategorized"},
}

SYSTEM_PROMPT = """
You are a master fashion taxonomist. Analyze this scraped Instagram commerce item
and map it to exactly one category slug from this allowed list:
men-jeans, men-shirts, men-t-shirts, men-bottoms, men-jackets,
men-ethnic-wear, women-jeans, women-dresses, women-tops, women-bottoms,
women-jackets, women-ethnic-wear, women-co-ords, women-activewear,
women-bags, women-shoes, women-jewelry, accessories-sunglasses,
accessories-watches, accessories-belts, accessories-hair,
accessories-other, home-decor.
Use uncategorized only when the product type or audience cannot be determined.

Return strict JSON with this schema:
{"category_slug": string, "confidence_score": number}
""".strip()


@dataclass(frozen=True)
class BrandMetadata:
    brand_name: str
    instagram_username: str
    instagram_profile_url: str
    profile_picture_url: str
    followers: int
    niche_score: float
    source_hashtag: str
    scraped_at: str
    bio: str = ""
    batch_id: str = ""
    candidate_source: str = ""

    @property
    def profile_url(self) -> str:
        return self.instagram_profile_url


@dataclass(frozen=True)
class ScrapedInstagramPost:
    product_url: str
    image_url: str
    description: str
    brand_name: str
    source_hashtag: str = ""
    followers: int = 0
    instagram_username: str = ""
    instagram_profile_url: str = ""
    profile_picture_url: str = ""
    brand_niche_score: float = 0.0
    likes_count: int = 0
    comments_count: int = 0
    scraped_at: str = ""
    source: str = DEFAULT_SOURCE
    instagram_post_id: str = ""
    extra_image_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProductCandidate:
    product_name: str
    brand_name: str
    category: str
    category_id: int
    image_url: str
    product_url: str
    description: str
    price: float | None
    niche_score: float
    classifier_confidence: float
    instagram_username: str = ""
    instagram_profile_url: str = ""
    profile_picture_url: str = ""
    followers: int = 0
    likes_count: int = 0
    comments_count: int = 0
    source_hashtag: str = ""
    scraped_at: str = ""
    source: str = DEFAULT_SOURCE
    currency: str = "INR"
    subcategory: str = ""
    instagram_post_id: str = ""
    instagram_post_url: str = ""
    extra_image_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None
    original_image_url: str = ""
    image_status: str = "UNKNOWN"
    normalized_main_category: str = ""
    normalized_subcategory: str = ""
    audience: str = ""
    classification_source: str = "heuristic"


@dataclass
class IngestionSummary:
    brands_attempted: int = 0
    brands_successful: int = 0
    brands_failed: int = 0
    posts_scanned: int = 0
    product_posts_detected: int = 0
    products_inserted: int = 0
    products_updated: int = 0
    skipped: int = 0
    duplicates_skipped: int = 0
    embedding_failures: int = 0
    embedding_successes: int = 0
    openai_calls: int = 0
    heuristic_classifications: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    brands_inserted: int = 0
    brands_updated: int = 0
    quality_rejections: int = 0
    image_candidates: int = 0
    images_uploaded: int = 0
    images_failed: int = 0
    images_already_stable: int = 0
    embeddings_reused: int = 0


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("INGEST_LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def retry(
    operation: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 1.5,
    label: str = "operation",
) -> Any:
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = base_delay_seconds * (2 ** (attempt - 1)) + random.uniform(0.1, 0.8)
            LOGGER.warning(
                "%s failed on attempt %s/%s; retrying in %.2fs: %s",
                label,
                attempt,
                attempts,
                delay,
                error,
            )
            time.sleep(delay)

    raise RuntimeError(f"{label} failed after {attempts} attempt(s): {last_error}") from last_error


def random_delay(min_seconds: float = 0.8, max_seconds: float = 2.4) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def get_supabase_client() -> Any:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is required for ingestion. "
            "A publishable key cannot perform server-side catalog writes."
        )
    return create_supabase_client(url, key)


def get_public_supabase_client() -> Any:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY are required for dry-run reads.")
    return create_supabase_client(url, key)


def verify_environment(*, require_openai: bool = False, require_service_role: bool = True) -> None:
    required_keys = ["SUPABASE_URL"]
    if require_service_role:
        required_keys.append("SUPABASE_SERVICE_ROLE_KEY")
    if require_openai:
        required_keys.append("OPENAI_API_KEY")
    missing_keys = [key for key in required_keys if not os.getenv(key)]
    if not require_service_role and not (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")):
        missing_keys.append("SUPABASE_ANON_KEY")
    if missing_keys:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing_keys)}")
    LOGGER.info(
        "Environment loaded",
        extra={
            "supabase_url_present": bool(os.getenv("SUPABASE_URL")),
            "supabase_service_role_present": bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY")),
            "openai_key_present": bool(os.getenv("OPENAI_API_KEY")),
        },
    )


def verify_supabase_connection(supabase: Any) -> None:
    def ping() -> Any:
        return supabase.table("brands").select("id").limit(1).execute()

    retry(ping, attempts=3, label="verify Supabase connection")
    LOGGER.info("Supabase connection verified")


def get_openai_client() -> Any | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    from openai import OpenAI

    return OpenAI(api_key=api_key)


def verify_openai_connection(openai_client: Any | None) -> None:
    if openai_client is None:
        LOGGER.info("OPENAI_API_KEY not configured; using rule-based fallback classifier")
        return

    def ping() -> Any:
        return openai_client.models.list()

    retry(ping, attempts=3, label="verify OpenAI connection")
    LOGGER.info("OpenAI connection verified")


def normalize_brand_name(raw_name: str | None) -> str:
    cleaned = (raw_name or "").strip().lstrip("@")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "unknown_brand"


def normalize_instagram_username(raw_name: str | None) -> str:
    cleaned = normalize_brand_name(raw_name).lower()
    cleaned = re.sub(r"[^a-z0-9._]", "", cleaned)
    return cleaned or "unknown_brand"


def parse_instagram_identity(value: str) -> tuple[str, str]:
    """Validate an exact handle/profile URL without guessing an account."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Instagram profile URL or handle is required")
    if raw.startswith(("http://", "https://")):
        match = re.fullmatch(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?(?:\?.*)?", raw)
        if not match or match.group(1).lower() in {"p", "reel", "explore", "accounts"}:
            raise ValueError("Use an exact Instagram profile URL, not a post or discovery URL")
        username = normalize_instagram_username(match.group(1))
    else:
        username = normalize_instagram_username(raw.lstrip("@"))
        if username == "unknown_brand" or username != raw.lstrip("@").lower():
            raise ValueError("Invalid Instagram handle")
    return username, f"https://www.instagram.com/{username}/"


def curated_brand(value: str, *, brand_name: str = "", category_hint: str = "", priority: str = "") -> BrandMetadata:
    username, profile_url = parse_instagram_identity(value)
    return BrandMetadata(
        brand_name=normalize_brand_name(brand_name or username), instagram_username=username,
        instagram_profile_url=profile_url, profile_picture_url="", followers=0,
        niche_score=calculate_brand_niche_score(0, brand_name or username),
        source_hashtag=f"curated:{category_hint or 'unspecified'}:{priority or 'normal'}",
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )


def load_curated_brand_list(path: Path) -> list[BrandMetadata]:
    brands: list[BrandMetadata] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), 2):
            value = (row.get("instagram_url") or row.get("instagram_handle") or "").strip()
            if not value:
                name = str(row.get("brand_name") or f"Row {line_number}").strip()
                print(f"{name}\nSTATUS: MISSING_INSTAGRAM_SOURCE\n\nPlease add instagram_url or instagram_handle.\n")
                continue
            brands.append(curated_brand(value, brand_name=row.get("brand_name") or "", category_hint=row.get("category_hint") or "", priority=row.get("priority") or ""))
    unique = {brand.instagram_username: brand for brand in brands}
    return list(unique.values())


def infer_brand_from_alt_text(alt_text: str) -> str:
    match = re.search(r"Photo by\s+(.+?)\s+on\s+", alt_text, flags=re.IGNORECASE)
    if match:
        return normalize_brand_name(match.group(1))
    return "unknown_brand"


def parse_follower_count(raw_text: str | None) -> int:
    text = (raw_text or "").strip().lower().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmb])?\s+followers", text)
    if not match:
        return 0

    value = float(match.group(1))
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2), 1)
    return int(value * multiplier)


def extract_brand(description: str, source_hashtag: str, followers: int = 0) -> BrandMetadata:
    brand_name = normalize_brand_name(infer_brand_from_alt_text(description))
    username = normalize_instagram_username(brand_name)
    instagram_profile_url = f"https://www.instagram.com/{username}/" if username != "unknown_brand" else ""
    follower_count = max(int(followers or 0), 0)
    return BrandMetadata(
        brand_name=brand_name,
        instagram_username=username,
        instagram_profile_url=instagram_profile_url,
        profile_picture_url="",
        followers=follower_count,
        niche_score=calculate_brand_niche_score(follower_count, brand_name),
        source_hashtag=source_hashtag,
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )


def extract_profile_picture_from_page(driver: Any) -> str:
    try:
        value = driver.execute_script(
            """
            const profileImages = Array.from(document.querySelectorAll("header img[alt*='profile picture' i], main header img[alt*='profile picture' i]"));
            for (const node of profileImages) {
              const srcset = node.srcset || "";
              if (srcset) {
                const candidates = srcset.split(",")
                  .map((entry) => entry.trim().split(/\\s+/))
                  .filter((parts) => parts[0]);
                candidates.sort((a, b) => (parseInt(b[1]) || 0) - (parseInt(a[1]) || 0));
                if (candidates.length && candidates[0][0].startsWith("http")) return candidates[0][0];
              }
              const src = node.currentSrc || node.src || "";
              if (src.startsWith("http") && !src.includes("data:image")) return src;
            }

            const metaImage = document.querySelector("meta[property='og:image'], meta[name='twitter:image']");
            return metaImage?.content || "";
            """
        )
    except Exception as error:
        LOGGER.debug("Profile picture selector fallback failed: %s", error)
        return ""

    return str(value or "").strip()


def extract_followers_from_page(driver: Any) -> int:
    try:
        raw_text = driver.execute_script(
            """
            const meta = document.querySelector("meta[property='og:description'], meta[name='description']");
            if (meta?.content) return meta.content;
            return document.body?.innerText || "";
            """
        )
    except Exception as error:
        LOGGER.debug("Follower extraction fallback failed: %s", error)
        return 0

    followers = parse_follower_count(str(raw_text or ""))
    LOGGER.debug("Profile follower extraction raw=%r parsed=%s", raw_text, followers)
    return followers


def extract_profile_bio_from_page(driver: Any) -> str:
    try:
        value = driver.execute_script("""
          const meta = document.querySelector("meta[property='og:description'], meta[name='description']");
          return meta?.content || document.querySelector('header section')?.innerText || '';
        """)
    except Exception:
        return ""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:500]


def enrich_brand_profile(
    driver: Any,
    brand: BrandMetadata,
    profile_cache: dict[str, BrandMetadata],
) -> BrandMetadata:
    if not brand.instagram_profile_url or brand.instagram_username == "unknown_brand":
        return brand
    if brand.instagram_username in profile_cache:
        return profile_cache[brand.instagram_username]

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    def load_profile() -> BrandMetadata:
        driver.get(brand.instagram_profile_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "main, header")))
        random_delay(1.0, 2.2)
        followers = extract_followers_from_page(driver) or brand.followers
        profile_picture_url = extract_profile_picture_from_page(driver)
        bio = extract_profile_bio_from_page(driver)
        enriched = BrandMetadata(
            brand_name=brand.brand_name,
            instagram_username=brand.instagram_username,
            instagram_profile_url=brand.instagram_profile_url,
            profile_picture_url=profile_picture_url,
            followers=max(int(followers or 0), 0),
            niche_score=calculate_brand_niche_score(max(int(followers or 0), 0), brand.brand_name),
            source_hashtag=brand.source_hashtag,
            scraped_at=brand.scraped_at,
            bio=bio,
        )
        LOGGER.info(
            "Enriched brand profile",
            extra={
                "instagram_username": enriched.instagram_username,
                "followers": enriched.followers,
                "has_profile_picture": bool(enriched.profile_picture_url),
            },
        )
        return enriched

    try:
        enriched_brand = retry(load_profile, attempts=3, label=f"load brand profile {brand.instagram_username}")
    except Exception as error:
        LOGGER.warning("Could not enrich Instagram profile for %s: %s", brand.instagram_username, error)
        enriched_brand = brand

    profile_cache[brand.instagram_username] = enriched_brand
    return enriched_brand


def looks_like_unparsed_caption(text: str) -> bool:
    """True if text is Instagram metadata/caption leakage rather than a product name.

    Catches cases the "Photo by X on Instagram:" prefix-strip misses, e.g. "Photo by
    X in Y with @handle." or Instagram's auto-generated alt text ("May be an image
    of ..."), both of which otherwise pass the generic length/marketing-word checks
    and were previously stored verbatim as product_name.
    """
    candidate = (text or "").strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    if re.match(r"^(?:photo|video|reel)s?\s+(?:by|shared by)\b", lowered):
        return True
    if "may be an image of" in lowered:
        return True
    if re.search(r"@[a-z0-9_.]+", lowered):
        return True
    # Instagram's engagement-count alt text ("4 likes, 0 comments - username"),
    # used as a stand-in description when a post has no real caption.
    if re.match(r"^[\d,]+\s+likes?,\s*[\d,]+\s+comments?\b", lowered):
        return True
    return False


def clean_product_name(description: str, hashtag: str) -> str | None:
    text = re.sub(r"\s+", " ", description or "").strip()
    text = re.sub(r"^Photo by .+? on Instagram:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip(" -—:|")
    text = re.sub(r"(?:₹|rs\.?|inr)\s*[0-9][0-9,.]*(?:\s*[kK])?(?:\s*/-)?(?:\s+onwards)?", "", text, flags=re.I)
    text = re.sub(r"\b(?:shop now|link in bio|dm (?:us )?to order|available now)\b.*$", "", text, flags=re.I)
    text = re.sub(r"#[A-Za-z0-9_]+", "", text)
    candidate = re.split(r"[.!?]|\s+[|•]\s+", text, maxsplit=1)[0].strip(" -—:|,.")
    generic_or_marketing = re.search(r"\b(?:bestselling|bestseller|finally back|shop now|new drop|collection|launching|introducing)\b", candidate, re.I)
    if (
        len(candidate) >= 4
        and len(candidate) <= 100
        and not generic_or_marketing
        and not looks_like_unparsed_caption(candidate)
        and candidate.lower() not in {"latest product", "new product", "instagram product", "product", "new drop", "collection"}
    ):
        return candidate
    family, product_type = identify_product(text)
    if product_type:
        colour = next((value for value in ("black", "white", "silver", "gold", "brown", "beige", "blue", "navy", "red", "green", "pink", "purple", "grey", "orange", "yellow") if re.search(rf"\b{value}\b", text, re.I)), "")
        material = next((value for value in ("linen", "cotton", "silk", "denim", "leather", "satin", "wool", "velvet") if re.search(rf"\b{value}\b", text, re.I)), "")
        concise = " ".join(value.title() for value in (colour, material, TYPE_LABELS.get(product_type, product_type)) if value)
        if concise:
            return concise[:100]
    return fallback_product_name(description, hashtag)


def extract_fashion_metadata(post: ScrapedInstagramPost) -> dict[str, Any]:
    text = post.description.lower(); family, product_type = identify_product(text)
    values = lambda options: [value for value in options if re.search(rf"\b{re.escape(value)}\b", text)]
    colours = values(("black", "white", "silver", "gold", "brown", "beige", "blue", "navy", "red", "green", "pink", "purple", "grey", "orange", "yellow"))
    styles = values(("minimal", "streetwear", "party", "formal", "casual", "ethnic", "y2k", "vintage", "quiet luxury", "boho", "sporty", "trendy", "contemporary"))
    fits = values(("oversized", "fitted", "slim", "relaxed", "wide leg", "straight leg", "cropped"))
    materials = values(("linen", "cotton", "silk", "denim", "leather", "satin", "wool", "velvet"))
    patterns = values(("floral", "striped", "checked", "printed", "solid", "embroidered"))
    occasions = values(("party", "festive", "wedding", "college", "work", "date night", "everyday"))
    return {"caption":post.description, "hashtags":re.findall(r"#([A-Za-z0-9_]+)", post.description), "product_family":family, "product_type":product_type, "primary_colour":colours[0] if colours else None, "secondary_colours":colours[1:], "style":styles, "fit":fits[0] if fits else None, "material":materials[0] if materials else None, "pattern":patterns[0] if patterns else None, "occasion":occasions, "metadata_source":"deterministic_caption"}


def extract_price(text: str) -> float | None:
    match = re.search(
        r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*([kK])?(?:\s*/-)?(?:\s+onwards)?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        value = float(match.group(1).replace(",", ""))
        return value * 1000 if match.group(2) else value
    shorthand = re.search(r"(?<![\w₹])([0-9]+(?:\.[0-9]+)?)\s*[kK]\b", text)
    return float(shorthand.group(1)) * 1000 if shorthand else None


PRODUCT_TERMS = {
    "dress", "gown", "kurta", "kurti", "saree", "shirt", "t-shirt", "tshirt", "tee",
    "top", "blouse", "jeans", "trousers", "pants", "skirt", "jacket", "coat", "blazer",
    "earring", "earrings", "necklace", "ring", "bracelet", "jewellery", "jewelry", "bag",
    "tote", "clutch", "shoes", "sneakers", "heels", "sandals", "watch", "belt", "sunglasses",
    "scrunchie", "co-ord", "coord", "activewear", "hoodie", "sweater", "shorts", "lehenga",
}
NON_PRODUCT_TERMS = {
    "meme", "giveaway winner", "happy birthday", "behind the scenes", "moodboard",
    "hiring", "job opening", "store closed", "holiday notice", "quote of the day",
}


def instagram_post_id(url: str) -> str:
    # Instagram post URLs may or may not include the author's username before
    # "/p/" or "/reel/" (e.g. instagram.com/p/ID vs instagram.com/username/p/ID);
    # both are valid and currently in use in this catalog's stored URLs.
    match = re.search(r"instagram\.com/(?:[^/?#]+/)?(?:p|reel)/([^/?#]+)", url, flags=re.I)
    return match.group(1) if match else ""


def is_likely_product_post(post: ScrapedInstagramPost) -> tuple[bool, str]:
    text = re.sub(r"[_#]", " ", post.description.lower())
    if any(term in text for term in NON_PRODUCT_TERMS) and not any(term in text for term in PRODUCT_TERMS):
        return False, "non-product announcement or social content"
    product_hits = sum(1 for term in PRODUCT_TERMS if re.search(rf"\b{re.escape(term)}s?\b", text))
    commerce_hits = sum(1 for term in ("₹", " rs", "inr", "shop", "order", "launch", "collection", "available", "size") if term in text)
    if product_hits or (commerce_hits >= 2 and post.image_url.startswith(("http://", "https://"))):
        return True, "fashion/product and commerce signals"
    return False, "no reliable fashion product signal"


def fallback_product_name(description: str, category_slug: str) -> str | None:
    """Derive a name from colour/material/garment keywords in the caption.

    Returns None (rather than a generic category label) when no such keyword is
    present at all -- callers that must always store a name (live ingestion) should
    substitute their own default; callers auditing/backfilling existing data should
    treat None as "flag for review", not a name to invent.
    """
    text = re.sub(r"^Photo by .+? on Instagram:\s*", "", description or "", flags=re.I).lower()
    colors = ["black", "white", "blue", "green", "red", "pink", "beige", "brown", "silver", "gold", "embroidered"]
    materials = ["linen", "cotton", "silk", "denim", "leather", "velvet"]
    nouns = sorted(PRODUCT_TERMS, key=len, reverse=True)
    words = [word.title() for word in colors if re.search(rf"\b{word}\b", text)]
    words += [word.title() for word in materials if re.search(rf"\b{word}\b", text)]
    noun = next((word for word in nouns if re.search(rf"\b{re.escape(word)}s?\b", text)), "")
    if noun:
        words.append(noun.title())
    if words:
        return " ".join(dict.fromkeys(words))[:100]
    return None


def parse_engagement_count(raw_text: str | None, label: str) -> int:
    text = (raw_text or "").strip().lower().replace(",", "")
    match = re.search(rf"([0-9]+(?:\.[0-9]+)?)\s*([kmb])?\s+{re.escape(label.lower())}", text)
    if not match:
        return 0

    value = float(match.group(1))
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2), 1)
    return int(value * multiplier)


def validate_scraped_post(post: ScrapedInstagramPost) -> None:
    if not post.product_url.startswith("https://www.instagram.com/"):
        raise ValueError(f"Invalid Instagram product URL: {post.product_url}")
    if not post.image_url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid image URL for {post.product_url}")
    if not post.description.strip():
        raise ValueError(f"Missing description for {post.product_url}")


GENERIC_PRODUCT_NAMES = {"product", "instagram product", "new drop", "collection", "look", "new arrival", "latest product", "women tops", "uncategorized"}


def product_quality_gate(product: ProductCandidate, post: ScrapedInstagramPost, *, duplicate: bool = False) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not product.brand_name or normalize_instagram_username(product.instagram_username or product.brand_name) == "unknown_brand": reasons.append("invalid_brand_relation")
    name = re.sub(r"\s+", " ", product.product_name or "").strip().lower()
    if len(name) < 4 or name in GENERIC_PRODUCT_NAMES or re.fullmatch(r"(?:look|product)\s*\d*", name): reasons.append("generic_product_name")
    if not post.product_url.startswith("https://www.instagram.com/"): reasons.append("invalid_source_post")
    if not post.image_url.startswith(("http://", "https://")): reasons.append("missing_or_unrecoverable_image")
    if duplicate: reasons.append("duplicate")
    likely_product, _ = is_likely_product_post(post)
    if not likely_product: reasons.append("non_product_post")
    if product.category_id is None: reasons.append("missing_category")
    if product.classifier_confidence < .45 and product.normalized_subcategory != "uncategorized": reasons.append("contradictory_or_weak_category")
    return not reasons, reasons


def validate_embedding(values: list[float], *, dimensions: int = 512, norm_tolerance: float = .02) -> dict[str, Any]:
    if len(values) != dimensions: raise ValueError(f"Expected {dimensions}-dimensional embedding, got {len(values)}")
    if not all(math.isfinite(float(value)) for value in values): raise ValueError("Embedding contains non-finite values")
    norm = math.sqrt(sum(float(value) ** 2 for value in values))
    if abs(norm - 1.0) > norm_tolerance: raise ValueError(f"Embedding L2 norm {norm:.6f} is not approximately 1")
    return {"dimensions":len(values), "l2_norm":norm, "valid":True}


def normalize_embedding_payload(value: Any) -> list[float]:
    if isinstance(value, list): return [float(item) for item in value]
    if isinstance(value, str):
        try: return [float(item) for item in value.strip().strip("[]").split(",") if item.strip()]
        except ValueError: return []
    return []


def create_selenium_driver() -> Any:
    try:
        import undetected_chromedriver as uc
    except (ImportError, ModuleNotFoundError) as error:
        uc = None
        LOGGER.warning("undetected-chromedriver unavailable (%s); using standard Selenium Chrome", error)

    if uc is not None:
        options = uc.ChromeOptions()
    else:
        from selenium.webdriver import ChromeOptions
        options = ChromeOptions()
    options.add_argument(f"--user-data-dir={SELENIUM_PROFILE_DIR.resolve()}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    )
    if SELENIUM_HEADLESS:
        options.add_argument("--headless=new")

    LOGGER.info("Starting Selenium Chrome with profile %s", SELENIUM_PROFILE_DIR)
    chrome_major_version = os.getenv("CHROME_MAJOR_VERSION")
    if uc is not None and chrome_major_version:
        return uc.Chrome(
            options=options,
            version_main=int(chrome_major_version),
            use_subprocess=True,
        )
    if uc is not None:
        return uc.Chrome(options=options, use_subprocess=True)
    from selenium import webdriver
    return webdriver.Chrome(options=options)


def is_instagram_logged_in(driver: Any) -> bool:
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By

    try:
        current_url = driver.current_url or ""
        if "/accounts/login" in current_url:
            return False
        if driver.find_elements(By.CSS_SELECTOR, "input[name='username'], input[name='password']"):
            return False
        return bool(
            driver.find_elements(
                By.CSS_SELECTOR,
                "a[href='/'], a[href='/explore/'], a[href*='/direct/'], svg[aria-label='Home']",
            )
        )
    except WebDriverException:
        return False


def scrape_instagram(
    hashtags: Iterable[str],
    *,
    per_hashtag_limit: int = 12,
) -> list[ScrapedInstagramPost]:
    hashtag_list = [hashtag.strip().lstrip("#") for hashtag in hashtags if hashtag.strip().lstrip("#")]
    posts: list[ScrapedInstagramPost] = []
    driver = create_selenium_driver()
    try:
        login(driver)
        seen_urls: set[str] = set()
        profile_cache: dict[str, BrandMetadata] = {}
        for clean_hashtag in hashtag_list:
            posts.extend(
                scrape_hashtag(
                    driver,
                    clean_hashtag,
                    per_hashtag_limit=per_hashtag_limit,
                    seen_urls=seen_urls,
                    profile_cache=profile_cache,
                )
            )
    finally:
        driver.quit()

    LOGGER.info("Scraped %s Instagram candidate post(s)", len(posts))
    return posts


def load_brands_for_product_scraping(
    supabase: Any,
    *,
    limit: int | None = None,
) -> list[BrandMetadata]:
    response = supabase.table("brands").select("*").execute()
    rows = response.data or []
    brands: list[BrandMetadata] = []

    for row in rows:
        name = normalize_brand_name(row.get("name") or row.get("brand_name") or row.get("instagram_username"))
        username = normalize_instagram_username(row.get("instagram_username") or name)
        if username == "unknown_brand":
            continue
        brands.append(
            BrandMetadata(
                brand_name=name,
                instagram_username=username,
                instagram_profile_url=row.get("instagram_profile_url")
                or row.get("profile_url")
                or f"https://www.instagram.com/{username}/",
                profile_picture_url=row.get("profile_picture_url") or "",
                followers=max(int(row.get("followers") or 0), 0),
                niche_score=float(row.get("niche_score") or calculate_brand_niche_score(int(row.get("followers") or 0), name)),
                source_hashtag="brand_profile",
                scraped_at=datetime.now(timezone.utc).isoformat(),
            )
        )

    brands.sort(key=lambda brand: brand.instagram_username)
    if limit is not None and limit > 0:
        return brands[:limit]
    return brands


def scrape_brand_profile(
    driver: Any,
    brand: BrandMetadata,
    *,
    per_brand_limit: int = 12,
    seen_urls: set[str] | None = None,
) -> list[ScrapedInstagramPost]:
    seen = seen_urls if seen_urls is not None else set()
    posts: list[ScrapedInstagramPost] = []
    LOGGER.info("Scraping brand profile %s", brand.instagram_username)

    try:
        retry(
            lambda: driver.get(brand.instagram_profile_url),
            attempts=3,
            label=f"load brand profile {brand.instagram_username}",
        )
        random_delay(2.0, 4.0)
    except RuntimeError as error:
        LOGGER.warning("Skipping brand %s; profile load failed: %s", brand.instagram_username, error)
        return posts

    anchors = scroll_for_candidates(
        driver,
        target_count=per_brand_limit,
        max_scrolls=DEFAULT_SCROLL_LIMIT,
        stall_limit=SCROLL_STALL_LIMIT,
    )

    for entry in anchors:
        if len(posts) >= per_brand_limit:
            break
        product_url = (entry.get("href") or "").split("?")[0].rstrip("/")
        page_data = extract_post_page_data(driver, product_url)
        image_url = page_data.get("image_url") or entry.get("img") or ""
        description = page_data.get("description") or (entry.get("alt") or "").strip()
        if not description:
            description = f"Photo by {brand.instagram_username} on Instagram: Latest product from {brand.brand_name}"

        if not product_url or product_url in seen:
            LOGGER.debug("Skipping duplicate Instagram URL: %s", product_url)
            continue

        post = ScrapedInstagramPost(
            product_url=product_url,
            image_url=image_url,
            description=description,
            brand_name=brand.brand_name,
            source_hashtag=brand.source_hashtag,
            followers=brand.followers,
            instagram_username=brand.instagram_username,
            instagram_profile_url=brand.instagram_profile_url,
            profile_picture_url=brand.profile_picture_url,
            brand_niche_score=brand.niche_score,
            likes_count=parse_engagement_count(description, "likes"),
            comments_count=parse_engagement_count(description, "comments"),
            scraped_at=datetime.now(timezone.utc).isoformat(),
            instagram_post_id=instagram_post_id(product_url),
            extra_image_urls=page_data.get("extra_image_urls", ()),
            metadata={"post_type": "reel" if "/reel/" in product_url else "post", "brand_bio":brand.bio},
        )
        try:
            validate_scraped_post(post)
        except ValueError as error:
            LOGGER.info("Skipping invalid post for %s: %s", brand.instagram_username, error)
            continue

        posts.append(post)
        seen.add(product_url)

    LOGGER.info("Scraped %s post(s) for brand %s", len(posts), brand.instagram_username)
    return posts


def scrape_instagram_brand_profiles(
    brands: Iterable[BrandMetadata],
    *,
    per_brand_limit: int = 12,
    on_failure: Callable[[BrandMetadata, str, Exception], None] | None = None,
) -> list[ScrapedInstagramPost]:
    brand_list = list(brands)
    posts: list[ScrapedInstagramPost] = []
    driver = create_selenium_driver()
    try:
        login(driver)
        seen_urls: set[str] = set()
        for index, brand in enumerate(brand_list, start=1):
            LOGGER.info("[BRAND] Processing %s (%s/%s)", brand.instagram_username, index, len(brand_list))
            try:
                brand = enrich_brand_profile(driver, brand, {})
                brand_posts = scrape_brand_profile(
                    driver,
                    brand,
                    per_brand_limit=per_brand_limit,
                    seen_urls=seen_urls,
                )
                LOGGER.info("[POST] %s posts found", len(brand_posts))
                posts.extend(brand_posts)
            except Exception as error:
                LOGGER.exception("Skipping brand %s due to scrape failure: %s", brand.instagram_username, error)
                if on_failure is not None:
                    on_failure(brand, "brand_scrape", error)
                continue
    finally:
        driver.quit()

    LOGGER.info("Scraped %s Instagram candidate post(s) across %s brand(s)", len(posts), len(brand_list))
    return posts


def login(driver: Any, *, manual_only: bool = False) -> None:
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    username = None if manual_only else (os.getenv("INSTAGRAM_USERNAME") or os.getenv("IG_USERNAME"))
    password = None if manual_only else (os.getenv("INSTAGRAM_PASSWORD") or os.getenv("IG_PASSWORD"))

    driver.get("https://www.instagram.com/")
    random_delay(2.0, 4.0)
    if is_instagram_logged_in(driver):
        LOGGER.info("Instagram session already logged in.")
        return

    driver.get("https://www.instagram.com/accounts/login/")
    random_delay(2.0, 4.0)

    if username and password:
        from selenium.webdriver.common.by import By

        def submit_credentials() -> None:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='username']"))
            ).send_keys(username)
            driver.find_element(By.CSS_SELECTOR, "input[name='password']").send_keys(password)
            driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

        retry(submit_credentials, attempts=2, label="submit Instagram credentials")
        LOGGER.info("Submitted Instagram credentials; waiting for authenticated session.")
    else:
        LOGGER.info(
            "Manual Instagram login required. Log in in the opened Chrome window; "
            "scraper will wait up to %s seconds.",
            MANUAL_LOGIN_TIMEOUT_SECONDS,
        )

    try:
        WebDriverWait(driver, MANUAL_LOGIN_TIMEOUT_SECONDS).until(lambda active_driver: is_instagram_logged_in(active_driver))
        LOGGER.info("Instagram login completed")
    except Exception as error:
        LOGGER.warning("Instagram login was not confirmed before timeout: %s", error)


def collect_post_candidates(driver: Any) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for selector in POST_SELECTOR_CANDIDATES:
        try:
            elements = driver.find_elements("css selector", selector)
            selector_candidates = driver.execute_script(
                """
                return arguments[0].map(node => {
                  const href = node.href || node.getAttribute('href') || '';
                  const image = node.querySelector('img');
                  const nestedImage = image || node.closest('article')?.querySelector('img');
                  return {
                    href,
                    img: nestedImage?.src || '',
                    alt: nestedImage?.alt || node.getAttribute('aria-label') || node.textContent || ''
                  };
                })
                """,
                elements,
            )
        except Exception as error:
            LOGGER.debug("Selector fallback failed for %s: %s", selector, error)
            continue
        LOGGER.debug("Selector %s returned %s candidate(s)", selector, len(selector_candidates))
        candidates.extend(selector_candidates)

    normalized: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        href = str(candidate.get("href") or "")
        if "/p/" not in href and "/reel/" not in href:
            continue
        normalized_url = href.split("?")[0].rstrip("/")
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        normalized.append(
            {
                "href": href,
                "img": str(candidate.get("img") or ""),
                "alt": str(candidate.get("alt") or ""),
            }
        )
    return normalized


def extract_post_page_data(driver: Any, post_url: str) -> dict[str, Any]:
    """Read the caption and primary/carousel images from an Instagram post page."""
    try:
        driver.get(post_url)
        random_delay(0.8, 1.8)
        payload = driver.execute_script(
            r"""
            const description = document.querySelector(
              "meta[property='og:description'], meta[name='description']"
            )?.content || '';
            const primary = document.querySelector(
              "meta[property='og:image'], meta[name='twitter:image']"
            )?.content || '';
            const images = Array.from(document.querySelectorAll('article img, main article img'))
              .map(node => node.currentSrc || node.src || '')
              .filter(value => /^https?:\/\//.test(value));
            return {description, primary, images: [...new Set(images)]};
            """
        ) or {}
    except Exception as error:
        LOGGER.warning("Could not read post metadata for %s: %s", post_url, error)
        return {}
    images = [str(value) for value in payload.get("images", []) if str(value).startswith(("http://", "https://"))]
    primary = str(payload.get("primary") or "")
    if primary and primary not in images:
        images.insert(0, primary)
    return {
        "description": str(payload.get("description") or "").strip(),
        "image_url": primary or (images[0] if images else ""),
        "extra_image_urls": tuple(images[1:]),
    }


def scroll_for_candidates(
    driver: Any,
    *,
    target_count: int,
    max_scrolls: int = DEFAULT_SCROLL_LIMIT,
    stall_limit: int = SCROLL_STALL_LIMIT,
) -> list[dict[str, str]]:
    candidates_by_url: dict[str, dict[str, str]] = {}
    stalled_scrolls = 0

    for scroll_index in range(max_scrolls + 1):
        candidates = retry(
            lambda: collect_post_candidates(driver),
            attempts=3,
            label=f"collect Instagram anchors scroll={scroll_index}",
        )
        before_count = len(candidates_by_url)
        for candidate in candidates:
            product_url = (candidate.get("href") or "").split("?")[0]
            if product_url and product_url not in candidates_by_url:
                candidates_by_url[product_url] = candidate

        new_count = len(candidates_by_url) - before_count
        LOGGER.info(
            "Instagram scroll %s collected %s candidate(s), %s new",
            scroll_index,
            len(candidates_by_url),
            new_count,
        )
        if len(candidates_by_url) >= target_count:
            break

        stalled_scrolls = stalled_scrolls + 1 if new_count == 0 else 0
        if stalled_scrolls >= stall_limit:
            LOGGER.info("Stopping scroll after %s stalled pass(es)", stalled_scrolls)
            break

        driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(900, 1800))
        random_delay(1.1, 3.2)

    return list(candidates_by_url.values())


def scrape_hashtag(
    driver: Any,
    hashtag: str,
    *,
    per_hashtag_limit: int = 12,
    seen_urls: set[str] | None = None,
    profile_cache: dict[str, BrandMetadata] | None = None,
) -> list[ScrapedInstagramPost]:
    clean_hashtag = hashtag.strip().lstrip("#")
    url = f"https://www.instagram.com/explore/tags/{clean_hashtag}/"
    seen = seen_urls if seen_urls is not None else set()
    posts: list[ScrapedInstagramPost] = []
    cached_profiles = profile_cache if profile_cache is not None else {}
    LOGGER.info("Scraping Instagram hashtag #%s", clean_hashtag)

    try:
        retry(
            lambda: driver.get(url),
            attempts=3,
            label=f"load hashtag #{clean_hashtag}",
        )
        random_delay(2.0, 4.5)
    except RuntimeError as error:
        LOGGER.warning("Timed out loading %s: %s", url, error)
        return posts

    anchors = scroll_for_candidates(
        driver,
        target_count=per_hashtag_limit,
        max_scrolls=DEFAULT_SCROLL_LIMIT,
        stall_limit=SCROLL_STALL_LIMIT,
    )

    for entry in anchors:
        if len(posts) >= per_hashtag_limit:
            break
        product_url = (entry.get("href") or "").split("?")[0].rstrip("/")
        page_data = extract_post_page_data(driver, product_url)
        image_url = page_data.get("image_url") or entry.get("img") or ""
        description = page_data.get("description") or entry.get("alt") or ""

        if not product_url or product_url in seen:
            LOGGER.debug("Skipping duplicate Instagram URL: %s", product_url)
            continue

        brand = enrich_brand_profile(
            driver,
            extract_brand(description, clean_hashtag),
            cached_profiles,
        )
        post = ScrapedInstagramPost(
            product_url=product_url,
            image_url=image_url,
            description=description,
            brand_name=brand.brand_name,
            source_hashtag=brand.source_hashtag,
            followers=brand.followers,
            instagram_username=brand.instagram_username,
            instagram_profile_url=brand.instagram_profile_url,
            profile_picture_url=brand.profile_picture_url,
            brand_niche_score=brand.niche_score,
            likes_count=parse_engagement_count(description, "likes"),
            comments_count=parse_engagement_count(description, "comments"),
            scraped_at=datetime.now(timezone.utc).isoformat(),
            instagram_post_id=instagram_post_id(product_url),
            extra_image_urls=page_data.get("extra_image_urls", ()),
            metadata={"post_type": "reel" if "/reel/" in product_url else "post"},
        )
        try:
            validate_scraped_post(post)
        except ValueError as error:
            LOGGER.info("Skipping invalid Instagram post: %s", error)
            continue

        posts.append(post)
        seen.add(product_url)

    LOGGER.info("Scraped %s post(s) for #%s", len(posts), clean_hashtag)
    return posts


def calculate_brand_niche_score(followers: int, brand_name: str) -> float:
    if normalize_brand_name(brand_name) == "unknown_brand":
        return 0.5
    if followers >= 5_000:
        return 1.0
    if followers >= 1_000:
        return 0.8
    return 0.75


def brand_cache_row(post: ScrapedInstagramPost) -> dict[str, str]:
    username = post.instagram_username or normalize_instagram_username(post.brand_name)
    instagram_profile_url = (
        post.instagram_profile_url
        or (f"https://www.instagram.com/{username}/" if username != "unknown_brand" else "")
    )
    followers = max(int(post.followers or 0), 0)
    niche_score = post.brand_niche_score or calculate_brand_niche_score(followers, post.brand_name)
    brand = BrandMetadata(
        brand_name=normalize_brand_name(post.brand_name),
        instagram_username=username,
        instagram_profile_url=instagram_profile_url,
        profile_picture_url=post.profile_picture_url or "",
        followers=followers,
        niche_score=niche_score,
        source_hashtag=post.source_hashtag,
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )
    return {
        "brand_name": brand.brand_name,
        "instagram_username": brand.instagram_username,
        "instagram_profile_url": brand.instagram_profile_url,
        "profile_url": brand.profile_url,
        "profile_picture_url": brand.profile_picture_url,
        "followers": str(brand.followers),
        "niche_score": f"{brand.niche_score:.2f}",
        "source_hashtag": brand.source_hashtag,
        "scraped_at": brand.scraped_at,
    }


def load_brand_cache(cache_path: Path) -> dict[str, dict[str, str]]:
    if not cache_path.exists():
        return {}

    with cache_path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            username = normalize_instagram_username(
                row.get("instagram_username") or row.get("brand_name") or row.get("name")
            )
            if username == "unknown_brand":
                continue
            rows[username] = {field: str(row.get(field, "") or "") for field in BRAND_CACHE_FIELDNAMES}
            rows[username]["instagram_username"] = username
            if not rows[username].get("instagram_profile_url"):
                rows[username]["instagram_profile_url"] = row.get("profile_url", "") or (
                    f"https://www.instagram.com/{username}/"
                )
            if not rows[username].get("profile_url"):
                rows[username]["profile_url"] = rows[username]["instagram_profile_url"]
        return rows


def save_brand_csv(
    posts: Iterable[ScrapedInstagramPost],
    *,
    cache_path: Path | str = BRAND_CACHE_PATH,
) -> None:
    path = Path(cache_path)
    rows = load_brand_cache(path)
    updated_count = 0

    for post in posts:
        row = brand_cache_row(post)
        username = row["instagram_username"]
        if username == "unknown_brand":
            continue
        rows[username] = row
        updated_count += 1

    if updated_count == 0:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=BRAND_CACHE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows[username] for username in sorted(rows))

    LOGGER.info("Updated brand cache %s with %s discovered brand(s)", path, updated_count)


append_update_brand_cache = save_brand_csv


def dedupe_posts(posts: Iterable[ScrapedInstagramPost]) -> list[ScrapedInstagramPost]:
    unique_posts: list[ScrapedInstagramPost] = []
    seen_keys: set[tuple[str, str]] = set()

    for post in posts:
        normalized_url = post.product_url.split("?")[0].rstrip("/").lower()
        post_id = post.instagram_post_id or instagram_post_id(normalized_url)
        strong_keys = [("post_id", post_id), ("product_url", normalized_url)]
        image_url = post.image_url.split("?")[0].lower()
        if image_url:
            strong_keys.append((f"brand_image:{normalize_instagram_username(post.brand_name)}", image_url))
        if any(value and (key, value) in seen_keys for key, value in strong_keys):
            LOGGER.info("[SKIP] Duplicate scraped post: %s", post.product_url)
            continue
        seen_keys.update((key, value) for key, value in strong_keys if value)
        unique_posts.append(post)

    return unique_posts


def fallback_category(description: str) -> dict[str, Any]:
    text = description.lower()
    matched = True
    if any(word in text for word in ["jewelry", "jewellery", "earring", "necklace", "bracelet", "ring"]):
        slug = "women-jewelry"
    elif any(word in text for word in ["bag", "tote", "purse", "clutch"]):
        slug = "women-bags"
    elif any(word in text for word in ["shoe", "heel", "sneaker", "sandal", "loafer"]):
        slug = "women-shoes"
    elif any(word in text for word in ["sunglass", "eyewear"]):
        slug = "accessories-sunglasses"
    elif "watch" in text:
        slug = "accessories-watches"
    elif "belt" in text:
        slug = "accessories-belts"
    elif any(word in text for word in ["scrunchie", "hair clip", "hairband", "headband"]):
        slug = "accessories-hair"
    elif any(word in text for word in ["kurta", "kurti", "saree", "lehenga", "ethnic"]):
        slug = "men-ethnic-wear" if any(word in text for word in [" men", "mens", "men's"]) else "women-ethnic-wear"
    elif any(word in text for word in ["co-ord", "coord", "matching set"]):
        slug = "women-co-ords"
    elif any(word in text for word in ["activewear", "sports bra", "leggings", "gym wear"]):
        slug = "women-activewear"
    elif any(word in text for word in ["dress", "gown", "frock"]):
        slug = "women-dresses"
    elif any(word in text for word in ["jacket", "coat", "blazer", "outerwear"]):
        slug = "men-jackets" if any(word in text for word in [" men", "mens", "men's"]) else "women-jackets"
    elif any(word in text for word in ["jeans", "denim jean"]):
        slug = "men-jeans" if any(word in text for word in [" men", "mens", "men's"]) else "women-jeans"
    elif any(word in text for word in ["trouser", "pants", "shorts", "skirt"]):
        slug = "men-bottoms" if any(word in text for word in [" men", "mens", "men's"]) else "women-bottoms"
    elif any(word in text for word in ["tshirt", "t-shirt", "tee"]):
        slug = "men-t-shirts" if any(word in text for word in [" men", "mens", "men's"]) else "women-tops"
    elif "shirt" in text:
        slug = "men-shirts"
    elif any(word in text for word in ["home", "decor", "lamp", "cushion", "ceramic"]):
        slug = "home-decor"
    else:
        slug = "uncategorized"
        matched = False
    return {"category_slug": slug, "confidence_score": 0.85 if matched else 0.35}


def validate_category(classification: dict[str, Any], description: str) -> dict[str, Any]:
    if not isinstance(classification, dict):
        LOGGER.warning("Invalid category payload type %s; using fallback category", type(classification).__name__)
        return fallback_category(description)

    slug = classification.get("category_slug")
    if slug not in CATEGORY_BY_SLUG:
        LOGGER.warning("Invalid category slug %r; using fallback category", slug)
        return fallback_category(description)

    try:
        confidence = float(classification.get("confidence_score", 0.0))
    except (TypeError, ValueError):
        LOGGER.warning("Invalid category confidence %r; using 0.0", classification.get("confidence_score"))
        confidence = 0.0

    confidence = max(0.0, min(confidence, 1.0))
    return {"category_slug": slug, "confidence_score": confidence}


def classify_category(openai_client: Any | None, post: ScrapedInstagramPost) -> dict[str, Any]:
    heuristic = validate_category(fallback_category(post.description), post.description)
    if openai_client is None or heuristic["confidence_score"] >= 0.7:
        return {**heuristic, "classification_source": "heuristic"}

    try:
        response = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "description": post.description,
                            "brand_name": post.brand_name,
                            "product_url": post.product_url,
                        }
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "fashion_category",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "category_slug": {
                                "type": "string",
                                "enum": list(CATEGORY_BY_SLUG.keys()),
                            },
                            "confidence_score": {"type": "number"},
                        },
                        "required": ["category_slug", "confidence_score"],
                    },
                },
            },
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        return {**validate_category(parsed, post.description), "classification_source": "openai"}
    except Exception as error:
        LOGGER.warning("Category classifier failed for %s: %s", post.product_url, error)
        return {**validate_category(fallback_category(post.description), post.description), "classification_source": "heuristic"}


def load_category_taxonomy(supabase: Any) -> dict[str, dict[str, Any]]:
    """Resolve generated category IDs from their stable slugs for this database."""
    response = supabase.table("categories").select("id,name,slug").execute()
    rows = response.data or []
    by_slug = {str(row.get("slug")): row for row in rows if row.get("slug")}
    missing = sorted(set(CATEGORY_BY_SLUG) - set(by_slug))
    if missing:
        raise RuntimeError(
            "Category migration is incomplete; missing slug(s): " + ", ".join(missing)
        )
    return by_slug


def extract_product(
    post: ScrapedInstagramPost,
    classification: dict[str, Any],
    *,
    taxonomy: dict[str, dict[str, Any]],
) -> ProductCandidate:
    classification_source = str(classification.get("classification_source") or "heuristic")
    validated_classification = validate_category(classification, post.description)
    slug = validated_classification["category_slug"]
    category = taxonomy[slug]
    return ProductCandidate(
        product_name=clean_product_name(post.description, slug) or str(category["name"]),
        brand_name=normalize_brand_name(post.brand_name),
        category=str(category["name"]),
        category_id=int(category["id"]),
        image_url=post.image_url,
        product_url=post.product_url,
        description=post.description,
        price=extract_price(post.description),
        niche_score=post.brand_niche_score
        or (0.75 if post.brand_name != "unknown_brand" else 0.5),
        classifier_confidence=float(validated_classification["confidence_score"]),
        instagram_username=post.instagram_username or normalize_instagram_username(post.brand_name),
        instagram_profile_url=post.instagram_profile_url
        or (
            f"https://www.instagram.com/{normalize_instagram_username(post.brand_name)}/"
            if normalize_instagram_username(post.brand_name) != "unknown_brand"
            else ""
        ),
        profile_picture_url=post.profile_picture_url,
        followers=max(int(post.followers or 0), 0),
        likes_count=max(int(post.likes_count or 0), 0),
        comments_count=max(int(post.comments_count or 0), 0),
        source_hashtag=post.source_hashtag,
        scraped_at=post.scraped_at or datetime.now(timezone.utc).isoformat(),
        source=post.source,
        subcategory=str(category["name"]),
        instagram_post_id=post.instagram_post_id or instagram_post_id(post.product_url),
        instagram_post_url=post.product_url,
        extra_image_urls=post.extra_image_urls,
        metadata={
            **extract_fashion_metadata(post), **(post.metadata or {}),
            "source_hashtag": post.source_hashtag,
            "extra_image_urls": list(post.extra_image_urls),
        },
        original_image_url=post.image_url,
        image_status="VALID",
        normalized_main_category=slug.split("-", 1)[0] if slug != "uncategorized" else "uncategorized",
        normalized_subcategory=slug,
        audience=slug.split("-", 1)[0].upper() if slug.startswith(("women-", "men-", "unisex-")) else "",
        classification_source=classification_source,
    )


def generate_embedding(model: SentenceTransformer, product: ProductCandidate) -> list[float]:
    import requests
    from PIL import Image, UnidentifiedImageError

    def fetch_image() -> Image.Image:
        response = requests.get(
            product.image_url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        try:
            return Image.open(BytesIO(response.content)).convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError) as error:
            raise ValueError(f"Invalid product image for {product.product_url}") from error

    image = retry(fetch_image, attempts=3, label=f"download image {product.product_url}")
    embedding = model.encode(image, normalize_embeddings=True)
    values = [float(value) for value in embedding.tolist()]
    if len(values) != 512:
        raise ValueError(f"Expected 512-dimensional CLIP embedding, got {len(values)}")
    return values


generate_clip_embedding = generate_embedding


def supabase_row_exists(
    supabase: Any,
    table_name: str,
    column_name: str,
    value: Any,
) -> bool:
    if value is None or value == "":
        return False

    response = supabase.table(table_name).select("id").eq(column_name, value).limit(1).execute()
    return bool(response.data)


def upsert_brand(supabase: Any, product: ProductCandidate) -> str | None:
    username = product.instagram_username or normalize_instagram_username(product.brand_name)

    def fetch_existing_brand_id() -> str | None:
        if username and username != "unknown_brand":
            response = (
                supabase.table("brands")
                .select("id")
                .eq("instagram_username", username)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if rows:
                return rows[0].get("id")

        response = supabase.table("brands").select("id").eq("name", product.brand_name).limit(1).execute()
        rows = response.data or []
        return rows[0].get("id") if rows else None

    payload = {
        "name": product.brand_name,
        "brand_name": product.brand_name,
        "instagram_username": username,
        "profile_url": product.instagram_profile_url or None,
        "instagram_profile_url": product.instagram_profile_url or None,
        "profile_picture_url": product.profile_picture_url or None,
        "post_url": product.product_url,
        "followers": max(int(product.followers or 0), 0),
        "niche_score": product.niche_score,
    }
    # "category" is a required (NOT NULL) column, but it can only ever hold one
    # value at a time even though a brand may sell products across several
    # categories -- overwriting it from whichever product happens to be scraped
    # last produced brand-level mislabeling (e.g. a kurti/co-ord seller displayed
    # as "Women Jewelry"). Set it once at creation only; the validated,
    # multi-category brand_categories table (rebuild_brand_categories) is the
    # authoritative source for display from then on.
    if not fetch_existing_brand_id():
        payload["category"] = product.category

    def execute_upsert() -> Any:
        return supabase.table("brands").upsert(payload, on_conflict="instagram_username").execute()

    response = retry(execute_upsert, attempts=3, label=f"upsert brand {product.brand_name}")
    rows = response.data or []
    brand_id = rows[0].get("id") if rows else None
    if brand_id:
        return brand_id

    brand_id = retry(fetch_existing_brand_id, attempts=3, label=f"fetch brand id {product.brand_name}")
    if not brand_id:
        raise RuntimeError(f"Could not resolve brand id for {product.brand_name}")
    return brand_id


upsert_brand_supabase = upsert_brand


def product_payload(product: ProductCandidate, embedding: list[float], brand_id: str | None) -> dict[str, Any]:
    return {
        # "name"/"brand" are legacy required (NOT NULL) columns kept in parallel with
        # product_name/brand_name -- mirrors the same dual-column handling
        # upsert_brand() already does for the brands table.
        "name": product.product_name,
        "brand": product.brand_name,
        "brand_id": brand_id,
        "brand_name": product.brand_name,
        "product_name": product.product_name,
        "item_name": product.product_name,
        "description": product.description,
        "raw_caption": product.description,
        "image_url": product.image_url,
        "category_id": product.category_id,
        "embedding": embedding,
        "source": product.source,
        "source_url": product.product_url,
        "product_url": product.product_url,
        # price is NOT NULL on the live table; every existing row uses 0.0 (never
        # null) as the "no price found" sentinel, so match that convention.
        "price": product.price if product.price is not None else 0.0,
        "currency": product.currency,
        "subcategory": product.subcategory,
        "instagram_post_id": product.instagram_post_id or None,
        "instagram_post_url": product.instagram_post_url or product.product_url,
        "metadata": product.metadata or {},
        "original_image_url": product.original_image_url or product.image_url,
        "image_status": product.image_status,
        "normalized_main_category": product.normalized_main_category or None,
        "normalized_subcategory": product.normalized_subcategory or None,
        "audience": product.audience or None,
        "classification_source": product.classification_source,
        "likes_count": product.likes_count,
        "comments_count": product.comments_count,
        "source_hashtag": product.source_hashtag or None,
        "scraped_at": product.scraped_at,
        "niche_score": product.niche_score,
        "classifier_confidence": product.classifier_confidence,
    }


def upsert_product(
    supabase: Any,
    product: ProductCandidate,
    embedding: list[float],
) -> dict[str, Any] | None:
    brand_id = upsert_brand(supabase, product)
    if not brand_id:
        raise RuntimeError(f"Cannot upsert product without brand_id for {product.product_url}")
    payload = product_payload(product, embedding, brand_id)
    LOGGER.info(
        "Upserting product payload",
        extra={
            "product_url": product.product_url,
            "brand_id": brand_id,
            "brand_name": product.brand_name,
            "product_name": product.product_name,
            "category_id": product.category_id,
            "image_url": product.image_url,
            "price": payload["price"],
            "embedding_length": len(embedding),
            "payload_columns": sorted(payload.keys()),
        },
    )

    def execute_upsert() -> Any:
        return supabase.table("products").upsert(
            payload,
            on_conflict="source,product_url",
        ).execute()

    response = retry(execute_upsert, attempts=3, label=f"upsert product {product.product_url}")
    rows = response.data or []
    LOGGER.info("Upserted product %s from %s", product.product_name, product.product_url)
    return rows[0] if rows else None


upsert_product_supabase = upsert_product


def record_ingestion_failure(supabase: Any, post: ScrapedInstagramPost, stage: str, error: Exception) -> None:
    payload = {
        "brand_name": normalize_brand_name(post.brand_name),
        "stage": stage,
        "message": str(error)[:2000],
        "recoverable": not isinstance(error, (KeyboardInterrupt, SystemExit)),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("ingestion_failures").insert(payload).execute()
    except Exception as report_error:
        LOGGER.warning("Could not persist ingestion failure report: %s", report_error)


def record_brand_failure(supabase: Any, brand: BrandMetadata, stage: str, error: Exception) -> None:
    payload = {
        "brand_name": brand.brand_name,
        "stage": stage,
        "message": str(error)[:2000],
        "recoverable": not isinstance(error, (KeyboardInterrupt, SystemExit)),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = supabase.table("brands").select("id").eq("instagram_username", brand.instagram_username).limit(1).execute()
        rows = response.data or []
        if rows:
            payload["brand_id"] = rows[0].get("id")
        supabase.table("ingestion_failures").insert(payload).execute()
    except Exception as report_error:
        LOGGER.warning("Could not persist brand failure report: %s", report_error)


async def run_pipeline(
    hashtags: list[str],
    *,
    per_hashtag_limit: int = 12,
    scrape_fn: Callable[..., Any] = scrape_instagram,
    model: SentenceTransformer | None = None,
    supabase: Any | None = None,
    openai_client: Any = AUTO_OPENAI,
    brand_cache_path: Path | str | None = BRAND_CACHE_PATH,
    verify_connections: bool = True,
    dry_run: bool = False,
    store_images: bool | None = None,
    batch_id: str = "",
    audit_path: Path | None = None,
) -> list[dict[str, Any]]:
    started_at = time.monotonic()
    verify_environment(require_openai=False, require_service_role=not dry_run)
    if openai_client is AUTO_OPENAI:
        openai_client = get_openai_client()

    supabase = supabase or (get_public_supabase_client() if dry_run else get_supabase_client())
    if verify_connections:
        verify_supabase_connection(supabase)
        verify_openai_connection(openai_client)
    taxonomy = load_category_taxonomy(supabase)
    try:
        existing_rows = supabase.table("products").select("id,product_url,image_url,image_status,embedding,brand_id").execute().data or []
    except Exception:
        existing_rows = []
    existing_by_url = {str(row.get("product_url") or "").split("?", 1)[0].rstrip("/"): row for row in existing_rows if row.get("product_url")}

    if model is None and not dry_run:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_NAME)
    scrape_result = scrape_fn(hashtags, per_hashtag_limit=per_hashtag_limit)
    if inspect.isawaitable(scrape_result):
        scrape_result = await scrape_result
    raw_posts = list(scrape_result)
    scraped_posts = dedupe_posts(raw_posts)
    LOGGER.info("Prepared %s unique scraped post(s) for ingestion", len(scraped_posts))
    if brand_cache_path is not None:
        save_brand_csv(scraped_posts, cache_path=brand_cache_path)
    inserted: list[dict[str, Any]] = []
    brand_keys = {normalize_instagram_username(post.brand_name) for post in scraped_posts}
    summary = IngestionSummary(
        brands_attempted=len(brand_keys),
        posts_scanned=len(raw_posts),
        duplicates_skipped=max(0, len(raw_posts) - len(scraped_posts)),
    )
    seen_brand_keys: set[str] = set()

    for index, post in enumerate(scraped_posts, start=1):
        try:
            LOGGER.info(
                "Processing product %s/%s",
                index,
                len(scraped_posts),
                extra={
                    "product_url": post.product_url,
                    "brand_name": post.brand_name,
                    "source_hashtag": post.source_hashtag,
                },
            )
            validate_scraped_post(post)
            is_product, detection_reason = is_likely_product_post(post)
            if not is_product:
                summary.skipped += 1
                if audit_path: append_audit(audit_path,batch_id=batch_id,brand=post.brand_name,post=post.product_url,action="classify_post",outcome="SKIPPED_NON_PRODUCT",reason=detection_reason)
                LOGGER.info("[SKIP] Non-product post %s: %s", post.product_url, detection_reason)
                continue
            summary.product_posts_detected += 1
            heuristic = fallback_category(post.description)
            if openai_client is not None and float(heuristic["confidence_score"]) < 0.7:
                summary.openai_calls += 1
            else:
                summary.heuristic_classifications += 1
            classification = classify_category(openai_client, post)
            product = extract_product(post, classification, taxonomy=taxonomy)
            existing_product = existing_by_url.get(post.product_url.split("?", 1)[0].rstrip("/"))
            LOGGER.info("[PRODUCT] %s", product.product_name)
            LOGGER.info("[PRICE] %s", f"₹{product.price:g}" if product.price is not None else "not provided")
            LOGGER.info("[CATEGORY] %s", product.category)
            accepted, rejection_reasons = product_quality_gate(product, post)
            if not accepted:
                summary.quality_rejections += 1; summary.skipped += 1
                LOGGER.info("[QUALITY HOLD] %s | %s", product.product_url, ", ".join(rejection_reasons))
                if audit_path: append_audit(audit_path,batch_id=batch_id,brand=product.brand_name,post=product.product_url,action="quality_gate",outcome="REVIEW",reason=", ".join(rejection_reasons))
                continue
            if dry_run:
                summary.skipped += 1
                brand_key = product.instagram_username or normalize_instagram_username(product.brand_name)
                if brand_key not in seen_brand_keys:
                    summary.brands_successful += 1
                    seen_brand_keys.add(brand_key)
                LOGGER.info("[DRY RUN] Would ingest %s | %s | %s | image=%s | duplicate=%s | confidence=%.2f", product.product_name, product.category, product.price, "ALREADY_STABLE" if existing_product and "/storage/v1/object/public/" in str(existing_product.get("image_url") or "") else "DOWNLOAD_AND_STORE", "EXISTING_UPDATE" if existing_product else "NO", product.classifier_confidence)
                inserted.append(product_payload(product, [], None))
                continue
            should_store_images = True if store_images is None else store_images
            if should_store_images:
                summary.image_candidates += 1
                if existing_product and "/storage/v1/object/public/" in str(existing_product.get("image_url") or ""):
                    product = replace(product, image_url=existing_product["image_url"], original_image_url=product.image_url, image_status="VALID")
                    summary.images_already_stable += 1
                    LOGGER.info("[IMAGE] existing stable image reused")
                else:
                    try:
                        from backend.category_quality import upload_stable_brand_image, upload_stable_image
                        brand_id_for_path = upsert_brand(supabase, product)
                        if product.profile_picture_url and brand_id_for_path:
                            try:
                                stable_profile = upload_stable_brand_image(product.profile_picture_url, brand_id=str(brand_id_for_path), base_url=os.environ["SUPABASE_URL"], key=os.environ["SUPABASE_SERVICE_ROLE_KEY"])
                                supabase.table("brands").update({"profile_picture_url":stable_profile}).eq("id", brand_id_for_path).execute()
                                product = replace(product, profile_picture_url=stable_profile)
                            except Exception as error:
                                LOGGER.warning("[IMAGE] stable brand profile copy failed: %s", error)
                        stable_url = upload_stable_image(
                            product.image_url,
                            product=product_payload(product, [], brand_id_for_path),
                            base_url=os.environ["SUPABASE_URL"],
                            key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                        )
                        product = replace(product, image_url=stable_url, original_image_url=product.image_url, image_status="VALID")
                        summary.images_uploaded += 1
                        LOGGER.info("[IMAGE] stored stable copy: %s", stable_url)
                    except Exception as error:
                        summary.images_failed += 1
                        if audit_path: append_audit(audit_path,batch_id=batch_id,brand=product.brand_name,post=product.product_url,action="store_image",outcome="FAILED_IMAGE",reason=str(error))
                        LOGGER.warning("[IMAGE] stable copy failed; preserving recovery metadata without a temporary catalog image: %s", error)
                        product = replace(product, original_image_url=product.image_url, image_url="", image_status="RECOVERY_REQUIRED")
            try:
                existing_embedding = normalize_embedding_payload(existing_product.get("embedding")) if existing_product else []
                if existing_embedding:
                    validate_embedding(existing_embedding); embedding = existing_embedding; summary.embeddings_reused += 1
                    LOGGER.info("[EMBEDDING] reused unchanged product embedding")
                else:
                    embedding = generate_embedding(model, product)
                embedding_validation = validate_embedding(embedding)
                summary.embedding_successes += 1
                LOGGER.info("[EMBEDDING] generated | dimensions=%s | norm=%.6f", embedding_validation["dimensions"], embedding_validation["l2_norm"])
            except Exception as error:
                summary.embedding_failures += 1
                summary.errors += 1
                summary.skipped += 1
                LOGGER.warning("[EMBEDDING] failed for %s: %s", product.product_url, error)
                continue

            brand_key = product.instagram_username or normalize_instagram_username(product.brand_name)
            if brand_key not in seen_brand_keys:
                brand_exists = supabase_row_exists(supabase, "brands", "instagram_username", brand_key)
                if brand_exists:
                    summary.brands_updated += 1
                else:
                    summary.brands_inserted += 1
                seen_brand_keys.add(brand_key)
                summary.brands_successful += 1

            product_exists = supabase_row_exists(supabase, "products", "product_url", product.product_url)
            row = upsert_product(supabase, product, embedding)
            if row:
                inserted.append(row)
                if product_exists:
                    summary.products_updated += 1
                    LOGGER.info("[UPSERT] updated")
                    if audit_path: append_audit(audit_path,batch_id=batch_id,brand=product.brand_name,post=product.product_url,action="upsert_product",outcome="UPDATED")
                    LOGGER.info(
                        "Product %s/%s updated: %s",
                        index,
                        len(scraped_posts),
                        product.product_url,
                    )
                else:
                    summary.products_inserted += 1
                    LOGGER.info("[UPSERT] inserted")
                    if audit_path: append_audit(audit_path,batch_id=batch_id,brand=product.brand_name,post=product.product_url,action="upsert_product",outcome="INSERTED")
                    LOGGER.info(
                        "Product %s/%s inserted: %s",
                        index,
                        len(scraped_posts),
                        product.product_url,
                    )
            else:
                summary.skipped += 1
                LOGGER.warning(
                    "Product %s/%s skipped: Supabase returned no row for %s",
                    index,
                    len(scraped_posts),
                    product.product_url,
                )
        except Exception as error:
            summary.skipped += 1
            summary.errors += 1
            if not dry_run:
                record_ingestion_failure(supabase, post, "product_processing", error)
            LOGGER.exception(
                "Product %s/%s skipped: %s (%s)",
                index,
                len(scraped_posts),
                post.product_url,
                error,
            )

    summary.brands_failed = max(0, summary.brands_attempted - summary.brands_successful)
    summary.elapsed_seconds = round(time.monotonic() - started_at, 2)
    LOGGER.info(
        "Batch ingestion summary | Brands attempted: %s | Brands successful: %s | "
        "Posts scanned: %s | Product posts detected: %s | Products inserted: %s | "
        "Products updated: %s | Products skipped: %s | Duplicates skipped: %s | "
        "Embedding successes: %s | Embedding failures: %s | OpenAI calls: %s | "
        "Heuristic classifications: %s | Quality holds: %s | Images uploaded: %s/%s | Already stable: %s | Image failures: %s | Embeddings reused: %s | Brands failed: %s | Errors: %s | Elapsed: %.2fs",
        summary.brands_attempted,
        summary.brands_successful,
        summary.posts_scanned,
        summary.product_posts_detected,
        summary.products_inserted,
        summary.products_updated,
        summary.skipped,
        summary.duplicates_skipped,
        summary.embedding_successes,
        summary.embedding_failures,
        summary.openai_calls,
        summary.heuristic_classifications,
        summary.quality_rejections,
        summary.images_uploaded,
        summary.image_candidates,
        summary.images_already_stable,
        summary.images_failed,
        summary.embeddings_reused,
        summary.brands_failed,
        summary.errors,
        summary.elapsed_seconds,
    )
    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical Instagram product ingestion pipeline.")
    parser.add_argument(
        "--source",
        choices=("hashtags", "brands"),
        default=os.getenv("INGEST_SOURCE", "hashtags"),
        help="Use hashtag discovery or existing Supabase brands as the product source.",
    )
    parser.add_argument(
        "--hashtags",
        default=",".join(DEFAULT_HASHTAGS),
        help="Comma-separated Instagram hashtags to ingest.",
    )
    parser.add_argument(
        "--per-hashtag-limit",
        type=int,
        default=int(os.getenv("INGEST_PER_HASHTAG_LIMIT", "12")),
        help="Maximum product candidates per hashtag.",
    )
    parser.add_argument(
        "--per-brand-limit", "--posts-per-brand",
        type=int,
        default=int(os.getenv("INGEST_PER_BRAND_LIMIT", "12")),
        help="Maximum latest posts to ingest from each brand profile.",
    )
    parser.add_argument(
        "--brand-limit", "--limit-brands",
        type=int,
        default=int(os.getenv("INGEST_BRAND_LIMIT", "0")),
        help="Maximum brands to process in brand source mode. Use 0 for all brands.",
    )
    parser.add_argument(
        "--brand-name", "--brand",
        default="",
        help="Only process brands whose name or Instagram username contains this text.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Scrape and classify without embeddings or database writes.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="Use duplicate-safe upserts (enabled by default).")
    return parser.parse_args()


def parse_curated_args(command: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Canonical curated Instagram ingestion: {command}")
    if command == "add-brand":
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--instagram"); source.add_argument("--handle")
        parser.add_argument("--brand-name", default=""); parser.add_argument("--category-hint", default=""); parser.add_argument("--priority", default="high")
    elif command in {"preview-brand-batch", "ingest-brand-batch"}:
        parser.add_argument("--file", type=Path, default=Path("data/brand_batches/batch_001.csv"))
        if command == "ingest-brand-batch":
            parser.add_argument("--approve", action="store_true", help="Required confirmation; only approved CSV rows are ingested.")
    elif command == "refresh-brand":
        parser.add_argument("--brand", required=True, help="Exact stored Instagram handle or brand name.")
    elif command == "stale-brands":
        parser.add_argument("--days", type=int, default=60)
    elif command in {"instagram-login", "instagram-session-check"}:
        pass
    else:
        parser.add_argument("--file", type=Path, default=Path("data/curated_brands.csv"))
    parser.add_argument("--posts-per-brand", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(sys.argv[2:])


def run_curated_command(command: str, args: argparse.Namespace) -> None:
    if command in {"instagram-login", "instagram-session-check"}:
        driver = create_selenium_driver()
        try:
            if command == "instagram-login":
                login(driver, manual_only=True)
                status = "READY" if is_instagram_logged_in(driver) else "MANUAL_LOGIN_REQUIRED"
                print(f"INSTAGRAM LOGIN\n\nChrome profile: {SELENIUM_PROFILE_DIR.resolve()}\nLogged in: {'PASS' if status == 'READY' else 'FAIL'}\n\nSTATUS: {status}")
                return
            profile_exists = SELENIUM_PROFILE_DIR.exists()
            reachable = logged_in = profile_access = False; challenge = False
            try:
                driver.get("https://www.instagram.com/"); random_delay(1, 2)
                reachable = "instagram.com" in str(driver.current_url)
                current = str(driver.current_url).lower(); source = str(driver.page_source).lower()
                challenge = "/challenge/" in current or "/checkpoint/" in current or "challenge_required" in source
                logged_in = is_instagram_logged_in(driver) and not challenge
                if logged_in:
                    driver.get("https://www.instagram.com/instagram/"); random_delay(1, 2)
                    profile_access = "/accounts/login" not in str(driver.current_url) and "page isn't available" not in str(driver.page_source).lower()
            except Exception:
                pass
            status = "READY" if all((profile_exists, reachable, logged_in, profile_access)) and not challenge else "CHECKPOINT_REQUIRED" if challenge else "MANUAL_LOGIN_REQUIRED"
            print(f"INSTAGRAM SESSION\n\nChrome profile: {'PASS' if profile_exists else 'FAIL'}\nInstagram reachable: {'PASS' if reachable else 'FAIL'}\nLogged in: {'PASS' if logged_in else 'FAIL'}\nProfile access: {'PASS' if profile_access else 'FAIL'}\nChallenge/checkpoint: {'FAIL' if challenge else 'PASS'}\n\nSTATUS: {status}")
        finally:
            driver.quit()
        return
    if command == "stale-brands":
        supabase = get_public_supabase_client()
        brands = supabase.table("brands").select("*").execute().data or []
        products = supabase.table("products").select("brand_id,scraped_at,created_at").execute().data or []
        print(json.dumps({"stale_after_days": args.days, "brands": stale_brand_rows(brands, products, stale_days=max(1, args.days))}, indent=2))
        return
    if command == "refresh-brand":
        supabase = get_public_supabase_client() if args.dry_run else get_supabase_client()
        available = load_brands_for_product_scraping(supabase)
        needle = normalize_instagram_username(args.brand)
        brands = [brand for brand in available if normalize_instagram_username(brand.instagram_username) == needle or brand.brand_name.strip().lower() == args.brand.strip().lower()]
        if not brands:
            raise SystemExit("No exact stored brand name/Instagram handle matched; refresh does not guess brands.")
    elif command == "add-brand":
        brands = [curated_brand(args.instagram or args.handle, brand_name=args.brand_name, category_hint=args.category_hint, priority=args.priority)]
    elif command in {"preview-brand-batch", "ingest-brand-batch"}:
        if command == "ingest-brand-batch" and not args.approve:
            raise SystemExit("Refusing ingestion without --approve. Only CSV rows marked approved will be used.")
        rows = load_batch(args.file, approved_only=command == "ingest-brand-batch")
        brands = [replace(curated_brand(row.instagram_value, brand_name=row.brand_name, category_hint=row.category_hint, priority=row.priority), batch_id=row.batch_id, candidate_source=row.source) for row in rows]
        args.dry_run = command == "preview-brand-batch" or args.dry_run
    else:
        brands = load_curated_brand_list(args.file)
    if not brands:
        raise SystemExit("No curated brands with exact Instagram URLs/handles were provided.")
    supabase = get_public_supabase_client() if args.dry_run else get_supabase_client()
    before_inventory = None
    if command == "ingest-brand-batch" and not args.dry_run:
        from backend.catalog_discovery import inventory_report
        before_inventory = inventory_report()
    existing = {normalize_instagram_username(row.get("instagram_username")) for row in (supabase.table("brands").select("instagram_username").execute().data or [])}
    for brand in brands:
        LOGGER.info("[CURATED BRAND] %s | @%s | %s | category hint=%s | status=%s", brand.brand_name, brand.instagram_username, brand.instagram_profile_url, brand.source_hashtag, "UPDATE" if brand.instagram_username in existing else "NEW")

    captured: list[ScrapedInstagramPost] = []
    def scrape_curated(_: list[str], *, per_hashtag_limit: int = 12) -> list[ScrapedInstagramPost]:
        posts = scrape_instagram_brand_profiles(brands, per_brand_limit=max(1, min(args.posts_per_brand, 40)))
        batch_id = args.file.stem if command in {"preview-brand-batch", "ingest-brand-batch"} else ""
        if batch_id:
            posts = [replace(post, metadata={**(post.metadata or {}), "ingestion_batch_id": batch_id}) for post in posts]
        captured.extend(posts)
        for brand in brands:
            brand_posts = [post for post in posts if post.instagram_username == brand.instagram_username]
            product_posts = [post for post in brand_posts if is_likely_product_post(post)[0]]
            skipped = len(brand_posts) - len(product_posts); bio = next((str((post.metadata or {}).get("brand_bio") or "") for post in brand_posts if (post.metadata or {}).get("brand_bio")), "")
            LOGGER.info("[PREVIEW] accessible=%s | brand=%s | @%s | bio=%s | likely_categories=%s | inspected=%s | product_candidates=%s | non_product_skipped=%s | expected_ingest=%s | image=DOWNLOAD_VALIDATE_STORAGE | duplicate_status=%s | existing_brand=%s | status=%s", bool(brand_posts), brand.brand_name, brand.instagram_username, bio or "unavailable", brand.source_hashtag, len(brand_posts), len(product_posts), skipped, len(product_posts), "CHECK_BY_POST_AND_IMAGE", brand.instagram_username in existing, "READY" if product_posts else "REVIEW")
            if command in {"preview-brand-batch", "ingest-brand-batch"}:
                ratio = len(product_posts) / max(1, len(brand_posts))
                quality = ingestion_quality_score(profile_accessible=bool(brand_posts), fashion_relevant=bool(product_posts), product_post_ratio=ratio, recent_activity=bool(brand_posts), usable_image_ratio=sum(bool(post.image_url) for post in product_posts) / max(1, len(product_posts)), category_relevant=bool(brand.source_hashtag), estimated_products=len(product_posts), duplicate_risk=1.0 if brand.instagram_username in existing else 0.0, niche_fit=brand.niche_score)
                LOGGER.info("[BATCH QUALITY] batch=%s | @%s | score=%s | recommendation=%s", batch_id, brand.instagram_username, quality["score"], quality["recommendation"])
        return posts
    batch_id = args.file.stem if command in {"preview-brand-batch", "ingest-brand-batch"} else ""
    audit_path = Path("data/ingestion_audit") / f"{batch_id}.jsonl" if command == "ingest-brand-batch" else None
    asyncio.run(run_pipeline([], per_hashtag_limit=args.posts_per_brand, scrape_fn=scrape_curated, supabase=supabase, dry_run=args.dry_run, store_images=not args.dry_run, batch_id=batch_id, audit_path=audit_path))
    if command == "ingest-brand-batch" and not args.dry_run:
        from backend.app import build_catalog_scale_report
        from backend.catalog_discovery import inventory_report
        from backend.catalog_quality import build_catalog_report, load_catalog
        from backend.category_quality import rebuild_brand_categories
        from backend.evaluate_search_quality import DEFAULT_QUERIES, evaluate_text_queries
        rebuilt_mappings = rebuild_brand_categories(supabase)
        after_inventory = inventory_report()
        before_types = {row["type"]: row for row in (before_inventory or {}).get("inventory", [])}
        comparisons = [{**row, "before_products": before_types.get(row["type"], {}).get("products", 0), "before_brands": before_types.get(row["type"], {}).get("brands", 0), "product_change": row["products"] - before_types.get(row["type"], {}).get("products", 0), "brand_change": row["brands"] - before_types.get(row["type"], {}).get("brands", 0)} for row in after_inventory.get("inventory", [])]
        try:
            search_regression: Any = {"status": "COMPLETED", "results": evaluate_text_queries(os.getenv("SEARCH_QA_API_URL", "http://127.0.0.1:8000"), DEFAULT_QUERIES)}
        except Exception as error:
            search_regression = {"status": "BLOCKED", "reason": str(error), "command": "python -m backend.evaluate_search_quality --api-url http://127.0.0.1:8000"}
        catalog_brands, catalog_products, failures = load_catalog(supabase)
        report = {
            "batch_id": batch_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "brand_category_mappings_rebuilt": rebuilt_mappings,
            "inventory_before": before_inventory,
            "inventory_after": after_inventory,
            "inventory_comparison": comparisons,
            "search_regression": search_regression,
            "catalog_quality": build_catalog_report(catalog_brands, catalog_products, failures),
            "catalog_scale": build_catalog_scale_report(catalog_brands, catalog_products),
            "follow_up_commands": [
                "python -m backend.category_quality category-health",
                "python -m backend.evaluate_search_quality --api-url http://127.0.0.1:8000",
            ],
        }
        report_dir = Path("data/batch_reports"); report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{batch_id}-after.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        LOGGER.info("[BATCH REPORT] %s", report_dir / f"{batch_id}-after.json")


def main() -> None:
    configure_logging()
    if len(sys.argv) > 1 and sys.argv[1] in {"add-brand", "ingest-curated", "ingest-curated-brands", "preview-brand-batch", "ingest-brand-batch", "refresh-brand", "stale-brands", "instagram-login", "instagram-session-check"}:
        command = "ingest-curated" if sys.argv[1] == "ingest-curated-brands" else sys.argv[1]; run_curated_command(command, parse_curated_args(command)); return
    args = parse_args()
    hashtags = [hashtag.strip() for hashtag in args.hashtags.split(",") if hashtag.strip()]
    if args.source == "brands":
        supabase = get_public_supabase_client() if args.dry_run else get_supabase_client()
        brands = load_brands_for_product_scraping(
            supabase,
            limit=args.brand_limit if args.brand_limit > 0 else None,
        )
        if args.brand_name:
            needle = args.brand_name.strip().lower()
            brands = [
                brand for brand in brands
                if needle in brand.brand_name.lower() or needle in brand.instagram_username.lower()
            ]

        def scrape_existing_brands(_: list[str], *, per_hashtag_limit: int = 12) -> list[ScrapedInstagramPost]:
            callback = None if args.dry_run else lambda brand, stage, error: record_brand_failure(supabase, brand, stage, error)
            return scrape_instagram_brand_profiles(brands, per_brand_limit=args.per_brand_limit, on_failure=callback)

        asyncio.run(
            run_pipeline(
                [],
                per_hashtag_limit=args.per_brand_limit,
                scrape_fn=scrape_existing_brands,
                supabase=supabase,
                dry_run=args.dry_run,
            )
        )
        if not args.dry_run:
            try:
                from backend.catalog_quality import build_catalog_report, load_catalog, print_report
                print_report(build_catalog_report(*load_catalog(supabase)))
            except Exception as error:
                LOGGER.warning("Catalog health summary unavailable: %s", error)
        return

    asyncio.run(run_pipeline(hashtags, per_hashtag_limit=args.per_hashtag_limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
