import os
from dataclasses import dataclass

from dotenv import load_dotenv
from psycopg_pool import ConnectionPool


load_dotenv()
load_dotenv("backend/.env", override=True)


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str | None
    clip_model_name: str
    search_limit: int


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required. Add it to backend/.env.")

    return Settings(
        database_url=database_url,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        clip_model_name=os.getenv("CLIP_MODEL_NAME", "clip-ViT-B-32"),
        search_limit=int(os.getenv("SEARCH_LIMIT", "20")),
    )


def create_pool(min_size: int = 1, max_size: int = 5) -> ConnectionPool:
    settings = get_settings()
    return ConnectionPool(
        conninfo=settings.database_url,
        min_size=min_size,
        max_size=max_size,
        kwargs={"autocommit": True},
    )
