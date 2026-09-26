from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/personalization", tags=["personalization"])


class PreferencesPayload(BaseModel):
    favourite_categories: list[str] = Field(default_factory=list)
    favourite_brand_ids: list[UUID] = Field(default_factory=list)
    preferred_styles: list[str] = Field(default_factory=list)
    preferred_colours: list[str] = Field(default_factory=list)
    min_price: float | None = None
    max_price: float | None = None
    gender_preference: str | None = None


class MoodboardCreate(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    cover_image_url: str | None = None


class MoodboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    cover_image_url: str | None = None


class ItemPayload(BaseModel):
    item_id: UUID


def _client(request: Request):
    client = getattr(request.app.state, "personalization", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Personalization requires SUPABASE_SERVICE_ROLE_KEY on the backend.",
        )
    return client


def _current_user_id(request: Request) -> UUID:
    """Verify the caller's Supabase Auth access token and return their real user id.

    The underlying tables (user_profiles, saved_products, ...) have a foreign key to
    auth.users, so writes must use a real authenticated identity -- never a client-
    supplied id -- or they fail the constraint (and would be an IDOR risk otherwise).
    """
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in required.")
    token = auth_header.split(" ", 1)[1].strip()
    auth_client = getattr(request.app.state, "supabase", None)
    if auth_client is None:
        raise HTTPException(status_code=503, detail="Auth is not configured.")
    try:
        result = auth_client.auth.get_user(token)
    except Exception as error:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from error
    user = getattr(result, "user", None)
    user_id = getattr(user, "id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return UUID(user_id)


def ensure_profile(client: Any, user_id: UUID) -> None:
    client.table("user_profiles").upsert(
        {"user_id": str(user_id)}, on_conflict="user_id"
    ).execute()


def _rows(client: Any, table: str, user_id: UUID) -> list[dict[str, Any]]:
    return client.table(table).select("*").eq("user_id", str(user_id)).execute().data or []


def load_personalization_context(client: Any, user_id: UUID) -> dict[str, Any]:
    preference_rows = _rows(client, "user_preferences", user_id)
    preferences = (preference_rows[0].get("preferences") if preference_rows else None) or {}
    saved_products = _rows(client, "saved_products", user_id)
    saved_brands = _rows(client, "saved_brands", user_id)
    return {
        "preferences": preferences,
        "saved_product_ids": {str(row["product_id"]) for row in saved_products},
        "saved_brand_ids": {str(row["brand_id"]) for row in saved_brands},
    }


def personalization_match_score(product: dict[str, Any], context: dict[str, Any]) -> float:
    preferences = context.get("preferences") or {}
    saved_brand_ids = context.get("saved_brand_ids") or set()
    haystack = " ".join(str(product.get(field) or "") for field in (
        "category", "subcategory", "normalized_main_category",
        "normalized_subcategory", "description", "product_name", "audience",
    )).lower()
    signals: list[float] = []
    if str(product.get("brand_id")) in saved_brand_ids:
        signals.append(1.0)
    categories = preferences.get("favourite_categories") or []
    if categories:
        signals.append(1.0 if any(str(value).lower() in haystack for value in categories) else 0.0)
    styles = preferences.get("preferred_styles") or []
    if styles:
        signals.append(1.0 if any(str(value).lower() in haystack for value in styles) else 0.0)
    colours = preferences.get("preferred_colours") or []
    if colours:
        signals.append(1.0 if any(str(value).lower() in haystack for value in colours) else 0.0)
    gender = preferences.get("gender_preference")
    if gender:
        signals.append(1.0 if str(gender).lower() in haystack or "unisex" in haystack else 0.0)
    price = product.get("price")
    if price is not None and (preferences.get("min_price") is not None or preferences.get("max_price") is not None):
        minimum = preferences.get("min_price")
        maximum = preferences.get("max_price")
        signals.append(float((minimum is None or float(price) >= float(minimum)) and (maximum is None or float(price) <= float(maximum))))
    return sum(signals) / len(signals) if signals else 0.0


@router.get("/me")
def get_personalization(request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    ensure_profile(client, user_id)
    context = load_personalization_context(client, user_id)
    boards = _rows(client, "moodboards", user_id)
    for board in boards:
        board["items"] = client.table("moodboard_items").select("*").eq("moodboard_id", board["id"]).order("position").execute().data or []
    return {
        "preferences": context["preferences"],
        "saved_product_ids": sorted(context["saved_product_ids"]),
        "saved_brand_ids": sorted(context["saved_brand_ids"]),
        "moodboards": boards,
    }


@router.put("/me/preferences")
def put_preferences(payload: PreferencesPayload, request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    if payload.min_price is not None and payload.max_price is not None and payload.min_price > payload.max_price:
        raise HTTPException(status_code=400, detail="min_price cannot exceed max_price.")
    ensure_profile(client, user_id)
    preferences = payload.model_dump(mode="json")
    row = {"user_id": str(user_id), "preferences": preferences}
    response = client.table("user_preferences").upsert(row, on_conflict="user_id").execute()
    saved = (response.data or [row])[0]
    return {"preferences": saved.get("preferences", preferences)}


def _save(user_id: UUID, item_id: UUID, request: Request, kind: str):
    client = _client(request)
    ensure_profile(client, user_id)
    table = f"saved_{kind}s"
    column = f"{kind}_id"
    row = {"user_id": str(user_id), column: str(item_id)}
    client.table(table).upsert(row, on_conflict=f"user_id,{column}").execute()
    return {"saved": True, column: str(item_id)}


def _unsave(user_id: UUID, item_id: UUID, request: Request, kind: str):
    client = _client(request)
    table = f"saved_{kind}s"
    column = f"{kind}_id"
    client.table(table).delete().eq("user_id", str(user_id)).eq(column, str(item_id)).execute()
    return {"saved": False, column: str(item_id)}


@router.put("/me/saved-products/{product_id}")
def save_product(product_id: UUID, request: Request):
    return _save(_current_user_id(request), product_id, request, "product")


@router.delete("/me/saved-products/{product_id}")
def unsave_product(product_id: UUID, request: Request):
    return _unsave(_current_user_id(request), product_id, request, "product")


@router.put("/me/saved-brands/{brand_id}")
def save_brand(brand_id: UUID, request: Request):
    return _save(_current_user_id(request), brand_id, request, "brand")


@router.delete("/me/saved-brands/{brand_id}")
def unsave_brand(brand_id: UUID, request: Request):
    return _unsave(_current_user_id(request), brand_id, request, "brand")


def _owned_board(client: Any, user_id: UUID, board_id: UUID) -> dict[str, Any]:
    rows = client.table("moodboards").select("*").eq("id", str(board_id)).eq("user_id", str(user_id)).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Moodboard not found.")
    return rows[0]


@router.post("/me/moodboards", status_code=201)
def create_moodboard(payload: MoodboardCreate, request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    ensure_profile(client, user_id)
    row: dict[str, Any] = {"user_id": str(user_id), "title": payload.name}
    if payload.id is not None:
        row["id"] = str(payload.id)
    if payload.cover_image_url is not None:
        row["cover_image_url"] = payload.cover_image_url
    response = client.table("moodboards").insert(row).execute()
    return {"moodboard": (response.data or [row])[0]}


@router.patch("/me/moodboards/{board_id}")
def update_moodboard(board_id: UUID, payload: MoodboardUpdate, request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    _owned_board(client, user_id, board_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"description"})
    if "name" in changes:
        changes["title"] = changes.pop("name")
    response = client.table("moodboards").update(changes).eq("id", str(board_id)).execute()
    return {"moodboard": (response.data or [{"id": str(board_id), **changes}])[0]}


@router.delete("/me/moodboards/{board_id}")
def delete_moodboard(board_id: UUID, request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    _owned_board(client, user_id, board_id)
    client.table("moodboards").delete().eq("id", str(board_id)).execute()
    return {"deleted": True}


@router.put("/me/moodboards/{board_id}/products")
def add_moodboard_product(board_id: UUID, payload: ItemPayload, request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    _owned_board(client, user_id, board_id)
    # moodboard_items has no unique constraint on (moodboard_id, product_id), so upsert
    # can't target it -- check for an existing row first instead.
    existing = (
        client.table("moodboard_items").select("id")
        .eq("moodboard_id", str(board_id)).eq("product_id", str(payload.item_id))
        .execute().data or []
    )
    if not existing:
        client.table("moodboard_items").insert(
            {"moodboard_id": str(board_id), "product_id": str(payload.item_id)}
        ).execute()
    return {"updated": True}


@router.delete("/me/moodboards/{board_id}/products")
def remove_moodboard_product(board_id: UUID, payload: ItemPayload, request: Request):
    user_id = _current_user_id(request)
    client = _client(request)
    _owned_board(client, user_id, board_id)
    client.table("moodboard_items").delete().eq("moodboard_id", str(board_id)).eq("product_id", str(payload.item_id)).execute()
    return {"updated": True}
