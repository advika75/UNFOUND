import threading
from dataclasses import dataclass
from typing import Any

import pytest

from backend.app import fetch_hybrid_candidates
from backend.search.fusion import rrf_fuse


@dataclass
class Resp:
    data: Any


def row(rid, sim):
    return {"id": rid, "brand_id": "b", "brand_name": "B", "category": "Tops", "item_name": rid, "description": "",
            "image_url": "", "product_url": "", "price": 1.0, "source": "s", "niche_score": 0.5, "likes_count": 0,
            "comments_count": 0, "source_hashtag": "", "scraped_at": "", "brand_follower_count": 1, "brand_niche_score": 0.5,
            "brand_profile_picture_url": "", "brand_instagram_profile_url": "", "category_id": 1, "similarity": sim}


class Client:
    """rpc() returns objects whose execute() blocks at a 2-party barrier when barrier is set:
    the barrier only releases if BOTH retrievals are in flight at the same moment."""

    def __init__(self, vector_rows, lexical_rows, barrier=None, vector_error=None, lexical_error=None):
        self.vector_rows, self.lexical_rows, self.barrier = vector_rows, lexical_rows, barrier
        self.vector_error, self.lexical_error, self.called = vector_error, lexical_error, []

    def rpc(self, name, payload):
        self.called.append(name)
        client = self

        class Call:
            def execute(self):
                if client.barrier is not None:
                    client.barrier.wait(timeout=3)
                error = client.vector_error if name == "match_products" else client.lexical_error
                if error:
                    raise error
                return Resp(client.vector_rows if name == "match_products" else client.lexical_rows)

        return Call()


V = [row("v1", 0.9), row("v2", 0.8), row("shared", 0.7)]
L = [row("shared", 2.0), row("l1", 1.5)]


def fetch(client):
    return fetch_hybrid_candidates(client, [0.0] * 512, "top", None)


def test_both_retrievals_are_in_flight_at_the_same_time():
    # Sequential code would block forever on the first barrier.wait and raise BrokenBarrierError.
    client = Client(V, L, barrier=threading.Barrier(2))
    assert {r["id"] for r in fetch(client)} == {"v1", "v2", "shared", "l1"}


def test_fusion_receives_exactly_the_same_two_ranked_lists():
    merged = fetch(Client(V, L))
    expected = rrf_fuse([["v1", "v2", "shared"], ["shared", "l1"]], k=60)
    assert [r["id"] for r in merged] == [pid for pid, _ in expected]


def test_vector_failure_raises_the_vector_error_like_the_sequential_version():
    client = Client(V, L, vector_error=RuntimeError("vector down"))
    with pytest.raises(RuntimeError, match="vector down"):
        fetch(client)


def test_lexical_failure_raises_the_lexical_error_like_the_sequential_version():
    client = Client(V, L, lexical_error=ConnectionError("lexical down"))
    with pytest.raises(ConnectionError, match="lexical down"):
        fetch(client)


def test_when_both_fail_the_vector_error_wins_as_it_did_when_it_ran_first():
    client = Client(V, L, vector_error=RuntimeError("vector down"), lexical_error=ConnectionError("lexical down"))
    with pytest.raises(RuntimeError, match="vector down"):
        fetch(client)


def test_errors_are_never_swallowed_into_a_degraded_result():
    with pytest.raises(Exception):
        fetch(Client(V, L, lexical_error=ValueError("x")))
