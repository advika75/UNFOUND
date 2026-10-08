from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any, Callable
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from typing import TYPE_CHECKING

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("TRANSFORMERS_NO_FLAX", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

Client = Any

from backend.supabase_compat import SupabaseRestClient, create_supabase_client
from backend.catalog_quality import build_catalog_report, load_catalog
from backend.personalization import (
    load_personalization_context,
    optional_user_id,
    personalization_match_score,
    router as personalization_router,
)
from backend.category_quality import AUTO_CATEGORY_THRESHOLD, category_map, fetch_all, product_supports_category
from backend.admin_dashboard import (
    brand_category_breakdown,
    caption_like_names,
    category_audience_breakdown,
    confidence_distribution,
    read_eval_run_history,
    read_ingestion_runs,
    search_analytics,
)
from backend.search_query_log import log_search_query, read_search_query_log
from backend.utils import (
    UploadValidationError,
    read_validated_image_upload,
    require_single_search_input,
)
from backend.product_taxonomy import (
    FAMILY_LABELS,
    TYPE_LABELS,
    attach_category_audience,
    family_match,
    family_terms,
    gender_match_score,
    identify_product,
    type_match,
    type_regex,
    type_terms,
)
from backend.discovery_engine import discovery_sections


def _load_runtime_env() -> None:
    """Load optional local configuration without making imports depend on it.

    A cloud-synced desktop can transiently leave ``.env`` online-only. Tests
    and deployments with process-level configuration must remain importable.
    """
    try:
        load_dotenv("backend/.env", override=True)
    except OSError as error:
        logging.warning("Unable to read backend/.env; using process environment: %s", error)


_load_runtime_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

CLIP_MODEL_NAME = os.getenv("CLIP_MODEL_NAME", "clip-ViT-B-32")
DISCOVER_LIMIT = 16
DISCOVER_CANDIDATE_LIMIT = int(os.getenv("DISCOVER_CANDIDATE_LIMIT", "80"))
MATCH_THRESHOLD = 0.0
RECOMMEND_PRODUCT_LIMIT = 12
RECOMMEND_BRAND_LIMIT = 6
RECOMMEND_MATCH_THRESHOLD = 0.3
EMBEDDING_DIMENSIONS = 512
SEARCH_CACHE_TTL_SECONDS = 300
# A warm Lambda container can live for hours, so both caches are bounded (LRU) as well as TTL'd.
# Ceiling: ~30 KB per search entry (20 hydrated rows) -> 256 entries ~ 8 MB; ~60 KB per discovery
# payload -> 64 entries ~ 4 MB.
SEARCH_CACHE_MAX_ENTRIES = 256
SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
# "vector" (default, unchanged behavior) or "hybrid" (fuse vector + lexical via RRF).
# Only affects text-mode search; image search always stays vector-only since the
# lexical retriever has no image query representation.
SEARCH_MODE = os.getenv("SEARCH_MODE", "vector")
# "off" (default: apply_final_ranking), "structured" (backend/search/rerank.py scores the whole
# pool instead; soft audience penalty rather than hard gender filter), or "gated"
# (apply_final_ranking decides the surviving candidates, rerank.py only re-orders them). Text search only.
RERANK = os.getenv("RERANK", "off")
HYBRID_RETRIEVER_LIMIT = 100
HYBRID_RRF_K = 60
DISCOVERY_FEED_CACHE_MAX_ENTRIES = 64
DISCOVERY_FEED_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
DISCOVERY_FEED_CACHE_TTL = int(os.getenv("DISCOVERY_FEED_CACHE_TTL", "60"))
PERSONALIZATION_WEIGHT = float(os.getenv("PERSONALIZATION_WEIGHT", "0.10"))
# Comma-separated browser origins allowed to call the API (CORS lives here, not in the Function URL config).
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]


def _cache_get(cache: dict[Any, tuple[float, Any]], key: Any, ttl: float) -> Any | None:
    entry = cache.pop(key, None)
    if entry is None:
        return None
    if time.monotonic() - entry[0] >= ttl:
        return None
    cache[key] = entry
    return entry[1]


def _cache_put(cache: dict[Any, tuple[float, Any]], key: Any, value: Any, max_entries: int) -> None:
    cache.pop(key, None)
    cache[key] = (time.monotonic(), value)
    while len(cache) > max_entries:
        del cache[next(iter(cache))]


# Final-score ties/near-ties, for both apply_final_ranking's default sort and rerank_candidates
# (backend/search/rerank.py imports these two). Ranking must not be decidable at the scale of
# floating-point noise: the ONNX vs. torch encoders agree to ~1e-6 on final_score (measured directly;
# see BASELINE.md), so two candidates whose final_score differs by less than TIE_BREAK_EPSILON are
# noise-indistinguishable and must not have a guaranteed relative order -- otherwise which one ranks
# first could flip between runtimes or hardware for no ranking-relevant reason. Candidates within
# TIE_BREAK_EPSILON of each other are instead ordered by TIE_BREAK_KEY, which is stable, always
# present, and identical across runtimes/hardware.
TIE_BREAK_EPSILON = 1e-5
TIE_BREAK_KEY = "id"


def deterministic_rank_key(product: dict[str, Any]) -> tuple[float, str]:
    """Sort key for descending final_score with a TIE_BREAK_KEY tie-break inside TIE_BREAK_EPSILON.

    Rounding final_score to TIE_BREAK_EPSILON-wide buckets (rather than comparing pairwise) is what
    makes this a total order: noise-scale differences collapse into the same bucket and are then
    resolved by TIE_BREAK_KEY alone, so sorting stays a plain, correct, transitive comparison.
    """
    score = float(product.get("final_score") or 0.0)
    return (-round(score / TIE_BREAK_EPSILON), str(product.get(TIE_BREAK_KEY) or ""))

TEXT_RANKING_WEIGHTS = {
    "semantic_similarity": float(os.getenv("TEXT_WEIGHT_SEMANTIC", "0.22")),
    "text_match": float(os.getenv("TEXT_WEIGHT_LEXICAL", "0.08")),
    "category_match": float(os.getenv("TEXT_WEIGHT_CATEGORY", "0.12")),
    "product_family_match": float(os.getenv("TEXT_WEIGHT_PRODUCT_FAMILY", "0.22")),
    "product_type_match": float(os.getenv("TEXT_WEIGHT_PRODUCT_TYPE", "0.20")),
    "colour_match": float(os.getenv("TEXT_WEIGHT_COLOUR", "0.06")),
    "style_match": float(os.getenv("TEXT_WEIGHT_STYLE", "0.04")),
    "fit_match": float(os.getenv("TEXT_WEIGHT_FIT", "0.02")),
    "gender_match": float(os.getenv("TEXT_WEIGHT_GENDER", "0.02")),
    "niche_score": float(os.getenv("TEXT_WEIGHT_NICHE", "0.02")),
}
IMAGE_RANKING_WEIGHTS = {
    "visual_similarity": float(os.getenv("IMAGE_WEIGHT_VISUAL", "0.72")),
    "category_match": float(os.getenv("IMAGE_WEIGHT_CATEGORY", "0.09")),
    "product_type_match": float(os.getenv("IMAGE_WEIGHT_PRODUCT_TYPE", "0.06")),
    "colour_match": float(os.getenv("IMAGE_WEIGHT_COLOUR", "0.03")),
    "style_match": float(os.getenv("IMAGE_WEIGHT_STYLE", "0.03")),
    "gender_match": float(os.getenv("IMAGE_WEIGHT_GENDER", "0.02")),
    "niche_score": float(os.getenv("IMAGE_WEIGHT_NICHE", "0.05")),
}

COLOURS = ("black", "white", "silver", "gold", "brown", "beige", "blue", "navy", "red", "green", "pink", "purple", "grey", "gray", "orange", "yellow")
STYLES = ("minimalist", "minimal", "stylish", "trendy", "contemporary", "streetwear", "vintage", "formal", "party", "going out", "quiet luxury", "luxury", "sporty", "classic", "boho", "ethnic", "gym", "activewear", "athletic", "workout", "sportswear")
FITS = ("oversized", "slim", "relaxed", "wide leg", "straight leg", "cropped")
def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required. Add it to backend/.env.")
    return value


_SECRET_CACHE: dict[str, str] = {}


def _resolve_secret(name: str) -> str | None:
    """`name`'s value directly if set (local dev / tests: unchanged), else resolve
    `{name}_SSM_PARAM` (an SSM parameter *path*, never a value) via boto3 SSM
    GetParameter with decryption, memoized per path so repeated calls (e.g. the
    per-request admin-key check) only hit SSM once per container."""
    direct = os.getenv(name)
    if direct:
        return direct
    ssm_path = os.getenv(f"{name}_SSM_PARAM")
    if not ssm_path:
        return None
    if ssm_path not in _SECRET_CACHE:
        import boto3

        t0 = time.monotonic()
        client = boto3.client("ssm")
        _SECRET_CACHE[ssm_path] = client.get_parameter(Name=ssm_path, WithDecryption=True)["Parameter"]["Value"]
        logging.info("Resolved %s from SSM (%s) in %.0f ms.", name, ssm_path, (time.monotonic() - t0) * 1000)
    return _SECRET_CACHE[ssm_path]


