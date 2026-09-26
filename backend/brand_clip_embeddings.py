"""
SQL SETUP FOR SUPABASE POSTGRES + PGVECTOR

-- 1. Enable pgvector and UUID generation.
create extension if not exists vector;
create extension if not exists pgcrypto;

-- 2. Create the table that stores brand image embeddings.
create table if not exists public.brand_embeddings (
    id uuid primary key default gen_random_uuid(),
    brand_handle text not null,
    post_url text not null,
    embedding vector(512) not null,
    created_at timestamptz not null default timezone('utc', now())
);

-- Optional cosine index for faster nearest-neighbor search.
create index if not exists brand_embeddings_embedding_idx
on public.brand_embeddings
using hnsw (embedding vector_cosine_ops);

-- 3. Create the RPC used by the Python search function.
create or replace function public.match_brands(
    query_embedding vector(512),
    match_threshold float,
    match_count int
)
returns table (
    id uuid,
    brand_handle text,
    post_url text,
    similarity float
)
language sql stable
as $$
    select
        brand_embeddings.id,
        brand_embeddings.brand_handle,
        brand_embeddings.post_url,
        1 - (brand_embeddings.embedding <=> query_embedding) as similarity
    from public.brand_embeddings
    where 1 - (brand_embeddings.embedding <=> query_embedding) >= match_threshold
    order by brand_embeddings.embedding <=> query_embedding
    limit match_count;
$$;
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
import torch
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from postgrest.exceptions import APIError
from supabase import Client, create_client
from transformers import CLIPModel, CLIPProcessor


load_dotenv()
load_dotenv("backend/.env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

MODEL_NAME: str = "openai/clip-vit-base-patch32"
EMBEDDING_DIMENSIONS: int = 512
TABLE_NAME: str = "brand_embeddings"
MATCH_RPC_NAME: str = "match_brands"

DEVICE: torch.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
_MODEL: CLIPModel | None = None
_PROCESSOR: CLIPProcessor | None = None
_SUPABASE: Client | None = None


class EmbeddingError(RuntimeError):
    """Raised when CLIP cannot produce a valid image embedding."""


class SupabaseConfigurationError(RuntimeError):
    """Raised when Supabase credentials are missing or invalid."""


class SupabaseOperationError(RuntimeError):
    """Raised when a Supabase insert or RPC call fails."""


def _get_clip_components() -> tuple[CLIPModel, CLIPProcessor]:
    """Load and cache the Hugging Face CLIP model and processor."""
    global _MODEL, _PROCESSOR

    if _MODEL is None or _PROCESSOR is None:
        logging.info("Loading CLIP model '%s' on %s.", MODEL_NAME, DEVICE)
        _MODEL = CLIPModel.from_pretrained(MODEL_NAME).to(DEVICE)
        _MODEL.eval()
        _PROCESSOR = CLIPProcessor.from_pretrained(MODEL_NAME)

    return _MODEL, _PROCESSOR


def _get_supabase_client() -> Client:
    """Create and cache the official Supabase Python client."""
    global _SUPABASE

    if _SUPABASE is not None:
        return _SUPABASE

    supabase_url: str | None = os.getenv("SUPABASE_URL")
    supabase_key: str | None = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise SupabaseConfigurationError(
            "SUPABASE_URL and SUPABASE_KEY must be set in the environment."
        )

    try:
        _SUPABASE = create_client(supabase_url, supabase_key)
        return _SUPABASE
    except Exception as exc:
        raise SupabaseConfigurationError(
            f"Could not initialize Supabase client: {exc}"
        ) from exc


def _validate_image_path(image_path: str) -> Path:
    """Resolve and validate a local image path before embedding."""
    path: Path = Path(image_path).expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(f"Image file does not exist: {path}")

    if not path.is_file():
        raise FileNotFoundError(f"Image path is not a file: {path}")

    return path


def get_image_embedding(image_path: str) -> list[float]:
    """
    Generate a normalized CLIP image embedding.

    Args:
        image_path: Local path to a fashion image.

    Returns:
        A normalized 512-dimensional vector as a plain Python list of floats.
    """
    path: Path = _validate_image_path(image_path)

    try:
        image: Image.Image = Image.open(path).convert("RGB")
    except UnidentifiedImageError as exc:
        raise EmbeddingError(f"File is not a valid image: {path}") from exc
    except OSError as exc:
        raise EmbeddingError(f"Could not open image file: {path}") from exc

    try:
        model, processor = _get_clip_components()
        inputs: dict[str, torch.Tensor] = processor(
            images=image,
            return_tensors="pt",
        )
        inputs = {key: value.to(DEVICE) for key, value in inputs.items()}

        with torch.no_grad():
            embedding_tensor: torch.Tensor = model.get_image_features(**inputs)
            embedding_tensor = embedding_tensor / embedding_tensor.norm(
                p=2,
                dim=-1,
                keepdim=True,
            )

        embedding: list[float] = embedding_tensor.squeeze(0).cpu().tolist()

        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                "CLIP returned "
                f"{len(embedding)} dimensions; expected {EMBEDDING_DIMENSIONS}."
            )

        return embedding
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"Failed to generate image embedding: {exc}") from exc


def insert_brand_item(image_path: str, brand_handle: str, post_url: str) -> dict[str, Any]:
    """
    Embed a brand image and insert it into the brand_embeddings table.

    Args:
        image_path: Local image path.
        brand_handle: Brand handle, without requiring an @ prefix.
        post_url: Source URL for the image/post.

    Returns:
        Inserted Supabase row data.
    """
    clean_brand_handle: str = brand_handle.strip().lstrip("@")
    clean_post_url: str = post_url.strip()

    if not clean_brand_handle:
        raise ValueError("brand_handle must not be empty.")

    if not clean_post_url:
        raise ValueError("post_url must not be empty.")

    try:
        embedding: list[float] = get_image_embedding(image_path)
        payload: dict[str, Any] = {
            "brand_handle": clean_brand_handle,
            "post_url": clean_post_url,
            "embedding": embedding,
        }

        response = _get_supabase_client().table(TABLE_NAME).insert(payload).execute()
        logging.info("Inserted brand embedding for @%s.", clean_brand_handle)
        return {"data": response.data}
    except FileNotFoundError:
        logging.exception("Cannot insert brand item because the image is missing.")
        raise
    except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as exc:
        raise SupabaseOperationError(
            f"Supabase connection dropped during insert: {exc}"
        ) from exc
    except APIError as exc:
        raise SupabaseOperationError(f"Supabase insert failed: {exc}") from exc
    except Exception as exc:
        raise SupabaseOperationError(f"Failed to insert brand item: {exc}") from exc


def search_similar_brands(
    query_image_path: str,
    match_threshold: float = 0.7,
    match_count: int = 5,
) -> list[dict[str, Any]]:
    """
    Search for visually similar brand embeddings through the match_brands RPC.

    Args:
        query_image_path: Local image path to search with.
        match_threshold: Minimum cosine similarity to return.
        match_count: Maximum number of matches to return.

    Returns:
        Supabase RPC result rows.
    """
    if not 0.0 <= match_threshold <= 1.0:
        raise ValueError("match_threshold must be between 0.0 and 1.0.")

    if match_count <= 0:
        raise ValueError("match_count must be greater than 0.")

    try:
        query_embedding: list[float] = get_image_embedding(query_image_path)
        rpc_payload: dict[str, Any] = {
            "query_embedding": query_embedding,
            "match_threshold": match_threshold,
            "match_count": match_count,
        }

        response = _get_supabase_client().rpc(MATCH_RPC_NAME, rpc_payload).execute()
        matches: list[dict[str, Any]] = response.data or []

        if not matches:
            print("No matching brands found.")
            return []

        print("\nSimilar brand matches")
        print("-" * 48)
        for index, match in enumerate(matches, start=1):
            brand: str = str(match.get("brand_handle", "unknown"))
            similarity: float = float(match.get("similarity", 0.0))
            post_url_value: str = str(match.get("post_url", ""))
            print(f"{index}. @{brand}")
            print(f"   Similarity: {similarity:.4f}")
            print(f"   Post URL:   {post_url_value}")

        return matches
    except FileNotFoundError:
        logging.exception("Cannot search because the query image is missing.")
        raise
    except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as exc:
        raise SupabaseOperationError(
            f"Supabase connection dropped during search: {exc}"
        ) from exc
    except APIError as exc:
        raise SupabaseOperationError(f"Supabase similarity search failed: {exc}") from exc
    except Exception as exc:
        raise SupabaseOperationError(f"Failed to search similar brands: {exc}") from exc


if __name__ == "__main__":
    mock_image_path: str = "./mock_fashion_image.jpg"
    mock_query_image_path: str = "./mock_query_image.jpg"
    mock_brand_handle: str = "sample_brand"
    mock_post_url: str = "https://example.com/sample-brand/post/123"

    try:
        insert_brand_item(
            image_path=mock_image_path,
            brand_handle=mock_brand_handle,
            post_url=mock_post_url,
        )

        search_similar_brands(
            query_image_path=mock_query_image_path,
            match_threshold=0.7,
            match_count=5,
        )
    except Exception as error:
        logging.error("Brand embedding workflow failed: %s", error)
