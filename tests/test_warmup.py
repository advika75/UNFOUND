import logging
import sys
import types

import pytest

import backend.app as app_module
from backend.app import warm_up_search_path


class Emb(list):
    def tolist(self):
        return list(self)


class Model:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def encode(self, text, normalize_embeddings=True):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("model exploded")
        return Emb([0.1] * 512)


class Query:
    def __init__(self, owner):
        self.owner = owner

    def select(self, columns):
        return self

    def limit(self, count):
        return self

    def execute(self):
        self.owner.log.append("table")
        if self.owner.fail_table:
            raise ConnectionError("network down")


class Rpc:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    def execute(self):
        self.owner.log.append(self.name)
        if self.name in self.owner.failing_rpcs:
            raise ConnectionError(f"{self.name} down")


class Supabase:
    def __init__(self, fail_table=False, failing_rpcs=()):
        self.fail_table, self.failing_rpcs, self.log = fail_table, set(failing_rpcs), []

    def table(self, name):
        return Query(self)

    def rpc(self, name, payload):
        self.payload = getattr(self, "payload", {})
        self.payload[name] = payload
        return Rpc(self, name)


def test_warms_encode_connection_and_first_calls_of_the_search_rpcs():
    model, supabase = Model(), Supabase()
    timings = warm_up_search_path(model, supabase)
    assert model.calls == ["warm-up"]
    assert sorted(set(supabase.log)) == ["exact_type_products", "match_products", "table"]
    assert len(supabase.payload["match_products"]["query_embedding"]) == 512
    assert supabase.payload["match_products"]["match_count"] == 1
    assert {"clip_encode_ms", "match_products_ms", "supabase_connect_ms", "exact_type_products_ms", "total_ms"} <= set(timings)


def test_lexical_rpc_is_warmed_only_in_hybrid_mode():
    plain, hybrid = Supabase(), Supabase()
    warm_up_search_path(Model(), plain)
    warm_up_search_path(Model(), hybrid, hybrid=True)
    assert "lexical_search_products" not in plain.log
    assert "lexical_search_products" in hybrid.log


def test_second_personalized_client_is_warmed_when_present():
    main, personalized = Supabase(), Supabase()
    timings = warm_up_search_path(Model(), main, personalization=personalized)
    assert personalized.log == ["table"]
    assert "personalization_connect_ms" in timings
    assert "personalization_connect_ms" not in warm_up_search_path(Model(), Supabase())


def test_encode_failure_is_logged_skips_dependent_rpc_but_other_steps_still_run(caplog):
    supabase = Supabase()
    with caplog.at_level(logging.ERROR):
        timings = warm_up_search_path(Model(fail=True), supabase)
    assert "match_products" not in supabase.log  # needs the embedding
    assert "table" in supabase.log and "exact_type_products" in supabase.log
    assert "clip_encode_ms" not in timings and "supabase_connect_ms" in timings
    assert "clip_encode" in caplog.text


def test_one_rpc_failing_does_not_stop_the_others(caplog):
    supabase = Supabase(failing_rpcs={"match_products"})
    with caplog.at_level(logging.ERROR):
        timings = warm_up_search_path(Model(), supabase)
    assert "exact_type_products_ms" in timings and "supabase_connect_ms" in timings
    assert "match_products_ms" not in timings


def test_everything_failing_never_raises():
    supabase = Supabase(fail_table=True, failing_rpcs={"match_products", "exact_type_products", "lexical_search_products"})
    timings = warm_up_search_path(Model(fail=True), supabase, personalization=Supabase(fail_table=True), hybrid=True)
    assert list(timings) == ["total_ms"]


def test_lifespan_starts_even_if_the_warm_up_itself_blows_up(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    class FakeST:
        def __init__(self, name):
            pass

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:1")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(app_module, "create_supabase_client", lambda url, key: object())

    def explode(*args, **kwargs):
        raise RuntimeError("warm-up bug")

    monkeypatch.setattr(app_module, "warm_up_search_path", explode)
    with TestClient(app_module.app) as client:
        assert client.get("/openapi.json").status_code == 200
