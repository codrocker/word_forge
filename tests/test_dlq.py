"""DeadLetterStore: record / list_open / replay."""

from __future__ import annotations

import sqlalchemy as sa

from wordforge.dlq import DeadLetterStore


def _seed_word(engine, *, word_id_hint: int = 1, batch_id: str = "B1") -> int:
    """Insert a batch + word row, return the word id."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.batches (id, label) VALUES (:b, :b) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"b": batch_id},
        )
        row = conn.execute(
            sa.text(
                "INSERT INTO pipeline.words "
                "(raw_form, normalized_form, type, batch_id) "
                "VALUES (:f, :f, 1, :b) RETURNING id"
            ),
            {"f": f"word{word_id_hint}", "b": batch_id},
        )
        return row.scalar_one()


def test_record_inserts_row(at_head):
    wid = _seed_word(at_head)
    store = DeadLetterStore(at_head)
    row_id = store.record(word_id=wid, stage_name="paraphrase", error="boom", attempt=3)
    assert row_id > 0

    with at_head.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT word_id, stage_name, error, attempt "
                    "FROM pipeline.dead_letter WHERE id = :id"
                ),
                {"id": row_id},
            )
            .mappings()
            .first()
        )
    assert row["word_id"] == wid
    assert row["stage_name"] == "paraphrase"
    assert row["error"] == "boom"
    assert row["attempt"] == 3


def test_list_open_filters_resolved(at_head):
    wid = _seed_word(at_head)
    store = DeadLetterStore(at_head)
    store.record(word_id=wid, stage_name="s1", error="e1", attempt=3)
    store.record(word_id=wid, stage_name="s2", error="e2", attempt=4)

    # Resolve one
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE pipeline.dead_letter SET resolved_at = now() "
                "WHERE word_id = :w AND stage_name = 's1'"
            ),
            {"w": wid},
        )

    open_rows = store.list_open()
    assert len(open_rows) == 1
    assert open_rows[0].stage_name == "s2"


def test_replay_marks_resolved_and_resets_word(at_head):
    wid = _seed_word(at_head)
    store = DeadLetterStore(at_head)
    store.record(word_id=wid, stage_name="s1", error="e1", attempt=3)
    store.record(word_id=wid, stage_name="s2", error="e2", attempt=4)

    # Set word status to 'failed' so we can verify reset
    with at_head.begin() as conn:
        conn.execute(
            sa.text("UPDATE pipeline.words SET status = 'failed' WHERE id = :w"),
            {"w": wid},
        )

    n = store.replay(word_id=wid)
    assert n == 2

    # Both rows resolved
    assert store.list_open() == []

    # Word status reset to 'new'
    with at_head.connect() as conn:
        status = conn.execute(
            sa.text("SELECT status FROM pipeline.words WHERE id = :w"),
            {"w": wid},
        ).scalar()
    assert status == "new"


def test_replay_no_open_letters_returns_0(at_head):
    wid = _seed_word(at_head)
    store = DeadLetterStore(at_head)
    n = store.replay(word_id=wid)
    assert n == 0


def test_replay_does_not_touch_other_words(at_head):
    wid1 = _seed_word(at_head, word_id_hint=1)
    wid2 = _seed_word(at_head, word_id_hint=2)
    store = DeadLetterStore(at_head)
    store.record(word_id=wid1, stage_name="s1", error="e1", attempt=3)
    store.record(word_id=wid2, stage_name="s1", error="e2", attempt=3)

    store.replay(word_id=wid1)

    open_rows = store.list_open()
    assert len(open_rows) == 1
    assert open_rows[0].word_id == wid2


def test_list_open_respects_limit(at_head):
    wid = _seed_word(at_head)
    store = DeadLetterStore(at_head)
    for i in range(5):
        store.record(word_id=wid, stage_name=f"s{i}", error="e", attempt=3)

    rows = store.list_open(limit=2)
    assert len(rows) == 2
