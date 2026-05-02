"""YoudaoClient: uses httpx.MockTransport; cache miss hits HTTP once, hit skips."""

from __future__ import annotations

import httpx

from wordforge.cache import CacheStore
from wordforge.sources.youdao import YoudaoClient


def _make_transport(call_counter: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        call_counter["n"] = call_counter.get("n", 0) + 1
        # Simulate Youdao jsonapi JSON response.
        return httpx.Response(
            200,
            json={
                "simple": {"word": [{"usphone": "ˈæp(ə)l", "return-phrase": "apple"}]},
                "ec": {"word": [{"trs": [{"tr": [{"l": {"i": ["n. 苹果"]}}]}]}]},
            },
        )

    return httpx.MockTransport(handler)


def test_youdao_fetch_miss_then_hit(at_head):
    store = CacheStore(at_head)
    counter: dict = {}
    http = httpx.Client(transport=_make_transport(counter), base_url="http://mock")
    client = YoudaoClient(store=store, http=http)

    r1 = client.fetch("apple")
    r2 = client.fetch("apple")

    assert r1 == r2
    assert "raw_json" in r1
    assert "simple" in r1["raw_json"]
    assert counter["n"] == 1


def test_youdao_different_word_makes_separate_call(at_head):
    store = CacheStore(at_head)
    counter: dict = {}
    http = httpx.Client(transport=_make_transport(counter), base_url="http://mock")
    client = YoudaoClient(store=store, http=http)

    client.fetch("apple")
    client.fetch("banana")
    assert counter["n"] == 2


def test_youdao_bypass_cache(at_head):
    store = CacheStore(at_head)
    counter: dict = {}
    http = httpx.Client(transport=_make_transport(counter), base_url="http://mock")
    client = YoudaoClient(store=store, http=http)

    client.fetch("apple")
    client.fetch("apple", bypass_cache=True)
    assert counter["n"] == 2
