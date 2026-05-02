"""Ingest raw words into pipeline.words (v9 spec §5).

normalize() is a pure sync function; ingest_words() runs it on each raw_form
and does one multi-row INSERT ON CONFLICT DO NOTHING per call. No async, no
batching beyond "one call = one transaction".

Not a pipeline stage (spec §5 Round 2 battle): normalize runs here so that
every row landing in pipeline.words already has a populated normalized_form,
removing the window where Stage 1 would concurrently UPDATE normalized_form
and collide on UNIQUE(normalized_form, type).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine


def normalize(raw_form: str) -> tuple[str, int] | None:
    """strip + casefold. Phrase if contains whitespace, else single word.

    Returns None for empty / whitespace-only input (caller skips)."""
    stripped = raw_form.strip()
    if not stripped:
        return None
    normalized = stripped.casefold()
    type_ = 2 if any(ch.isspace() for ch in normalized) else 1
    return normalized, type_


@dataclass
class IngestResult:
    inserted: int
    deduped: int
    skipped_empty: int


def ingest_words(
    engine: Engine,
    *,
    raw_forms: list[str],
    batch_id: str | None = None,
) -> IngestResult:
    """Insert normalized (raw_form, normalized_form, type[, batch_id]) rows.

    Each raw_form gets normalized; empty lines are skipped and counted.
    Existing (normalized_form, type) rows are left untouched (spec §4:
    raw_form is audit-only, first-seen wins via ON CONFLICT DO NOTHING).
    Returns counts per outcome.

    Notes (Round 2 R2-U-arch-2 + R2-U-gem-3):
    - `raw_form` retained for a given normalized key depends on the order
      of `raw_forms`: within a single multi-row INSERT, PG's ON CONFLICT
      keeps the first conflicting row. Callers should not pre-shuffle or
      `set(raw_forms)` — that would change which raw gets archived.
    - No in-memory pre-dedup: 100% duplicate workloads push every row to
      PG. PG unique-index probes at ~2-5 us/row are cheap enough for the
      single-machine 10k~100k word scale; saving the ~0.1-0.4s by adding
      a Python `seen` set is not worth the extra state + the need to
      document which raw_form wins.
    """
    skipped_empty = 0
    to_insert: list[dict] = []
    for raw in raw_forms:
        norm = normalize(raw)
        if norm is None:
            skipped_empty += 1
            continue
        normalized, type_ = norm
        to_insert.append(
            {
                "raw": raw,
                "norm": normalized,
                "type": type_,
                "batch": batch_id,
            }
        )

    if not to_insert:
        return IngestResult(inserted=0, deduped=0, skipped_empty=skipped_empty)

    # Chunked single-statement multi-row INSERT (Round 1 battle B):
    #  - 4 params per row (raw, norm, type, batch). We chunk at 5000 rows
    #    (20000 params) — a conservative limit that stays comfortably within
    #    psycopg's practical parameter-per-statement bounds and keeps each
    #    statement parse/bind/execute cheap.
    #  - rowcount on a single multi-row INSERT is the true inserted count
    #    (not PEP 249 executemany-last-row-only semantics).
    CHUNK = 5000
    inserted = 0
    with engine.begin() as conn:
        if batch_id is not None:
            conn.execute(
                sa.text(
                    "INSERT INTO pipeline.batches (id, label) "
                    "VALUES (:b, :b) ON CONFLICT (id) DO NOTHING"
                ),
                {"b": batch_id},
            )

        for start in range(0, len(to_insert), CHUNK):
            chunk = to_insert[start : start + CHUNK]
            placeholders = ",".join(
                f"(:raw{i}, :norm{i}, :type{i}, :batch{i})"
                for i in range(len(chunk))
            )
            params: dict = {}
            for i, r in enumerate(chunk):
                params[f"raw{i}"] = r["raw"]
                params[f"norm{i}"] = r["norm"]
                params[f"type{i}"] = r["type"]
                params[f"batch{i}"] = r["batch"]
            result = conn.execute(
                sa.text(
                    "INSERT INTO pipeline.words "
                    "(raw_form, normalized_form, type, batch_id) VALUES "
                    + placeholders
                    + " ON CONFLICT (normalized_form, type) DO NOTHING"
                ),
                params,
            )
            inserted += result.rowcount or 0

    # deduped = proposed but not inserted (either already existed in DB or
    # the same call had the same (normalized_form, type) twice). Single
    # scalar — CLI prints it, tests assert against it.
    deduped = len(to_insert) - inserted

    return IngestResult(
        inserted=inserted,
        deduped=deduped,
        skipped_empty=skipped_empty,
    )