# Which encoders serve queries. "onnx" (default) is torch-free (backend/ml/onnx_*_encoder.py); "torch" loads the
# full SentenceTransformer, for local parity checks only. Text and image are configured independently.
TEXT_ENCODER = os.getenv("TEXT_ENCODER", "onnx")
IMAGE_ENCODER = os.getenv("IMAGE_ENCODER", "onnx")
_ML_ARTIFACTS = Path(__file__).resolve().parent / "ml" / "artifacts"
ONNX_TEXT_ENCODER_DIR = os.getenv("ONNX_TEXT_ENCODER_DIR", str(_ML_ARTIFACTS / "clip-vit-b-32-text"))
ONNX_IMAGE_ENCODER_DIR = os.getenv("ONNX_IMAGE_ENCODER_DIR", str(_ML_ARTIFACTS / "clip-vit-b-32-image"))
# 0 (default): load the image tower on the first image-search request. 1: load it during startup (init).
EAGER_IMAGE_ENCODER = os.getenv("EAGER_IMAGE_ENCODER", "0") == "1"
_IMAGE_ENCODER: dict[str, Any] = {}
_IMAGE_ENCODER_LOCK = threading.Lock()


def load_text_encoder() -> Any:
    if TEXT_ENCODER == "torch":
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(CLIP_MODEL_NAME)
    if TEXT_ENCODER == "onnx":
        from backend.ml.onnx_text_encoder import OnnxTextEncoder

        return OnnxTextEncoder(ONNX_TEXT_ENCODER_DIR)
    raise RuntimeError(f"TEXT_ENCODER must be 'onnx' or 'torch', got {TEXT_ENCODER!r}")


def load_image_encoder(text_encoder: Any = None) -> Any:
    if IMAGE_ENCODER == "torch":
        if TEXT_ENCODER == "torch" and text_encoder is not None:
            return text_encoder  # already the full model
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(CLIP_MODEL_NAME)
    if IMAGE_ENCODER == "onnx":
        from backend.ml.onnx_image_encoder import OnnxImageEncoder

        return OnnxImageEncoder(ONNX_IMAGE_ENCODER_DIR)
    raise RuntimeError(f"IMAGE_ENCODER must be 'onnx' or 'torch', got {IMAGE_ENCODER!r}")


def get_image_encoder(text_encoder: Any = None) -> Any:
    """The CLIP image encoder for image-upload search: loaded once, on first use (or at startup if EAGER)."""
    with _IMAGE_ENCODER_LOCK:
        if "model" not in _IMAGE_ENCODER:
            _IMAGE_ENCODER["model"] = load_image_encoder(text_encoder)
        return _IMAGE_ENCODER["model"]


def check_image_encoder_artifact() -> None:
    """Fail at startup (not on the first user upload) if the deployed image-encoder artifact is missing."""
    if IMAGE_ENCODER == "onnx":
        for name in ("model.onnx", "preprocessor_config.json", "export_metadata.json"):
            if not (Path(ONNX_IMAGE_ENCODER_DIR) / name).is_file():
                raise FileNotFoundError(
                    f"{Path(ONNX_IMAGE_ENCODER_DIR) / name} is missing. Build it with "
                    "`python -m backend.ml.export_clip_image_onnx` or set ONNX_IMAGE_ENCODER_DIR."
                )


