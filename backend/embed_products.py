from io import BytesIO

import requests
from PIL import Image, UnidentifiedImageError
from psycopg.rows import dict_row
from sentence_transformers import SentenceTransformer

from backend.app import vector_to_pgvector
from backend.db import create_pool, get_settings


def fetch_products_without_embeddings(db_pool, batch_size: int):
    sql = """
        select id, brand_name, item_name, description, image_url
        from public.products
        where embedding is null
          and brand_name is not null
          and item_name is not null
          and category_id is not null
        order by created_at desc
        limit %(batch_size)s;
    """
    with db_pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, {"batch_size": batch_size})
            return cursor.fetchall()


def load_remote_image(image_url: str | None) -> Image.Image | None:
    if not image_url:
        return None

    try:
        response = requests.get(image_url, timeout=15)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGB")
    except (requests.RequestException, UnidentifiedImageError, OSError, ValueError):
        return None


def product_text(row) -> str:
    return " | ".join(
        part
        for part in [
            row.get("brand_name"),
            row.get("item_name"),
            row.get("description"),
        ]
        if part
    )


def encode_product(model: SentenceTransformer, row) -> str:
    image = load_remote_image(row.get("image_url"))
    source = image if image is not None else product_text(row)
    embedding = model.encode(source, normalize_embeddings=True)
    return vector_to_pgvector(embedding.tolist())


def update_product_embedding(db_pool, product_id: str, embedding: str):
    sql = """
        update public.products
        set embedding = %(embedding)s::vector,
            updated_at = timezone('utc', now())
        where id = %(product_id)s;
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, {"product_id": product_id, "embedding": embedding})


def main():
    settings = get_settings()
    batch_size = int(__import__("os").getenv("EMBED_BATCH_SIZE", "100"))
    model = SentenceTransformer(settings.clip_model_name)
    db_pool = create_pool(min_size=1, max_size=3)

    try:
        rows = fetch_products_without_embeddings(db_pool, batch_size)
        for row in rows:
            embedding = encode_product(model, row)
            update_product_embedding(db_pool, str(row["id"]), embedding)
            print(f"embedded product {row['id']}")
        print(f"embedded {len(rows)} product(s)")
    finally:
        db_pool.close()


if __name__ == "__main__":
    main()
