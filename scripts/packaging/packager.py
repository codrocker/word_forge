"""SQLite + zip IO for the sailing words packager.

Spec §7. Uses stdlib sqlite3 + zipfile only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

# TODO(spec §13): runtime pragma tuning (VACUUM / page_size / journal_mode=DELETE)
# pending flutter-side startup-cost measurements.
_CREATE_TABLE = """
CREATE TABLE word (
  word_id INTEGER PRIMARY KEY,
  word_json TEXT NOT NULL
)
"""

_INSERT = "INSERT INTO word (word_id, word_json) VALUES (?, ?)"


def write_sqlite(
    db_path: Path, rows: Iterable[tuple[int, str]], *, batch_size: int = 5000
) -> int:
    """Write (word_id, word_json) tuples into a fresh SQLite file.

    Overwrites any existing file at db_path. Returns the number of rows inserted.
    """
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # Bulk-insert only pragmas (do NOT ship to flutter — TODO in spec §13)
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute(_CREATE_TABLE)

        total = 0
        batch: list[tuple[int, str]] = []
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                conn.executemany(_INSERT, batch)
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(_INSERT, batch)
            total += len(batch)
        conn.commit()
        return total
    finally:
        conn.close()
