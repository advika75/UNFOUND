import argparse
import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI
from psycopg.rows import dict_row

from backend.db import create_pool, get_settings


load_dotenv()
load_dotenv("backend/.env", override=True)


@dataclass(frozen=True)
class ProductClassification:
    category_id: int
    confidence_score: float
    reasoning: str


CLASSIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category_id", "confidence_score", "reasoning"],
    "properties": {
        "category_id": {"type": "integer"},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
}


def load_categories(db_pool) -> dict[int, dict]:
    sql = """
        select
          c.id,
          c.parent_id,
          c.name,
          c.slug,
          not exists (
            select 1 from public.categories child where child.parent_id = c.id
          ) as is_leaf
        from public.categories c
        order by c.parent_id nulls first, c.name;
    """
    with db_pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()

    return {int(row["id"]): dict(row) for row in rows}


def compact_raw_text(value: str | None) -> str:
    if not value:
        return ""

    return " ".join(value.replace("\n", " ").replace("\t", " ").split())


def fallback_category_id(category_id: int, categories: dict[int, dict]) -> int:
    category = categories.get(category_id)
    if not category:
        roots = [item for item in categories.values() if item["parent_id"] is None]
        if not roots:
            raise RuntimeError("No valid categories exist in the database.")
        return int(roots[0]["id"])

    parent_id = category.get("parent_id")
    return int(parent_id or category_id)


def validate_classification(payload: dict, categories: dict[int, dict]) -> ProductClassification:
    category_id = int(payload["category_id"])
    confidence_score = float(payload["confidence_score"])
    reasoning = str(payload["reasoning"])

    if category_id not in categories:
        category_id = fallback_category_id(category_id, categories)
        confidence_score = min(confidence_score, 0.69)
        reasoning = f"Model returned an invalid category id; assigned nearest valid parent. {reasoning}"

    if confidence_score < 0.7:
        category_id = fallback_category_id(category_id, categories)
        reasoning = f"Confidence below 0.7; assigned parent category. {reasoning}"

    return ProductClassification(
        category_id=category_id,
        confidence_score=round(max(0, min(confidence_score, 1)), 4),
        reasoning=reasoning[:1000],
    )


def classify_product(
    *,
    brand_name: str,
    item_name: str,
    description: str = "",
    tags: list[str] | None = None,
    categories: dict[int, dict],
) -> ProductClassification:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for classification.")

    category_context = [
        {
            "id": category["id"],
            "parent_id": category["parent_id"],
            "name": category["name"],
            "slug": category["slug"],
            "is_leaf": category["is_leaf"],
        }
        for category in categories.values()
    ]

    system_prompt = (
        "You are an e-commerce taxonomy clerk. You classify messy scraped product data "
        "into an existing database category tree. You must choose only from the provided "
        "category IDs. Prefer the most granular LEAF category available. For example, "
        "a pair of men's jeans must be assigned to the Men's Jeans category, not a "
        "generic parent such as Men or Men Clothing. If gender, product type, or intent "
        "is ambiguous, choose the safest nearest valid parent category. Return strict "
        "JSON only with category_id, confidence_score, and reasoning."
    )

    user_payload = {
        "existing_categories": category_context,
        "raw_product": {
            "brand_name": compact_raw_text(brand_name),
            "item_name": compact_raw_text(item_name),
            "description": compact_raw_text(description),
            "tags": [compact_raw_text(tag) for tag in tags or [] if compact_raw_text(tag)],
        },
    }

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_CLASSIFICATION_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "product_category_classification",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            },
        },
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned an empty classification response.")

    payload = json.loads(content)
    return validate_classification(payload, categories)


def update_product_category(db_pool, product_id: str, classification: ProductClassification):
    sql = """
        update public.products
        set category_id = %(category_id)s,
            updated_at = timezone('utc', now())
        where id = %(product_id)s
        returning id, category_id;
    """
    with db_pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                sql,
                {
                    "product_id": product_id,
                    "category_id": classification.category_id,
                },
            )
            return cursor.fetchone()


def main():
    parser = argparse.ArgumentParser(description="Classify scraped product text into categories.")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--tags", default="", help="Comma-separated scraped tags.")
    parser.add_argument("--product-id", help="Optional product UUID to update after classification.")
    args = parser.parse_args()

    db_pool = create_pool(min_size=1, max_size=2)
    try:
        categories = load_categories(db_pool)
        classification = classify_product(
            brand_name=args.brand,
            item_name=args.item,
            description=args.description,
            tags=[tag.strip() for tag in args.tags.split(",") if tag.strip()],
            categories=categories,
        )

        if args.product_id:
            update_product_category(db_pool, args.product_id, classification)

        print(
            json.dumps(
                {
                    "category_id": classification.category_id,
                    "confidence_score": classification.confidence_score,
                    "reasoning": classification.reasoning,
                },
                ensure_ascii=True,
            )
        )
    finally:
        db_pool.close()


if __name__ == "__main__":
    main()