def warm_up_search_path(
    model: Any,
    supabase: Any,
    personalization: Any = None,
    hybrid: bool = False,
) -> dict[str, float]:
    """Pay the one-time costs before the first real search.

    * the first CLIP forward pass;
    * TLS + HTTP/2 to Supabase on the shared client (later calls reuse the connection);
    * the first call of each search RPC (match_products; exact_type_products; and
      lexical_search_products when hybrid), which is measurably slower than later calls;
    * the connection on the second (personalized) client, which has its own pool.

    Fail-soft: every failure is logged and skipped, never raised, so a warm-up problem can't stop the
    app from starting. Independent steps run concurrently. Returns per-step wall times in ms (a step
    that failed is absent) plus the total.
    """
    from concurrent.futures import ThreadPoolExecutor

    timings: dict[str, float] = {}

    def step(name: str, action: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = action()
        except Exception:
            logging.exception("Warm-up step %r failed; continuing without it.", name)
            return None
        timings[f"{name}_ms"] = (time.perf_counter() - started) * 1000
        return result if result is not None else True

    def touch(client: Any) -> Any:
        return client.table("products").select("id").limit(1).execute()

    def encode_then_vector_rpc() -> None:
        embedding = step("clip_encode", lambda: encode_text(model, "warm-up"))
        if embedding is None:
            return
        step("match_products", lambda: supabase.rpc(
            "match_products",
            {"query_embedding": embedding, "match_threshold": 0.0, "match_count": 1, "filter_category_id": None},
        ).execute())

    def type_rpc() -> None:
        step("exact_type_products", lambda: supabase.rpc(
            "exact_type_products", {"type_pattern": type_regex("dress"), "exclude_ids": [], "row_limit": 1},
        ).execute())

    def lexical_rpc() -> None:
        step("lexical_search_products", lambda: supabase.rpc(
            "lexical_search_products",
            {"query_text": "warm-up", "filter_category_id": None, "match_count": 1, "fallback_threshold": 5},
        ).execute())

    jobs: list[Callable[[], None]] = [
        encode_then_vector_rpc,
        lambda: step("supabase_connect", lambda: touch(supabase)),
        type_rpc,
    ]
    if hybrid:
        jobs.append(lexical_rpc)
    if personalization is not None:
        jobs.append(lambda: step("personalization_connect", lambda: touch(personalization)))

    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            list(pool.map(lambda job: job(), jobs))
    except Exception:
        logging.exception("Search-path warm-up could not run; continuing without it.")
    timings["total_ms"] = (time.perf_counter() - started) * 1000
    return timings


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.clip_model = load_text_encoder()
    check_image_encoder_artifact()
    if EAGER_IMAGE_ENCODER:
        get_image_encoder(app.state.clip_model)
    app.state.supabase = create_supabase_client(
        _get_required_env("SUPABASE_URL"),
        os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"),
    )
    try:
        service_key = _resolve_secret("SUPABASE_SERVICE_ROLE_KEY")
    except Exception:
        logging.exception("Could not resolve SUPABASE_SERVICE_ROLE_KEY; personalization stays unavailable.")
        service_key = None
    app.state.personalization = (
        create_supabase_client(_get_required_env("SUPABASE_URL"), service_key)
        if service_key else None
    )
    logging.info("Loaded %s and initialized Supabase client.", CLIP_MODEL_NAME)
    try:
        timings = warm_up_search_path(
            app.state.clip_model, app.state.supabase, app.state.personalization, hybrid=SEARCH_MODE == "hybrid",
        )
        logging.info("Search-path warm-up finished: %s", {k: round(v) for k, v in timings.items()})
    except Exception:
        logging.exception("Search-path warm-up failed; starting without it.")
    yield


app = FastAPI(
    title="AI Fashion Discovery & Recommendation Platform",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(personalization_router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "unfound-api"}


def vector_to_pgvector(values: list[float]) -> list[float]:
    if len(values) != EMBEDDING_DIMENSIONS:
        raise HTTPException(
            status_code=500,
            detail=(
                f"CLIP model returned {len(values)} dimensions; "
                f"expected {EMBEDDING_DIMENSIONS}."
            ),
        )
    return [float(value) for value in values]


def encode_image(model: SentenceTransformer, image_bytes: bytes) -> list[float]:
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail="The uploaded image could not be parsed. Upload a valid PNG or JPEG.",
        ) from error

    embedding = model.encode(image, normalize_embeddings=True)
    return vector_to_pgvector(embedding.tolist())


def encode_text(model: SentenceTransformer, text_query: str) -> list[float]:
    cleaned_query = text_query.strip()
    if not cleaned_query:
        raise HTTPException(status_code=400, detail="text_query cannot be empty.")

    embedding = model.encode(cleaned_query, normalize_embeddings=True)
    return vector_to_pgvector(embedding.tolist())


def format_product(row: dict[str, Any]) -> dict[str, Any]:
    similarity = row.get("similarity", row.get("match_score", 0.0))
    category_id = row.get("category_id")
    category = row.get("category") or row.get("category_name")
    if not category and category_id is not None:
        category = f"Category {category_id}"
    product_name = row.get("product_name") or row.get("item_name") or row.get("name") or "Untitled item"
    similarity_score = float(similarity or 0.0)
    return {
        "id": row.get("id"),
        "brand_id": row.get("brand_id"),
        "brand_name": row.get("brand_name") or row.get("brand") or "Unknown brand",
        "product_name": product_name,
        "item_name": product_name,
        "description": row.get("description") or "",
        "image_url": row.get("image_url") or row.get("thumbnail_url") or row.get("photo_url") or row.get("cover_url") or "",
        "product_url": row.get("product_url") or row.get("source_url") or "",
        "price": float(row["price"]) if row.get("price") is not None else None,
        "source": row.get("source") or "",
        "niche_score": float(row["niche_score"]) if row.get("niche_score") is not None else None,
        "likes_count": int(row["likes_count"]) if row.get("likes_count") is not None else 0,
        "comments_count": int(row["comments_count"]) if row.get("comments_count") is not None else 0,
        "source_hashtag": row.get("source_hashtag") or "",
        "scraped_at": row.get("scraped_at") or row.get("created_at") or "",
        "category_id": category_id,
        "category": category or "",
        "category_audience": row.get("category_audience") or "",
        "classifier_confidence": row.get("classifier_confidence"),
        "audience": row.get("audience") or row.get("gender") or "",
        "subcategory": row.get("normalized_subcategory") or row.get("subcategory") or "",
        "normalized_main_category": row.get("normalized_main_category") or "",
        "normalized_subcategory": row.get("normalized_subcategory") or "",
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
        "currency": row.get("currency") or "INR",
        "similarity": similarity_score,
        "similarity_score": similarity_score,
        "final_score": float(row.get("final_score") or similarity_score),
        "brand_follower_count": int(row["brand_follower_count"])
        if row.get("brand_follower_count") is not None
        else None,
        "brand_niche_score": float(row["brand_niche_score"])
        if row.get("brand_niche_score") is not None
        else None,
        "brand_profile_picture_url": row.get("brand_profile_picture_url") or "",
        "brand_instagram_profile_url": row.get("brand_instagram_profile_url")
        or row.get("brand_profile_url")
        or "",
    }


def format_brand(row: dict[str, Any]) -> dict[str, Any]:
    name = row.get("name") or row.get("brand_name") or row.get("instagram_username") or "Unknown brand"
    instagram_profile_url = row.get("instagram_profile_url") or row.get("profile_url") or ""
    image_url = row.get("image_url") or row.get("profile_picture_url") or row.get("profile_image_url") or row.get("logo_url") or ""
    return {
        "id": row.get("id"),
        "name": name,
        "brand_name": name,
        "instagram_username": row.get("instagram_username") or "",
        "category": row.get("category") or "Fashion",
        "followers": int(row["followers"]) if row.get("followers") is not None else 0,
        "niche_score": float(row["niche_score"]) if row.get("niche_score") is not None else 0.0,
        "image_url": image_url,
        "profile_picture_url": image_url,
        "instagram_profile_url": instagram_profile_url,
        "profile_url": instagram_profile_url,
        "post_url": row.get("post_url") or "",
    }


def format_catalog_products(
    supabase: Client | SupabaseRestClient,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        names_by_id = {
            category.get("id"): category.get("name")
            for category in category_map(supabase).values()
        }
    except Exception:
        logging.exception("Category-name enrichment failed; using stored product labels.")
        names_by_id = {}
    return [
        format_product({**row, "category_name": names_by_id.get(row.get("category_id"))})
        for row in rows
    ]


def product_matches_filters(
    product: dict[str, Any],
    *,
    min_price: float | None,
    max_price: float | None,
    min_niche_score: float | None,
    min_followers: int | None,
    brand: str | None,
    gender: str | None,
) -> bool:
    price = product.get("price")
    niche_score = product.get("niche_score")
    brand_name = (product.get("brand_name") or "").strip().lower()
    followers = product.get("brand_follower_count")
    audience = " ".join(str(product.get(field) or "") for field in (
        "audience", "category", "normalized_main_category", "normalized_subcategory"
    )).lower()

    if min_price is not None and (price is None or float(price) < min_price):
        return False
    if max_price is not None and (price is None or float(price) > max_price):
        return False
    if min_niche_score is not None and (niche_score is None or float(niche_score) < min_niche_score):
        return False
    if min_followers is not None and (followers is None or int(followers) < min_followers):
        return False
    if brand and brand.strip().lower() not in brand_name:
        return False
    if gender and gender.strip().lower() not in audience:
        return False
    return True


def apply_product_filters(
    products: list[dict[str, Any]],
    *,
    min_price: float | None,
    max_price: float | None,
    min_niche_score: float | None,
    min_followers: int | None,
    brand: str | None,
    gender: str | None,
) -> list[dict[str, Any]]:
    return [
        product
        for product in products
        if product_matches_filters(
            product,
            min_price=min_price,
            max_price=max_price,
            min_niche_score=min_niche_score,
            min_followers=min_followers,
            brand=brand,
            gender=gender,
        )
    ]


def normalize_optional_int(value: Any) -> int | None:
    if value is None or not isinstance(value, (float, int, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return int(value)


def normalize_optional_float(value: Any) -> float | None:
    if value is None or not isinstance(value, (float, int, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return float(value)


def normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_embedding_value(value: Any) -> list[float]:
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, str):
        cleaned = value.strip().strip("[]")
        if not cleaned:
            return []
        return [float(item) for item in cleaned.split(",")]
    return []


def product_engagement_score(product: dict[str, Any]) -> float:
    likes = int(product.get("likes_count") or 0)
    comments = int(product.get("comments_count") or 0)
    engagement = likes + (comments * 3)
    return min(1.0, engagement / 10_000)


def extract_query_attributes(query: str) -> dict[str, Any]:
    text = query.strip().lower()
    product_family, product_type = identify_product(text)
    price_match = re.search(r"(?:under|below|less than|up to)\s*(?:₹|rs\.?|inr)?\s*([\d,]+)", text)
    min_price_match = re.search(r"(?:over|above|more than|from)\s*(?:₹|rs\.?|inr)?\s*([\d,]+)", text)
    occasion = next((term for term in ("college party", "date night", "festive", "wedding", "party", "college", "work") if term in text), None)
    material = next((term for term in ("linen", "cotton", "silk", "denim", "leather", "satin", "wool") if re.search(rf"\b{term}\b", text)), None)
    return {
        "product_type": product_type,
        "product_family": product_family,
        "colours": [colour for colour in COLOURS if re.search(rf"\b{re.escape(colour)}\b", text)],
        "styles": [style for style in STYLES if re.search(rf"\b{re.escape(style)}\b", text)],
        "fits": [fit for fit in FITS if fit in text],
        "gender": next((gender for gender in ("women", "men", "unisex") if re.search(rf"\b{gender}(?:'s)?\b", text)), None),
        "max_price": float(price_match.group(1).replace(",", "")) if price_match else None,
        "min_price": float(min_price_match.group(1).replace(",", "")) if min_price_match else None,
        "occasion": occasion,
        "material": material,
        "aesthetic_terms": [term for term in ("quiet luxury", "minimal", "minimalist", "trendy", "stylish", "streetwear", "contemporary", "cute") if term in text],
        "query_tokens": [token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 2 and token not in {"under", "below", "with", "from", "this", "that"}],
    }


def product_search_text(product: dict[str, Any]) -> str:
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else {}
    return " ".join(str(value or "") for value in (
        product.get("product_name"), product.get("item_name"), product.get("description"),
        product.get("category"), product.get("subcategory"), product.get("normalized_main_category"),
        product.get("normalized_subcategory"), product.get("audience"), metadata.get("colour"),
        metadata.get("color"), metadata.get("style"), metadata.get("fit"),
    )).lower()


def phrase_match_score(haystack: str, phrases: list[str]) -> float:
    if not phrases:
        return 0.5
    return sum(1 for phrase in phrases if phrase in haystack) / len(phrases)


def product_type_score(haystack: str, product_type: str | None) -> float:
    if not product_type:
        return 0.5
    return 1.0 if type_match(haystack, product_type) else 0.0


def product_family_score(haystack: str, product_family: str | None) -> float:
    if not product_family:
        return 0.5
    return 1.0 if family_match(haystack, product_family) else 0.0


def category_match_score(product: dict[str, Any], attributes: dict[str, Any], category_id: int | None) -> float:
    if category_id is not None:
        return 1.0 if product.get("category_id") == category_id else 0.0
    product_family = attributes.get("product_family")
    if not product_family:
        return 0.5
    haystack = " ".join(str(product.get(field) or "") for field in (
        "category", "subcategory", "normalized_main_category", "normalized_subcategory"
    )).lower()
    category_terms = {
        "tops": ("top", "blouse"), "ethnic-upperwear": ("ethnic", "kurta", "kurti", "tunic"),
        "shirts": ("shirt", "top"), "bottoms": ("bottom", "pants", "trouser", "jeans", "skirt", "shorts"),
        "dresses": ("dress", "gown"), "sets": ("co-ord", "coord", "set"), "outerwear": ("jacket", "outerwear"),
        "jewellery": ("jewellery", "jewelry", "accessories"), "bags": ("bag", "accessories"),
        "footwear": ("footwear", "shoe", "sneaker"), "watches": ("watch", "accessories"),
    }.get(product_family, family_terms(product_family))
    return 1.0 if any(term in haystack for term in category_terms) else 0.0


def apply_final_ranking(
    products: list[dict[str, Any]],
    *,
    category_id: int | None,
    sort_by: str | None,
    search_mode: str = "text",
    query_attributes: dict[str, Any] | None = None,
    personalization_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    attributes = query_attributes or {}
    weights = IMAGE_RANKING_WEIGHTS if search_mode == "image" else TEXT_RANKING_WEIGHTS
    scored: list[dict[str, Any]] = []
    for product in products:
        clip_score = float(product.get("similarity_score") or product.get("similarity") or 0.0)
        haystack = product_search_text(product)
        tokens = attributes.get("query_tokens") or []
        lexical_score = sum(1 for token in tokens if token in haystack) / max(len(tokens), 1) if tokens else 0.5
        signals = {
            "category_match": category_match_score(product, attributes, category_id),
            "product_family_match": product_family_score(haystack, attributes.get("product_family")),
            "product_type_match": product_type_score(haystack, attributes.get("product_type")),
            "colour_match": phrase_match_score(haystack, attributes.get("colours") or []),
            "style_match": phrase_match_score(haystack, (attributes.get("styles") or []) + (attributes.get("aesthetic_terms") or [])),
            "fit_match": phrase_match_score(haystack, attributes.get("fits") or []),
            "gender_match": gender_match_score(product, attributes.get("gender")),
            "niche_score": max(0.0, min(1.0, float(product.get("niche_score") or 0.0))),
        }
        if search_mode == "image":
            signals["visual_similarity"] = clip_score
        else:
            signals["semantic_similarity"] = clip_score
            signals["text_match"] = lexical_score
        final_score = sum(weights.get(signal, 0.0) * value for signal, value in signals.items())
        if personalization_context:
            signals["personalization_match"] = personalization_match_score(product, personalization_context)
            final_score = ((1.0 - PERSONALIZATION_WEIGHT) * final_score) + (
                PERSONALIZATION_WEIGHT * signals["personalization_match"]
            )
        scored.append({**product, "final_score": final_score, "score_breakdown": signals})

    # An explicit product request should not degrade into unrelated fashion
    # merely because those items have the nearest available CLIP vectors.
    # Unconditional on purpose (no "leaves something" fallback like the type
    # filter below) -- this is the only thing that stops a single, totally
    # wrong-family candidate from surviving via the type filter's own
    # fallback (a mismatched item always fails type_match too, which would
    # otherwise trivially satisfy type filter's "only narrow if non-empty"
    # escape hatch). See test_explicit_product_type_does_not_return_unrelated_fallbacks.
    if search_mode == "text" and attributes.get("product_family"):
        scored = [
            product for product in scored
            if float(product.get("score_breakdown", {}).get("product_family_match") or 0.0) > 0.0
        ]
    if search_mode == "text" and attributes.get("product_type"):
        # Product type is an explicit user constraint, not a soft discovery
        # preference. Returning fewer results is preferable to contaminating
        # a kurta/top/jewellery query with a semantically nearby product.
        # But a compound type like "party-dress" only matches the literal phrase
        # "party dress" -- if that exact wording never appears in scraped captions,
        # this would zero out every candidate even though the broader family
        # (dresses) has real matches. Only narrow to type if that leaves something.
        type_filtered = [
            product for product in scored
            if float(product.get("score_breakdown", {}).get("product_type_match") or 0.0) > 0.0
        ]
        if type_filtered:
            scored = type_filtered
    if search_mode == "text" and attributes.get("gender"):
        gender_filtered = [
            product for product in scored
            if float(product.get("score_breakdown", {}).get("gender_match") or 0.0) > 0.0
        ]
        if gender_filtered:
            scored = gender_filtered

    if sort_by == "newest":
        return sorted(scored, key=lambda product: product.get("scraped_at") or "", reverse=True)
    if sort_by == "popular":
        return sorted(scored, key=lambda product: product_engagement_score(product), reverse=True)
    if sort_by == "price_asc":
        return sorted(scored, key=lambda product: (product.get("price") is None, float(product.get("price") or 0)))
    if sort_by == "price_desc":
        return sorted(scored, key=lambda product: float(product.get("price") or -1), reverse=True)
    if sort_by == "niche_score":
        return sorted(scored, key=lambda product: float(product.get("niche_score") or 0), reverse=True)
    return sorted(scored, key=deterministic_rank_key)


# The only raw `products` columns hydration can ever change in the formatted output. The merge in
# enrich_product_candidates is {**raw_row, **rpc_product}, so the RPC-shaped product wins on every key
# it carries; the raw row matters only through format_product's `or`-fallbacks to keys the RPC row
# lacks AND that are real table columns: scraped_at <- created_at, product_url <- source_url.
# (Verified across all catalog rows: no other output field ever differs with vs without a full row.)
# Fetching just these avoids pulling ~165 KB per search -- 75% of it the embedding vector.
HYDRATION_COLUMNS = ("id", "created_at", "source_url")


def enrich_product_candidates(
    supabase: Client | SupabaseRestClient,
    products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = [product.get("id") for product in products if product.get("id")]
    if not ids:
        return products
    try:
        response = supabase.table("products").select(",".join(HYDRATION_COLUMNS)).in_("id", ids).execute()
    except Exception:
        logging.exception("Candidate metadata enrichment failed; ranking raw RPC rows.")
        return products
    metadata_by_id = {str(row.get("id")): row for row in response.data or []}
    enriched = []
    for product in products:
        metadata = metadata_by_id.get(str(product.get("id")), {})
        enriched.append(format_product({**metadata, **product}))
    return enriched


def fetch_hybrid_candidates(
    supabase: Client | SupabaseRestClient,
    query_embedding: list[float],
    query_text: str,
    category_id: int | None,
    *,
    retriever_limit: int = HYBRID_RETRIEVER_LIMIT,
    rrf_k: int = HYBRID_RRF_K,
    rrf_weights: list[float] | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_niche_score: float | None = None,
    min_followers: int | None = None,
    brand: str | None = None,
    gender: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch top-N from vector and lexical retrievers independently (each
    pre-filtered internally -- see backend/search/vector.py's docstring for
    why filtering must happen before fusion, not after), then fuse via RRF.

    Each candidate's native similarity score (cosine similarity from vector,
    ts_rank_cd/word_similarity from lexical) is NOT carried into the merged
    result -- those scales aren't comparable (ts_rank_cd routinely exceeds
    1.0, unlike cosine similarity), which is exactly the problem RRF avoids
    by working on rank position alone. Instead each merged candidate's
    "similarity"/"final_score" seed is replaced with its RRF score, min-max
    normalized to 0..1 within this result set so it composes correctly with
    apply_final_ranking's existing signal weights, which assume a
    similarity-shaped input.
    """
    from backend.search.fusion import rrf_fuse
    from backend.search.lexical import lexical_search
    from backend.search.vector import vector_retrieve

    filter_kwargs = dict(
        min_price=min_price,
        max_price=max_price,
        min_niche_score=min_niche_score,
        min_followers=min_followers,
        brand=brand,
        gender=gender,
    )
    # The two retrievals are independent, so run them concurrently on the shared client (wall time
    # becomes max(vector, lexical) instead of the sum). Fusion still receives exactly the same two
    # lists. Failure behavior is unchanged from the sequential version, which had no error handling:
    # any RPC error propagates and fails the search -- the vector error if vector failed (as before,
    # it was raised first), otherwise the lexical error. Both calls are always allowed to finish
    # before anything is raised, so no request is left running in the background.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        vector_future = pool.submit(
            vector_retrieve, supabase, query_embedding, category_id=category_id, limit=retriever_limit, **filter_kwargs,
        )
        lexical_future = pool.submit(
            lexical_search, supabase, query_text, category_id=category_id, limit=retriever_limit, **filter_kwargs,
        )
    vector_products = vector_future.result()
    lexical_products = lexical_future.result()

    by_id: dict[str, dict[str, Any]] = {}
    for product in vector_products + lexical_products:
        by_id.setdefault(str(product["id"]), product)

    vector_ids = [str(product["id"]) for product in vector_products]
    lexical_ids = [str(product["id"]) for product in lexical_products]
    fused = rrf_fuse([vector_ids, lexical_ids], k=rrf_k, weights=rrf_weights)

    scores = [score for _, score in fused]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 0.0)
    span = hi - lo

    merged: list[dict[str, Any]] = []
    for product_id, score in fused:
        product = dict(by_id[product_id])
        normalized = (score - lo) / span if span > 0 else 1.0
        product["similarity"] = normalized
        product["similarity_score"] = normalized
        merged.append(product)
    return merged


def run_match_products_rpc(
    supabase: Client | SupabaseRestClient,
    query_embedding: list[float],
    category_id: int | None,
    *,
    match_threshold: float = MATCH_THRESHOLD,
    match_count: int = DISCOVER_LIMIT,
    min_price: float | None = None,
    max_price: float | None = None,
    min_niche_score: float | None = None,
    min_followers: int | None = None,
    brand: str | None = None,
    gender: str | None = None,
    sort_by: str | None = None,
    search_mode: str = "text",
    query_attributes: dict[str, Any] | None = None,
    result_limit: int = DISCOVER_LIMIT,
    personalization_context: dict[str, Any] | None = None,
    retrieval_mode: str = "vector",
    query_text: str | None = None,
    rrf_weights: list[float] | None = None,
    rerank_mode: str = "off",
    cache_stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cache_key = (
        tuple(round(value, 6) for value in query_embedding[:16]),
        category_id,
        match_threshold,
        match_count,
        min_price,
        max_price,
        min_niche_score,
        min_followers,
        (brand or "").lower() if brand else None,
        (gender or "").lower() if gender else None,
        sort_by,
        search_mode,
        repr(sorted((query_attributes or {}).items(), key=lambda item: item[0])),
        result_limit,
        repr(personalization_context),
        retrieval_mode,
        query_text,
        tuple(rrf_weights) if rrf_weights else None,
        rerank_mode,
    )
    cached = _cache_get(SEARCH_CACHE, cache_key, SEARCH_CACHE_TTL_SECONDS)
    if cached is not None:
        if cache_stats is not None:
            cache_stats["hit"] = True
        return cached
    if cache_stats is not None:
        cache_stats["hit"] = False

    use_hybrid = retrieval_mode == "hybrid" and search_mode == "text" and query_text

    if use_hybrid:
        products = fetch_hybrid_candidates(
            supabase,
            query_embedding,
            query_text,
            category_id,
            retriever_limit=HYBRID_RETRIEVER_LIMIT,
            rrf_weights=rrf_weights,
            min_price=min_price,
            max_price=max_price,
            min_niche_score=min_niche_score,
            min_followers=min_followers,
            brand=brand,
            gender=gender,
        )
    else:
        payload: dict[str, Any] = {
            "query_embedding": query_embedding,
            "match_threshold": match_threshold,
            "match_count": match_count,
            "filter_category_id": category_id,
        }

        try:
            response = supabase.rpc("match_products", payload).execute()
        except Exception as error:
            logging.exception("Supabase match_products RPC failed.")
            raise HTTPException(
                status_code=502,
                detail="Product discovery is temporarily unavailable.",
            ) from error

        products = enrich_product_candidates(
            supabase,
            [format_product(row) for row in response.data or []],
        )
    # Vector top-K can miss a rare but lexically explicit type. Add bounded
    # exact catalog matches before ranking so a real corset/kurta is never
    # hidden simply because its CLIP vector fell outside the semantic window.
    requested_type = (query_attributes or {}).get("product_type")
    type_pattern = type_regex(requested_type) if requested_type else ""
    if search_mode == "text" and type_pattern:
        try:
            # Filtered in the database (public.exact_type_products): same word-boundary text predicate
            # as type_match(product_search_text(row), type), at most 100 rows, no embedding column --
            # instead of downloading the whole products table on every search.
            existing_ids = sorted({str(row["id"]) for row in products if row.get("id")})
            response = supabase.rpc(
                "exact_type_products",
                {"type_pattern": type_pattern, "exclude_ids": existing_ids, "row_limit": 100},
            ).execute()
            products.extend(format_product(row) for row in response.data or [])
        except Exception:
            logging.exception("Exact-type catalog augmentation failed; continuing with vector candidates.")
    ranking_category_id = category_id
    if search_mode == "image" and ranking_category_id is None:
        leading = products[:10]
        category_counts: dict[Any, int] = {}
        for product in leading:
            candidate_category = product.get("category_id")
            if candidate_category is not None:
                category_counts[candidate_category] = category_counts.get(candidate_category, 0) + 1
        if category_counts:
            dominant_category, count = max(category_counts.items(), key=lambda item: item[1])
            if count >= max(3, len(leading) // 2):
                ranking_category_id = dominant_category
    rerank_active = rerank_mode in ("structured", "gated") and search_mode == "text" and not sort_by and not personalization_context
    use_structured_rerank = rerank_active and rerank_mode == "structured"
    use_gated_rerank = rerank_active and rerank_mode == "gated"
    filtered = apply_product_filters(
        products,
        min_price=min_price,
        max_price=max_price,
        min_niche_score=min_niche_score,
        min_followers=min_followers,
        brand=brand,
        gender=None if use_structured_rerank else gender,
    )
    if use_structured_rerank:
        from backend.search.rerank import rerank_candidates

        attributes = dict(query_attributes or {})
        if gender:
            attributes["gender"] = gender
        # RPC rows lack classifier_confidence/metadata; one bulk lookup on the pool restores them.
        ranked = rerank_candidates(enrich_product_candidates(supabase, filtered), attributes)[:result_limit]
    else:
        survivors = apply_final_ranking(
            filtered,
            category_id=ranking_category_id,
            sort_by=sort_by,
            search_mode=search_mode,
            query_attributes=query_attributes,
            personalization_context=personalization_context,
        )
        if use_gated_rerank:
            # Gated: apply_final_ranking's hard type/family/gender gates (with their fallbacks) decide
            # WHICH candidates survive, exactly as with rerank off. rerank_candidates only re-orders
            # that survivor set, before the top-K cut. No enrichment round trip is added: signals
            # missing on un-hydrated (hybrid) candidates simply contribute 0.
            from backend.search.rerank import rerank_candidates

            attributes = dict(query_attributes or {})
            if gender:
                attributes["gender"] = gender
            survivors = rerank_candidates(survivors, attributes)
        ranked = survivors[:result_limit]
    _cache_put(SEARCH_CACHE, cache_key, ranked, SEARCH_CACHE_MAX_ENTRIES)
    return ranked


def build_similar_brands(
    products: list[dict[str, Any]],
    *,
    limit: int = RECOMMEND_BRAND_LIMIT,
) -> list[dict[str, Any]]:
    brands: dict[str, dict[str, Any]] = {}

    for product in products:
        brand_name = (product.get("brand_name") or "").strip()
        if not brand_name or brand_name.lower() == "unknown brand":
            continue

        similarity = float(product.get("similarity") or 0.0)
        niche_score = product.get("niche_score")
        current = brands.get(brand_name)
        if current is None:
            brands[brand_name] = {
                "brand_id": product.get("brand_id"),
                "brand_name": brand_name,
                "follower_count": product.get("brand_follower_count"),
                "followers": product.get("brand_follower_count"),
                "profile_picture_url": product.get("brand_profile_picture_url") or "",
                "instagram_profile_url": product.get("brand_instagram_profile_url") or "",
                "similarity": similarity,
                "niche_score": niche_score,
                "brand_niche_score": product.get("brand_niche_score") or niche_score,
                "matched_product_count": 1,
                "representative_product": {
                    "id": product.get("id"),
                    "product_name": product.get("product_name"),
                    "image_url": product.get("image_url"),
                    "product_url": product.get("product_url"),
                },
            }
            continue

        current["matched_product_count"] += 1
        if similarity > float(current["similarity"] or 0.0):
            current["similarity"] = similarity
            current["brand_id"] = product.get("brand_id") or current.get("brand_id")
            current["follower_count"] = product.get("brand_follower_count") or current.get("follower_count")
            current["followers"] = product.get("brand_follower_count") or current.get("followers")
            current["profile_picture_url"] = product.get("brand_profile_picture_url") or current.get("profile_picture_url")
            current["instagram_profile_url"] = product.get("brand_instagram_profile_url") or current.get("instagram_profile_url")
            current["niche_score"] = niche_score
            current["brand_niche_score"] = product.get("brand_niche_score") or niche_score
            current["representative_product"] = {
                "id": product.get("id"),
                "product_name": product.get("product_name"),
                "image_url": product.get("image_url"),
                "product_url": product.get("product_url"),
            }

    return sorted(
        brands.values(),
        key=lambda brand: (
            float(brand.get("similarity") or 0.0),
            float(brand.get("niche_score") or 0.0),
            int(brand.get("matched_product_count") or 0),
        ),
        reverse=True,
    )[:limit]


def split_search_groups(
    products: list[dict[str, Any]], attributes: dict[str, Any], *, exact_limit: int = 8
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep exact type matches separate from same-family alternatives."""
    requested_type = attributes.get("product_type")
    if not requested_type:
        return products[:exact_limit], products[exact_limit:]
    exact = [row for row in products if type_match(product_search_text(row), requested_type)]
    alternatives = [row for row in products if row not in exact]
    return exact[:exact_limit], exact[exact_limit:] + alternatives


def public_search_products(products: list[dict[str, Any]], *, debug: bool) -> list[dict[str, Any]]:
    if debug and os.getenv("ENVIRONMENT", "development").lower() != "production":
        return products
    return [{key:value for key,value in row.items() if key not in ("score_breakdown", "rerank_detail")} for row in products]


def _usable_image(product: dict[str, Any]) -> bool:
    url = str(product.get("image_url") or "")
    status = str(product.get("image_status") or "").upper()
    return bool(url.startswith(("https://", "http://"))) and "unsplash.com" not in url and status not in {
        "BROKEN", "EXPIRED_OR_FORBIDDEN", "INVALID_URL", "RECOVERY_REQUIRED", "UNRECOVERABLE", "MISSING"
    }


def _stable_product_image(product: dict[str, Any]) -> bool:
    base_url = str(os.getenv("SUPABASE_URL") or "").rstrip("/")
    return _usable_image(product) and bool(base_url) and str(product.get("image_url") or "").startswith(f"{base_url}/storage/v1/object/public/product-images/")


def build_catalog_scale_report(brands: list[dict[str, Any]], products: list[dict[str, Any]]) -> dict[str, Any]:
    """Return operational scale/readiness metrics without mutating the catalog."""
    active = [row for row in products if row.get("catalog_status") != "NON_PRODUCT"]
    stable = [row for row in active if _usable_image(row) and "/storage/v1/object/public/" in str(row.get("image_url") or "")]
    valid_embeddings = []
    typed = []
    families: dict[str, int] = {}
    categories: dict[str, int] = {}
    prices = {"under_1000": 0, "1000_2500": 0, "2500_5000": 0, "over_5000": 0, "missing": 0}
    brand_sizes = {"micro": 0, "small": 0, "medium": 0, "larger_independent": 0, "unknown": 0}
    for row in active:
        try:
            vector = normalize_embedding_value(row.get("embedding"))
            if len(vector) == EMBEDDING_DIMENSIONS and all(float("-inf") < item < float("inf") for item in vector) and sum(item * item for item in vector) > 0:
                valid_embeddings.append(row)
        except (TypeError, ValueError):
            pass
        family, product_type = identify_product(product_search_text(row))
        if family and product_type:
            typed.append(row); families[family] = families.get(family, 0) + 1
        category = str(row.get("category_slug") or row.get("category") or "uncategorized")
        categories[category] = categories.get(category, 0) + 1
        try: price = float(row.get("price") or 0)
        except (TypeError, ValueError): price = 0
        bucket = "missing" if price <= 0 else "under_1000" if price < 1000 else "1000_2500" if price <= 2500 else "2500_5000" if price <= 5000 else "over_5000"
        prices[bucket] += 1
    for brand in brands:
        try: followers = int(brand.get("followers") or brand.get("follower_count") or 0)
        except (TypeError, ValueError): followers = 0
        size = "unknown" if followers <= 0 else "micro" if followers < 10_000 else "small" if followers < 50_000 else "medium" if followers < 250_000 else "larger_independent"
        brand_sizes[size] += 1
    denominator = max(1, len(active))
    milestones = {
        "brands": {str(target): {"reached": len(brands) >= target, "remaining": max(0, target - len(brands))} for target in (150, 200, 250)},
        "products": {str(target): {"reached": len(active) >= target, "remaining": max(0, target - len(active))} for target in (2000, 3000, 5000)},
    }
    weak_families = sorted(({"family": key, "products": value} for key, value in families.items() if value < 20), key=lambda item: item["products"])
    trusted_types: dict[str, int] = {}
    for row in stable:
        _, product_type = identify_product(product_search_text(row))
        if product_type and float(row.get("classifier_confidence") or 0) >= AUTO_CATEGORY_THRESHOLD:
            trusted_types[product_type] = trusted_types.get(product_type, 0) + 1
    return {
        "brands": len(brands), "products": len(active), "milestones": milestones,
        "coverage": {"stable_images": len(stable), "stable_image_rate": round(len(stable) / denominator, 4), "valid_embeddings": len(valid_embeddings), "valid_embedding_rate": round(len(valid_embeddings) / denominator, 4), "classified_type": len(typed), "classified_type_rate": round(len(typed) / denominator, 4), "categorized": sum(value for key, value in categories.items() if key != "uncategorized"), "category_rate": round(sum(value for key, value in categories.items() if key != "uncategorized") / denominator, 4)},
        "inventory": {"categories": categories, "families": families, "price_brackets": prices, "brand_size_groups": brand_sizes, "weak_families": weak_families},
        "training_readiness": {"usable_stable_examples": sum(trusted_types.values()), "product_type_counts": trusted_types, "class_status": {key: "SUFFICIENT" if value >= 100 else "DEVELOPING" if value >= 50 else "WEAK" for key, value in trusted_types.items()}},
    }


def require_admin_key(provided: str | None) -> None:
    try:
        expected = _resolve_secret("ADMIN_API_KEY")
    except Exception:
        logging.exception("Could not resolve ADMIN_API_KEY.")
        expected = None
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured. Administrative API access is disabled.")
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="Invalid admin key.")


def representative_type_tiles(products: list[dict[str, Any]], allowed_families: set[str] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        family, product_type = identify_product(product_search_text(product))
        if not family or not product_type or (allowed_families and family not in allowed_families):
            continue
        grouped.setdefault(product_type, []).append(product)
    tiles = []
    for product_type, rows in grouped.items():
        image_rows = [row for row in rows if _stable_product_image(row)]
        image_rows.sort(key=lambda row: (
            float(row.get("classifier_confidence") or 0),
            float(row.get("niche_score") or 0),
            product_engagement_score(row),
            str(row.get("scraped_at") or ""),
        ), reverse=True)
        representative = image_rows[0] if image_rows else None
        tiles.append({
            "name": TYPE_LABELS.get(product_type, product_type.replace("-", " ").title()),
            "slug": product_type,
            "image_url": representative.get("image_url") if representative else None,
            "product_count": len(rows),
            "terms": list(type_terms(product_type)),
        })
    return sorted(tiles, key=lambda tile: (-tile["product_count"], tile["name"]))


@app.get("/api/brands")
async def list_brands(limit: int = 48, offset: int = 0, query: str | None = None):
    supabase: Client | SupabaseRestClient = app.state.supabase
    try:
        response = supabase.table("brands").select("*").execute()
    except Exception as error:
        logging.exception("Supabase brands select failed.")
        raise HTTPException(status_code=502, detail="Brands are temporarily unavailable.") from error

    # brands.category is a raw, unvalidated snapshot of whatever product was
    # scraped most recently for that brand (see training/scraper_pipeline.py
    # upsert_brand) -- it does not reflect what the brand actually, validly
    # sells. Override it with the brand_categories primary mapping, which is
    # only populated from products that pass product_supports_category.
    try:
        primary_mappings = supabase.table("brand_categories").select("brand_id,category_id").eq("is_primary", True).execute().data or []
        category_name_by_id = {row.get("id"): row.get("name") for row in fetch_all(supabase, "categories")}
        validated_category_by_brand = {
            str(row["brand_id"]): category_name_by_id[row["category_id"]]
            for row in primary_mappings
            if row.get("category_id") in category_name_by_id
        }
    except Exception:
        logging.exception("Validated brand-category lookup failed; falling back to raw brand.category.")
        validated_category_by_brand = {}

    brands = [format_brand(row) for row in response.data or []]
    for brand in brands:
        validated_category = validated_category_by_brand.get(str(brand.get("id")))
        if validated_category:
            brand["category"] = validated_category
    brands.sort(key=lambda brand: (float(brand.get("niche_score") or 0.0), int(brand.get("followers") or 0)), reverse=True)
    if query and query.strip():
        needle = query.strip().lower()
        brands = [brand for brand in brands if needle in f"{brand['name']} {brand['instagram_username']} {brand['category']}".lower()]
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)
    return {
        "brands": brands[safe_offset:safe_offset + safe_limit],
        "total": len(brands),
        "limit": safe_limit,
        "offset": safe_offset,
    }


@app.get("/api/products")
async def list_products(limit: int = 64, offset: int = 0):
    """Return a bounded real-catalog page for discovery surfaces."""
    supabase: Client | SupabaseRestClient = app.state.supabase
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)
    try:
        response = (
            supabase.table("products")
            .select("*")
            .range(safe_offset, safe_offset + safe_limit - 1)
            .execute()
        )
    except Exception as error:
        logging.exception("Supabase products select failed.")
        raise HTTPException(status_code=502, detail="Products are temporarily unavailable.") from error

    products = format_catalog_products(
        supabase,
        [row for row in response.data or [] if row.get("catalog_status") != "NON_PRODUCT"],
    )
    products.sort(key=lambda product: product.get("scraped_at") or "", reverse=True)
    return {
        "products": products,
        "limit": safe_limit,
        "offset": safe_offset,
        "has_more": len(response.data or []) == safe_limit,
    }


@app.get("/api/categories/{category_slug}")
async def category_catalog(category_slug: str, limit: int = 48, offset: int = 0):
    """Return validated products and only the brands derived from those products."""
    supabase: Client | SupabaseRestClient = app.state.supabase
    try:
        categories = category_map(supabase)
        target = categories.get(category_slug)
        if not target:
            raise HTTPException(status_code=404, detail="Category not found.")
        all_products = attach_category_audience(fetch_all(supabase, "products"), categories.values())
        all_brands = fetch_all(supabase, "brands")
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Category catalog lookup failed for %s", category_slug)
        raise HTTPException(status_code=502, detail="Category results are temporarily unavailable.") from error

    children_by_parent: dict[Any, list[Any]] = {}
    for category in categories.values():
        children_by_parent.setdefault(category.get("parent_id"), []).append(category.get("id"))
    allowed_ids = {target.get("id")}
    queue = [target.get("id")]
    while queue:
        child_ids = children_by_parent.get(queue.pop(), [])
        allowed_ids.update(child_ids)
        queue.extend(child_ids)

    validated = []
    for row in all_products:
        if product_supports_category(row, category_slug, categories):
            validated.append(format_product(row))
    validated.sort(key=lambda row: row.get("scraped_at") or "", reverse=True)
    page = validated[max(0, offset): max(0, offset) + max(1, min(limit, 100))]
    evidence: dict[str, list[dict[str, Any]]] = {}
    for row in validated:
        if row.get("brand_id"):
            evidence.setdefault(str(row["brand_id"]), []).append({"product_id":row.get("id"),"product_name":row.get("product_name"),"confidence":next((float(source.get("classifier_confidence") or 0) for source in all_products if source.get("id") == row.get("id")),0)})
    brands = []
    for row in all_brands:
        rows = evidence.get(str(row.get("id")), [])
        if not rows:
            continue
        brands.append({
            **format_brand(row),
            "supporting_product_count": len(rows),
            "supporting_product_ids": [item["product_id"] for item in rows],
            "supporting_products": rows[:5],
            "average_confidence": round(sum(item["confidence"] for item in rows) / len(rows), 4),
        })
    if os.getenv("ENVIRONMENT", "development").lower() != "production":
        logging.info("[CATEGORY QUERY] slug=%s candidates=%s validated=%s brands=%s returned=%s", category_slug, len(all_products), len(validated), len(brands), len(page))
    return {"category": target, "products": page, "brands": brands, "total": len(validated), "limit": limit, "offset": offset, "query_debug":{"candidate_products":len(all_products),"validated_products":len(validated),"distinct_brands":len(brands)} if os.getenv("ENVIRONMENT","development").lower() != "production" else None}


@app.get("/api/categories/{category_slug}/types")
async def category_types(category_slug: str):
    supabase: Client | SupabaseRestClient = app.state.supabase
    family_by_category = {
        "women-tops": {"tops", "shirts"}, "women-ethnic-wear": {"ethnic-upperwear", "sets"},
        "women-kurtis": {"ethnic-upperwear"}, "women-bottoms": {"bottoms"}, "women-jeans": {"bottoms"},
        "accessories-jewellery": {"jewellery"}, "women-jewelry": {"jewellery"},
        "accessories-bags": {"bags"}, "women-bags": {"bags"},
    }
    try:
        categories = category_map(supabase)
        if category_slug not in categories and category_slug not in family_by_category:
            raise HTTPException(status_code=404, detail="Category not found.")
        enriched_products = attach_category_audience(fetch_all(supabase, "products"), categories.values())
        products = [format_product(row) for row in enriched_products if product_supports_category(row, category_slug, categories)]
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Category type lookup failed for %s", category_slug)
        raise HTTPException(status_code=502, detail="Category types are temporarily unavailable.") from error
    return {"category": category_slug, "types": representative_type_tiles(products, family_by_category.get(category_slug))}


def _discovery_product(row: dict[str, Any]) -> dict[str, Any]:
    public = format_product(row)
    for key in ("gem_score", "gem_label", "personalized_discovery_score", "personalization_matched", "recommendation_reason", "product_family", "product_type"):
        public[key] = row.get(key)
    if os.getenv("ENVIRONMENT", "development").lower() != "production":
        public["discovery_debug"] = {"components": row.get("components"), "exposure_boost": row.get("exposure_boost"), "eligible": row.get("discovery_eligible")}
    return public


def _discovery_brand(row: dict[str, Any]) -> dict[str, Any]:
    public = format_brand(row)
    for key in ("gem_score", "gem_label", "recommendation_reason", "dominant_styles", "eligible_product_count"):
        public[key] = row.get(key)
    if os.getenv("ENVIRONMENT", "development").lower() != "production": public["gem_components"] = row.get("gem_components")
    return public


def _load_discovery(category: str | None = None, product_type: str | None = None, limit: int = 16, user_id: UUID | None = None, session_seed: str | None = None) -> dict[str, Any]:
    # A verified signed-in user id is preferred when available (stable across devices);
    # otherwise the client's own session seed (e.g. its localStorage guest id) is used
    # purely as a determinism seed here -- never for any data lookup, so it carries
    # none of the IDOR risk a client-supplied *identity* would.
    effective_seed = str(user_id) if user_id else (session_seed or "")
    cache_key = (category, product_type, limit, str(user_id) if user_id else None, session_seed)
    cached = _cache_get(DISCOVERY_FEED_CACHE, cache_key, DISCOVERY_FEED_CACHE_TTL)
    if cached is not None: return cached
    supabase: Client | SupabaseRestClient = app.state.supabase
    try:
        try:
            categories = category_map(supabase)
        except Exception:
            categories = {}
        products = attach_category_audience(
            [row for row in fetch_all(supabase, "products") if row.get("catalog_status") != "NON_PRODUCT"],
            categories.values(),
        )
        brands = fetch_all(supabase, "brands")
        try: interactions = fetch_all(supabase, "interactions")
        except Exception: interactions = []
    except Exception as error:
        raise HTTPException(status_code=502, detail="Discovery feeds are temporarily unavailable.") from error
    preferences = None
    personalization_client = getattr(app.state, "personalization", None)
    if user_id is not None and personalization_client is not None:
        try: preferences = load_personalization_context(personalization_client, user_id)
        except Exception: preferences = None
    sections = discovery_sections(products, brands, supabase_url=os.getenv("SUPABASE_URL", ""), interactions=interactions, preferences=preferences, category=category, product_type=product_type, limit=max(1,min(limit,50)), seed=effective_seed)
    payload = {key:[_discovery_product(row) for row in sections[key]] for key in ("hidden_gems","trending","fresh_drops","new_discoveries","missed")}
    payload["emerging_brands"] = [_discovery_brand(row) for row in sections["emerging_brands"]]
    payload["trending_brands"] = [_discovery_brand(row) for row in sections["trending_brands"]]
    payload["trending_signal"] = sections["trending_signal"]
    payload["stylish_tops"] = [_discovery_product(row) for row in sections["scored_products"] if row.get("discovery_eligible") and row.get("product_family") == "tops"][:16]
    payload["modern_ethnic"] = [_discovery_product(row) for row in sections["scored_products"] if row.get("discovery_eligible") and row.get("product_family") in {"ethnic-upperwear","sets"}][:16]
    if os.getenv("ENVIRONMENT", "development").lower() != "production":
        payload["discovery_diagnostics"] = {"candidates": len(products), "eligible": sections["eligible_candidates"]}
    _cache_put(DISCOVERY_FEED_CACHE, cache_key, payload, DISCOVERY_FEED_CACHE_MAX_ENTRIES)
    return payload


@app.get("/api/discovery/feeds")
async def discovery_feeds(request: Request, category: str | None = None, product_type: str | None = None, limit: int = 16, session_seed: str | None = None): return _load_discovery(category, product_type, limit, optional_user_id(request), session_seed)

@app.get("/api/discovery/hidden-gems")
async def hidden_gems(category: str | None = None, product_type: str | None = None, limit: int = 16): return {"products":_load_discovery(category, product_type, limit)["hidden_gems"]}

@app.get("/api/discovery/trending")
async def trending(category: str | None = None, product_type: str | None = None, limit: int = 16):
    data=_load_discovery(category, product_type, limit); return {"products":data["trending"],"signal":data["trending_signal"]}

@app.get("/api/discovery/emerging-brands")
async def emerging_brands(category: str | None = None, limit: int = 16): return {"brands":_load_discovery(category, None, limit)["emerging_brands"]}

@app.get("/api/discovery/new")
async def new_discoveries(category: str | None = None, product_type: str | None = None, limit: int = 16): return {"products":_load_discovery(category, product_type, limit)["new_discoveries"]}

@app.get("/api/discovery/fresh-drops")
async def fresh_drops(category: str | None = None, product_type: str | None = None, limit: int = 16): return {"products":_load_discovery(category, product_type, limit)["fresh_drops"]}

@app.get("/api/discovery/missed")
async def missed(category: str | None = None, product_type: str | None = None, limit: int = 16): return {"products":_load_discovery(category, product_type, limit)["missed"]}


@app.get("/api/admin/catalog/health")
async def catalog_health(x_admin_key: str | None = Header(default=None)):
    require_admin_key(x_admin_key)
    try:
        brands, products, failures = load_catalog(app.state.supabase)
        report = build_catalog_report(brands, products, failures)
    except Exception as error:
        logging.exception("Catalog health scan failed.")
        raise HTTPException(status_code=502, detail="Catalog health is temporarily unavailable.") from error
    return {key: value for key, value in report.items() if key not in {"issues", "duplicate_groups", "brand_health"}}


@app.get("/api/admin/catalog/scale")
async def catalog_scale(x_admin_key: str | None = Header(default=None)):
    require_admin_key(x_admin_key)
    try:
        brands, products, _ = load_catalog(app.state.supabase)
        return build_catalog_scale_report(brands, products)
    except Exception as error:
        logging.exception("Catalog scale report failed.")
        raise HTTPException(status_code=502, detail="Catalog scale report is temporarily unavailable.") from error


@app.get("/api/admin/catalog/issues")
async def catalog_issues(issue: str | None = None, brand_id: str | None = None, limit: int = 50, x_admin_key: str | None = Header(default=None)):
    require_admin_key(x_admin_key)
    try:
        brands, products, failures = load_catalog(app.state.supabase)
        rows = build_catalog_report(brands, products, failures)["issues"]
    except Exception as error:
        logging.exception("Catalog issue scan failed.")
        raise HTTPException(status_code=502, detail="Catalog issues are temporarily unavailable.") from error
    if issue:
        rows = [row for row in rows if issue.upper() in row["warnings"]]
    if brand_id:
        rows = [row for row in rows if str(row["product"].get("brand_id")) == brand_id]
    return {"issues": rows[: max(1, min(limit, 500))], "total": len(rows)}


@app.get("/api/admin/brands/health")
async def brands_health(x_admin_key: str | None = Header(default=None)):
    require_admin_key(x_admin_key)
    try:
        brands, products, failures = load_catalog(app.state.supabase)
        return {"brands": build_catalog_report(brands, products, failures)["brand_health"]}
    except Exception as error:
        logging.exception("Brand health scan failed.")
        raise HTTPException(status_code=502, detail="Brand health is temporarily unavailable.") from error


EVAL_RUNS_DIR = Path(__file__).resolve().parent / "eval" / "runs"
INGESTION_AUDIT_DIR = Path(__file__).resolve().parent.parent / "data" / "ingestion_audit"


@app.get("/api/admin/dashboard")
async def admin_dashboard(x_admin_key: str | None = Header(default=None)):
    require_admin_key(x_admin_key)
    try:
        brands, products, failures = load_catalog(app.state.supabase)
        catalog_report = build_catalog_report(brands, products, failures)
        category_name_by_id = {row.get("id"): row.get("name") for row in fetch_all(app.state.supabase, "categories")}
    except Exception as error:
        logging.exception("Admin dashboard catalog scan failed.")
        raise HTTPException(status_code=502, detail="Catalog data is temporarily unavailable.") from error
    return {
        "catalog_health": {
            key: value for key, value in catalog_report.items() if key not in {"issues", "duplicate_groups", "brand_health"}
        },
        "confidence_distribution": confidence_distribution(products),
        "caption_like_names": caption_like_names(products),
        "category_audience_breakdown": category_audience_breakdown(products, category_name_by_id),
        "brand_category_breakdown": brand_category_breakdown(brands),
        "search_analytics": search_analytics(read_search_query_log()),
        "relevance_runs": read_eval_run_history(EVAL_RUNS_DIR),
        "ingestion_runs": read_ingestion_runs(INGESTION_AUDIT_DIR),
    }


@app.get("/api/brands/{brand_id}/products")
async def list_brand_products(brand_id: str):
    supabase: Client | SupabaseRestClient = app.state.supabase
    try:
        response = supabase.table("products").select("*").eq("brand_id", brand_id).execute()
    except Exception as error:
        logging.exception("Supabase products select failed for brand %s.", brand_id)
        raise HTTPException(status_code=502, detail="Brand products are temporarily unavailable.") from error

    products = format_catalog_products(supabase, response.data or [])
    products.sort(key=lambda product: product.get("scraped_at") or "", reverse=True)
    return {"products": products}


@app.get("/api/products/{product_id}/related")
async def related_products(product_id: str):
    supabase: Client | SupabaseRestClient = app.state.supabase
    try:
        source_response = supabase.table("products").select("*").eq("id", product_id).limit(1).execute()
        source_rows = source_response.data or []
        if not source_rows:
            raise HTTPException(status_code=404, detail="Product not found.")
        embedding = normalize_embedding_value(source_rows[0].get("embedding"))
        if not embedding:
            return {"products": []}
        # Derive the same structured signals (product type/family/colour/style/gender)
        # from the source product that text search derives from a user's query, so the
        # existing hard-filters and score_breakdown actually discriminate here too --
        # otherwise this path only has (a possibly wrong) category_id and raw
        # embedding distance to go on. search_mode="image" because the stored
        # embedding is CLIP-image-based, so it should be weighted as visual
        # similarity (0.72), not diluted as lexical semantic_similarity (0.22).
        source_text = " ".join(str(source_rows[0].get(field) or "") for field in ("product_name", "item_name", "description"))
        related = run_match_products_rpc(
            supabase=supabase,
            query_embedding=embedding,
            category_id=source_rows[0].get("category_id"),
            match_threshold=0.0,
            match_count=9,
            search_mode="image",
            query_attributes=extract_query_attributes(source_text),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Related product lookup failed for %s.", product_id)
        raise HTTPException(status_code=502, detail="Related products are temporarily unavailable.") from error

    return {"products": [product for product in related if product.get("id") != product_id][:8]}


@app.get("/api/brands/{brand_id}/similar")
async def similar_brands_for_brand(brand_id: str):
    supabase: Client | SupabaseRestClient = app.state.supabase
    try:
        brand_response = supabase.table("brands").select("*").eq("id", brand_id).limit(1).execute()
        brand_rows = brand_response.data or []
        if not brand_rows:
            raise HTTPException(status_code=404, detail="Brand not found.")

        product_response = supabase.table("products").select("*").execute()
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Similar brand lookup failed for %s.", brand_id)
        raise HTTPException(status_code=502, detail="Similar brands are temporarily unavailable.") from error

    target_products = [format_product(row) for row in product_response.data or [] if row.get("brand_id") == brand_id]
    if not target_products:
        return {"brands": []}

    target_categories = {product.get("category_id") for product in target_products if product.get("category_id") is not None}
    target_niche = sum(float(product.get("niche_score") or 0.0) for product in target_products) / max(len(target_products), 1)
    by_brand: dict[str, dict[str, Any]] = {}

    for row in product_response.data or []:
        if row.get("brand_id") == brand_id or not row.get("brand_id"):
            continue
        product = format_product(row)
        current = by_brand.setdefault(
            str(product["brand_id"]),
            {
                "brand_id": product["brand_id"],
                "brand_name": product["brand_name"],
                "shared_categories": 0,
                "product_count": 0,
                "niche_total": 0.0,
            },
        )
        current["product_count"] += 1
        current["niche_total"] += float(product.get("niche_score") or 0.0)
        if product.get("category_id") in target_categories:
            current["shared_categories"] += 1

    scored = []
    for brand in by_brand.values():
        avg_niche = brand["niche_total"] / max(brand["product_count"], 1)
        shared_score = brand["shared_categories"] / max(len(target_categories), 1)
        niche_score = 1 - min(1.0, abs(avg_niche - target_niche))
        scored.append({**brand, "average_niche_score": avg_niche, "similarity_score": (0.65 * shared_score) + (0.35 * niche_score)})

    scored.sort(key=lambda brand: brand["similarity_score"], reverse=True)
    return {"brands": scored[:8]}


@app.post("/api/discover")
async def discover(
    request: Request,
    text_query: str | None = Form(default=None),
    image_file: UploadFile | None = File(default=None),
    category_id: int | None = Form(default=None),
    category_slug: str | None = Form(default=None),
    min_price: float | None = Form(default=None),
    max_price: float | None = Form(default=None),
    min_niche_score: float | None = Form(default=None),
    min_followers: int | None = Form(default=None),
    brand: str | None = Form(default=None),
    gender: str | None = Form(default=None),
    sort_by: str | None = Form(default=None),
    debug: bool = Form(default=False),
):
    try:
        require_single_search_input(text_query=text_query, image_file=image_file)
    except UploadValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    model: SentenceTransformer = app.state.clip_model
    supabase: Client | SupabaseRestClient = app.state.supabase
    resolved_category_id = category_id
    if isinstance(category_slug, str) and category_slug.strip():
        target = category_map(supabase).get(category_slug.strip())
        if not target:
            raise HTTPException(status_code=400, detail="Unknown category_slug.")
        resolved_category_id = int(target["id"])

    if image_file is not None:
        try:
            image_bytes = await read_validated_image_upload(image_file)
        except UploadValidationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        query_embedding = encode_image(get_image_encoder(model), image_bytes)
        search_mode = "image"
        query_attributes: dict[str, Any] = {}
    else:
        query_embedding = encode_text(model, text_query or "")
        search_mode = "text"
        query_attributes = extract_query_attributes(text_query or "")

    resolved_max_price = normalize_optional_float(max_price)
    if resolved_max_price is None:
        resolved_max_price = query_attributes.get("max_price")
    resolved_gender = normalize_optional_text(gender) or query_attributes.get("gender")
    resolved_min_price = normalize_optional_float(min_price)
    if resolved_min_price is None:
        resolved_min_price = query_attributes.get("min_price")
    personalization_context = None
    user_id = optional_user_id(request)
    if user_id is not None and getattr(app.state, "personalization", None) is not None:
        personalization_context = load_personalization_context(app.state.personalization, user_id)

    cache_stats: dict[str, Any] = {}
    search_started_at = time.perf_counter()
    results = run_match_products_rpc(
        supabase=supabase,
        query_embedding=query_embedding,
        category_id=resolved_category_id,
        min_price=resolved_min_price,
        max_price=resolved_max_price,
        min_niche_score=normalize_optional_float(min_niche_score),
        min_followers=normalize_optional_int(min_followers),
        brand=normalize_optional_text(brand),
        gender=resolved_gender,
        sort_by=normalize_optional_text(sort_by),
        match_count=DISCOVER_CANDIDATE_LIMIT,
        result_limit=DISCOVER_LIMIT,
        search_mode=search_mode,
        query_attributes=query_attributes,
        personalization_context=personalization_context,
        retrieval_mode=SEARCH_MODE,
        rerank_mode=RERANK,
        query_text=text_query if search_mode == "text" else None,
        cache_stats=cache_stats,
    )
    search_latency_ms = (time.perf_counter() - search_started_at) * 1000
    # Analytics only -- text queries alone (an image upload has no query text to
    # aggregate into "top queries"), and never anything that identifies who searched.
    if search_mode == "text" and text_query and text_query.strip():
        try:
            log_search_query(text_query.strip(), len(results), search_latency_ms, cache_stats.get("hit", False))
        except OSError:
            logging.exception("Could not write search query log.")
    best_matches, more_like_this = split_search_groups(results, query_attributes)
    debug_enabled = debug and os.getenv("ENVIRONMENT", "development").lower() != "production"
    return {
        "results": public_search_products(results, debug=debug_enabled),
        "best_matches": public_search_products(best_matches, debug=debug_enabled),
        "more_like_this": public_search_products(more_like_this, debug=debug_enabled),
        "more_like_this_label": f"Similar {FAMILY_LABELS.get(query_attributes.get('product_family'), 'styles')}" if more_like_this else None,
        "inventory_gap": bool(query_attributes.get("product_type") and len(best_matches) < 5),
        "similar_brands": build_similar_brands(best_matches or more_like_this),
        "query_understanding": query_attributes,
        "ranking_weights": (IMAGE_RANKING_WEIGHTS if search_mode == "image" else TEXT_RANKING_WEIGHTS) if debug_enabled else None,
        "debug": debug_enabled,
        "search_mode": search_mode,
        "personalized": personalization_context is not None,
    }


@app.post("/api/recommend")
async def recommend(
    text_query: str | None = Form(default=None),
    image_file: UploadFile | None = File(default=None),
    category_id: int | None = Form(default=None),
    category_slug: str | None = Form(default=None),
    min_price: float | None = Form(default=None),
    max_price: float | None = Form(default=None),
    min_niche_score: float | None = Form(default=None),
    min_followers: int | None = Form(default=None),
    brand: str | None = Form(default=None),
    gender: str | None = Form(default=None),
    sort_by: str | None = Form(default=None),
    debug: bool = Form(default=False),
):
    try:
        require_single_search_input(text_query=text_query, image_file=image_file)
    except UploadValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    model: SentenceTransformer = app.state.clip_model
    supabase: Client | SupabaseRestClient = app.state.supabase
    resolved_category_id = category_id
    if isinstance(category_slug, str) and category_slug.strip():
        target = category_map(supabase).get(category_slug.strip())
        if not target:
            raise HTTPException(status_code=400, detail="Unknown category_slug.")
        resolved_category_id = int(target["id"])

    if image_file is not None:
        try:
            image_bytes = await read_validated_image_upload(image_file)
        except UploadValidationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        query_embedding = encode_image(get_image_encoder(model), image_bytes)
        search_mode = "image"
        query_attributes: dict[str, Any] = {}
    else:
        query_embedding = encode_text(model, text_query or "")
        search_mode = "text"
        query_attributes = extract_query_attributes(text_query or "")

    resolved_max_price = normalize_optional_float(max_price)
    if resolved_max_price is None:
        resolved_max_price = query_attributes.get("max_price")
    resolved_gender = normalize_optional_text(gender) or query_attributes.get("gender")
    resolved_min_price = normalize_optional_float(min_price)
    if resolved_min_price is None:
        resolved_min_price = query_attributes.get("min_price")

    similar_products = run_match_products_rpc(
        supabase=supabase,
        query_embedding=query_embedding,
        category_id=resolved_category_id,
        match_threshold=RECOMMEND_MATCH_THRESHOLD,
        match_count=DISCOVER_CANDIDATE_LIMIT,
        min_price=resolved_min_price,
        max_price=resolved_max_price,
        min_niche_score=normalize_optional_float(min_niche_score),
        min_followers=normalize_optional_int(min_followers),
        brand=normalize_optional_text(brand),
        gender=resolved_gender,
        sort_by=normalize_optional_text(sort_by),
        result_limit=RECOMMEND_PRODUCT_LIMIT,
        search_mode=search_mode,
        query_attributes=query_attributes,
        retrieval_mode=SEARCH_MODE,
        rerank_mode=RERANK,
        query_text=text_query if search_mode == "text" else None,
    )

    debug_enabled = debug and os.getenv("ENVIRONMENT", "development").lower() != "production"
    return {
        "similar_products": public_search_products(similar_products, debug=debug_enabled),
        "similar_brands": build_similar_brands(similar_products),
        "query_understanding": query_attributes,
        "ranking_weights": (IMAGE_RANKING_WEIGHTS if search_mode == "image" else TEXT_RANKING_WEIGHTS) if debug_enabled else None,
        "debug": debug_enabled,
    }
