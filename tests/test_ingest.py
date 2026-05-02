"""ingest: normalize() + INSERT ON CONFLICT DO NOTHING into pipeline.words."""

from __future__ import annotations

import sqlalchemy as sa

from wordforge.ingest import IngestResult, ingest_words, normalize


def test_normalize_single_word():
    assert normalize("  Apple  ") == ("apple", 1)


def test_normalize_phrase_by_whitespace():
    assert normalize("Pick Up") == ("pick up", 2)


def test_normalize_preserves_internal_hyphen_as_word():
    """Hyphenated compound (no whitespace) stays type=1."""
    assert normalize("well-known") == ("well-known", 1)


def test_normalize_empty_returns_none():
    assert normalize("   ") is None
    assert normalize("") is None


def test_ingest_inserts_new_rows(at_head):
    res = ingest_words(at_head, raw_forms=["apple", "banana", "pick up"])
    assert isinstance(res, IngestResult)
    assert res.inserted == 3
    assert res.deduped == 0
    assert res.skipped_empty == 0
    with at_head.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT normalized_form, type FROM pipeline.words ORDER BY normalized_form")
        ).all()
    assert rows == [("apple", 1), ("banana", 1), ("pick up", 2)]


def test_ingest_dedupes_on_conflict(at_head):
    ingest_words(at_head, raw_forms=["apple"])
    res = ingest_words(at_head, raw_forms=["Apple", "APPLE", "banana"])
    assert res.inserted == 1  # only banana is new
    assert res.deduped == 2  # two "apple" variants collapsed
    with at_head.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.words WHERE normalized_form = 'apple'")
        ).scalar()
    assert n == 1


def test_ingest_preserves_first_raw_form(at_head):
    """spec §4 line 206: raw_form is audit-only, stores the first raw seen."""
    ingest_words(at_head, raw_forms=["  Apple  "])
    ingest_words(at_head, raw_forms=["APPLE"])
    with at_head.connect() as conn:
        raw = conn.execute(
            sa.text("SELECT raw_form FROM pipeline.words WHERE normalized_form = 'apple'")
        ).scalar()
    assert raw == "  Apple  "


def test_ingest_skips_empty_lines(at_head):
    res = ingest_words(at_head, raw_forms=["apple", "", "  ", "banana"])
    assert res.inserted == 2
    assert res.skipped_empty == 2


def test_ingest_with_batch_id_auto_creates_batch(at_head):
    """ingest_words(batch_id='B1') must auto-create the batch row
    (ON CONFLICT DO NOTHING), so users don't need a separate step."""
    ingest_words(at_head, raw_forms=["apple"], batch_id="B1")
    with at_head.connect() as conn:
        batch = conn.execute(
            sa.text("SELECT batch_id FROM pipeline.words WHERE normalized_form = 'apple'")
        ).scalar()
        batch_row = conn.execute(
            sa.text("SELECT id FROM pipeline.batches WHERE id = 'B1'")
        ).scalar()
    assert batch == "B1"
    assert batch_row == "B1"


def test_ingest_batch_idempotent_across_calls(at_head):
    """Two ingest_words calls with the same batch_id must not fail on the
    second batch INSERT (ON CONFLICT DO NOTHING handles concurrent creates)."""
    ingest_words(at_head, raw_forms=["apple"], batch_id="B1")
    ingest_words(at_head, raw_forms=["banana"], batch_id="B1")
    with at_head.connect() as conn:
        n = conn.execute(sa.text("SELECT count(*) FROM pipeline.batches WHERE id = 'B1'")).scalar()
    assert n == 1


def test_ingest_same_form_different_type_both_inserted(at_head):
    """UNIQUE(normalized_form, type): 'set' as word and 'set' as phrase coexist
    (contrived example — in practice ingest only emits one type per raw_form,
    but the DDL allows it so the test locks behavior)."""
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words (raw_form, normalized_form, type) "
                "VALUES ('set', 'set', 1), ('setting up', 'set', 2)"
            )
        )
    with at_head.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.words WHERE normalized_form = 'set'")
        ).scalar()
    assert n == 2
