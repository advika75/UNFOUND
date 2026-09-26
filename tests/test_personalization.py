from types import SimpleNamespace
from uuid import uuid4

from backend.app import apply_final_ranking
from backend.personalization import (
    ItemPayload,
    MoodboardCreate,
    create_moodboard,
    delete_moodboard,
    get_personalization,
    remove_moodboard_product,
    save_brand,
    save_product,
    unsave_brand,
    unsave_product,
    add_moodboard_product,
)


class Response:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, store, table, operation, payload=None):
        self.store, self.table, self.operation, self.payload = store, table, operation, payload
        self.filters = {}

    def eq(self, key, value): self.filters[key] = str(value); return self
    def order(self, *args, **kwargs): return self
    def execute(self):
        rows = self.store.setdefault(self.table, [])
        matches = lambda row: all(str(row.get(key)) == value for key, value in self.filters.items())
        if self.operation == "select": return Response([dict(row) for row in rows if matches(row)])
        if self.operation == "delete":
            self.store[self.table] = [row for row in rows if not matches(row)]
            return Response([])
        if self.operation == "update":
            changed = []
            for row in rows:
                if matches(row): row.update(self.payload); changed.append(dict(row))
            return Response(changed)
        row = dict(self.payload)
        if self.table == "moodboards": row.setdefault("id", str(uuid4()))
        if self.table == "moodboard_items": row.setdefault("id", str(uuid4()))
        conflict = {
            "user_profiles": ("user_id",),
            "user_preferences": ("user_id",),
            "saved_products": ("user_id", "product_id"),
            "saved_brands": ("user_id", "brand_id"),
        }.get(self.table, ())
        existing = next((item for item in rows if conflict and all(item.get(k) == row.get(k) for k in conflict)), None)
        if existing: existing.update(row); row = existing
        else: rows.append(row)
        return Response([dict(row)])


class Table:
    def __init__(self, store, name): self.store, self.name = store, name
    def select(self, columns="*"): return Query(self.store, self.name, "select")
    def insert(self, payload): return Query(self.store, self.name, "insert", payload)
    def upsert(self, payload, on_conflict=None): return Query(self.store, self.name, "upsert", payload)
    def update(self, payload): return Query(self.store, self.name, "update", payload)
    def delete(self): return Query(self.store, self.name, "delete")


class FakeClient:
    def __init__(self): self.rows = {}
    def table(self, name): return Table(self.rows, name)


class FakeAuth:
    def __init__(self, user_id): self.user_id = str(user_id)
    def get_user(self, token):
        if token != "good-token":
            raise ValueError("invalid token")
        return SimpleNamespace(user=SimpleNamespace(id=self.user_id))


def request(client, user_id):
    auth_client = SimpleNamespace(auth=FakeAuth(user_id))
    return SimpleNamespace(
        headers={"authorization": "Bearer good-token"},
        app=SimpleNamespace(state=SimpleNamespace(personalization=client, supabase=auth_client)),
    )


def test_product_and_brand_save_round_trip():
    client, profile, product, brand = FakeClient(), uuid4(), uuid4(), uuid4()
    req = request(client, profile)
    assert save_product(product, req)["saved"] is True
    assert save_brand(brand, req)["saved"] is True
    state = get_personalization(req)
    assert state["saved_product_ids"] == [str(product)]
    assert state["saved_brand_ids"] == [str(brand)]
    assert unsave_product(product, req)["saved"] is False
    assert unsave_brand(brand, req)["saved"] is False
    assert get_personalization(req)["saved_product_ids"] == []


def test_moodboard_create_add_remove_and_delete():
    client, profile, product = FakeClient(), uuid4(), uuid4()
    req = request(client, profile)
    board_id = uuid4()
    created = create_moodboard(MoodboardCreate(id=board_id, name="Silver minimal", description="Jewellery"), req)
    assert created["moodboard"]["title"] == "Silver minimal"
    assert add_moodboard_product(board_id, ItemPayload(item_id=product), req)["updated"]
    assert get_personalization(req)["moodboards"][0]["items"][0]["product_id"] == str(product)
    assert remove_moodboard_product(board_id, ItemPayload(item_id=product), req)["updated"]
    assert delete_moodboard(board_id, req)["deleted"] is True


def test_personalization_requires_authentication():
    client, product = FakeClient(), uuid4()
    req = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(personalization=client, supabase=SimpleNamespace(auth=FakeAuth(uuid4())))),
    )
    try:
        save_product(product, req)
        assert False, "expected an auth error"
    except Exception as error:
        assert getattr(error, "status_code", None) == 401


def test_personalization_is_an_additional_ranking_signal():
    products = [
        {"id": "a", "brand_id": "saved", "product_name": "Silver ring", "category": "Jewellery", "similarity": .70, "niche_score": .5},
        {"id": "b", "brand_id": "other", "product_name": "Silver ring", "category": "Jewellery", "similarity": .70, "niche_score": .5},
    ]
    ranked = apply_final_ranking(
        products, category_id=None, sort_by=None, search_mode="text",
        query_attributes={}, personalization_context={"preferences": {}, "saved_brand_ids": {"saved"}},
    )
    assert ranked[0]["id"] == "a"
    assert ranked[0]["score_breakdown"]["personalization_match"] == 1
