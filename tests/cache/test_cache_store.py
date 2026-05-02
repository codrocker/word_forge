"""CacheStore: get / put / prune on pipeline.external_call_cache."""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa

from wordforge.cache import CacheStore


def test_get_returns_none_when_missing(at_head):
    store = CacheStore(at_head)
    assert store.get("llm:claude-opus", "no-such-key") is None


def test_put_then_get_roundtrip(at_head):
    store = CacheStore(at_head)
    store.put(
        kind="llm:claude-opus",
        cache_key="abc123",
        response={"text": "hello"},
        cost_usd=0.0042,
    )
    row = store.get("llm:claude-opus", "abc123")
    assert row is not None
    assert row["response"] == {"text": "hello"}
    # cost_usd comes back as Decimal from NUMERIC(10,6); cast to float for approx
    assert float(row["cost_usd"]) == pytest.approx(0.0042)
    assert row["kind"] == "llm:claude-opus"


def test_put_is_idempotent(at_head):
    """Same cache_key twice: second put wins silently (no IntegrityError)."""
    store = CacheStore(at_head)
    store.put(kind="llm:claude-opus", cache_key="k1", response={"v": 1}, cost_usd=0.01)
    store.put(kind="llm:claude-opus", cache_key="k1", response={"v": 2}, cost_usd=0.02)
    row = store.get("llm:claude-opus", "k1")
    assert row["response"] == {"v": 2}
    assert float(row["cost_usd"]) == pytest.approx(0.02)


def test_prune_removes_old_rows(at_head):
    store = CacheStore(at_head)
    # Insert two rows: one with created_at = now()-40d, one with created_at = now()
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.external_call_cache "
                "(cache_key, kind, response, cost_usd, created_at) "
                "VALUES ('old', 'llm:x', '{}'::jsonb, 0.01, now() - interval '40 days')"
            )
        )
    store.put(kind="llm:x", cache_key="new", response={}, cost_usd=0.01)

    n = store.prune(older_than=timedelta(days=30))
    assert n == 1

    assert store.get("llm:x", "old") is None
    assert store.get("llm:x", "new") is not None


def test_prune_zero_when_nothing_old(at_head):
    store = CacheStore(at_head)
    store.put(kind="llm:x", cache_key="fresh", response={}, cost_usd=0.01)
    assert store.prune(older_than=timedelta(days=30)) == 0
