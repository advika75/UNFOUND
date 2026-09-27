import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import (
    ALLOWED_ORIGINS,
    DISCOVERY_FEED_CACHE,
    SEARCH_CACHE,
    _cache_get,
    _cache_put,
    _discovery_brand,
    _discovery_product,
    app,
    public_search_products,
)

ROOT = Path(__file__).resolve().parent.parent


class Response:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, rows): self.rows, self.filters = rows, {}
    def eq(self, key, value): self.filters[key] = str(value); return self
    def execute(self): return Response([r for r in self.rows if all(str(r.get(k)) == v for k, v in self.filters.items())])


class Table:
    def __init__(self, rows): self.rows = rows
    def select(self, columns="*"): return Query(self.rows)


class FakeClient:
    """Service-role stand-in holding saved products per user id."""
    def __init__(self, saved): self.saved = saved
    def table(self, name):
        return Table(self.saved if name == "saved_products" else [])


class FakeAuth:
    def __init__(self, user_id): self.user_id = str(user_id)
    def get_user(self, token):
        if token != "good-token":
            raise ValueError("invalid token")
        return SimpleNamespace(user=SimpleNamespace(id=self.user_id))


@pytest.fixture
def api(monkeypatch):
    victim, caller = uuid4(), uuid4()
    saved = [
        {"user_id": str(victim), "product_id": "victim-product"},
        {"user_id": str(caller), "product_id": "caller-product"},
    ]
    seen: list = []

    def fake_rpc(**kwargs):
        seen.append(kwargs["personalization_context"])
        return []

    monkeypatch.setattr(app_module, "encode_text", lambda model, text: [0.0] * 512)
    monkeypatch.setattr(app_module, "run_match_products_rpc", fake_rpc)
    monkeypatch.setattr(app.state, "clip_model", object(), raising=False)
    monkeypatch.setattr(app.state, "supabase", SimpleNamespace(auth=FakeAuth(caller)), raising=False)
    monkeypatch.setattr(app.state, "personalization", FakeClient(saved), raising=False)
    DISCOVERY_FEED_CACHE.clear()
    yield SimpleNamespace(client=TestClient(app), victim=victim, caller=caller, seen=seen)
    DISCOVERY_FEED_CACHE.clear()


def test_discover_ignores_supplied_profile_id_without_token(api):
    response = api.client.post("/api/discover", data={"text_query": "red dress", "profile_id": str(api.victim)})
    assert response.status_code == 200
    assert api.seen == [None]
    assert response.json()["personalized"] is False


def test_discover_ignores_supplied_profile_id_with_invalid_token(api):
    response = api.client.post(
        "/api/discover",
        data={"text_query": "red dress", "profile_id": str(api.victim)},
        headers={"Authorization": "Bearer forged"},
    )
    assert api.seen == [None]
    assert response.json()["personalized"] is False


def test_discover_uses_only_the_token_identity_even_if_profile_id_names_someone_else(api):
    response = api.client.post(
        "/api/discover",
        data={"text_query": "red dress", "profile_id": str(api.victim)},
        headers={"Authorization": "Bearer good-token"},
    )
    assert response.json()["personalized"] is True
    assert api.seen[0]["saved_product_ids"] == {"caller-product"}


def _patch_discovery(monkeypatch):
    captured: list = []
    monkeypatch.setattr(app_module, "fetch_all", lambda client, table: [])

    def fake_sections(products, brands, **kwargs):
        captured.append(kwargs["preferences"])
        keys = ("hidden_gems", "trending", "fresh_drops", "new_discoveries", "missed", "emerging_brands", "scored_products")
        return {**{k: [] for k in keys}, "trending_signal": {}, "eligible_candidates": 0}

    monkeypatch.setattr(app_module, "discovery_sections", fake_sections)
    return captured


def test_discovery_feeds_ignore_profile_id_query_param(api, monkeypatch):
    captured = _patch_discovery(monkeypatch)
    assert api.client.get(f"/api/discovery/feeds?profile_id={api.victim}").status_code == 200
    assert captured == [None]


def test_discovery_feeds_personalize_from_verified_token_only(api, monkeypatch):
    captured = _patch_discovery(monkeypatch)
    api.client.get(f"/api/discovery/feeds?profile_id={api.victim}", headers={"Authorization": "Bearer good-token"})
    assert captured[0]["saved_product_ids"] == {"caller-product"}


def test_no_endpoint_accepts_a_user_identity_field():
    for route in app.routes:
        params = {p.name for p in getattr(route, "dependant").query_params + getattr(route, "dependant").body_params} if hasattr(route, "dependant") else set()
        assert not params & {"profile_id", "user_id"}, (getattr(route, "path", route), params)


def test_cors_allows_configured_origin_only():
    assert "*" not in ALLOWED_ORIGINS
    client = TestClient(app)
    headers = {"Access-Control-Request-Method": "GET"}
    allowed = client.options("/", headers={**headers, "Origin": ALLOWED_ORIGINS[0]})
    assert allowed.headers.get("access-control-allow-origin") == ALLOWED_ORIGINS[0]
    denied = client.options("/", headers={**headers, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_cache_is_bounded_and_evicts_least_recently_used():
    cache: dict = {}
    for i in range(3):
        _cache_put(cache, i, i, max_entries=3)
    assert _cache_get(cache, 0, ttl=60) == 0  # touch 0 so 1 becomes the oldest
    _cache_put(cache, 3, 3, max_entries=3)
    assert list(cache) == [2, 0, 3]
    for i in range(100):
        _cache_put(cache, f"k{i}", i, max_entries=3)
    assert len(cache) == 3


def test_cache_expired_entries_are_dropped_on_read():
    cache: dict = {"k": (time.monotonic() - 100, "stale")}
    assert _cache_get(cache, "k", ttl=60) is None
    assert "k" not in cache


def test_real_caches_have_bounds():
    assert app_module.SEARCH_CACHE_MAX_ENTRIES > 0 and app_module.DISCOVERY_FEED_CACHE_MAX_ENTRIES > 0
    SEARCH_CACHE.clear()


def test_production_hides_all_debug_fields(monkeypatch):
    row = {"id": "1", "name": "x", "score_breakdown": {"a": 1}, "rerank_detail": {"b": 2}, "components": {"c": 1}, "gem_components": {"d": 1}}
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert "score_breakdown" in public_search_products([row], debug=True)[0]
    assert "discovery_debug" in _discovery_product(row)
    assert "gem_components" in _discovery_brand(row)

    monkeypatch.setenv("ENVIRONMENT", "production")
    exposed = public_search_products([row], debug=True)[0]
    assert "score_breakdown" not in exposed and "rerank_detail" not in exposed
    assert "discovery_debug" not in _discovery_product(row)
    assert "gem_components" not in _discovery_brand(row)


def test_env_example_ranking_weights_match_code_defaults():
    example = dict(re.findall(r"^((?:TEXT|IMAGE)_WEIGHT_\w+)=([\d.]+)", (ROOT / "backend/.env.example").read_text(), re.M))
    code = dict(re.findall(r'"((?:TEXT|IMAGE)_WEIGHT_\w+)", "([\d.]+)"', (ROOT / "backend/app.py").read_text()))
    assert code and example == code
