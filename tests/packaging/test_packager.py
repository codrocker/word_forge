"""SQLite + zip IO for the packager."""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from scripts.packaging.packager import write_sqlite, zip_db


def test_write_sqlite_creates_expected_schema(tmp_path: Path):
    db_path = tmp_path / "words.db"
    rows = [(1, '{"id":1,"form":"hello"}'), (2, '{"id":2,"form":"world"}')]
    write_sqlite(db_path, rows)

    conn = sqlite3.connect(db_path)
    try:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='word'"
        ).fetchone()[0]
        assert "word_id" in schema
        assert "word_json" in schema

        count = conn.execute("SELECT COUNT(*) FROM word").fetchone()[0]
        assert count == 2

        row = conn.execute("SELECT word_json FROM word WHERE word_id=1").fetchone()
        assert json.loads(row[0])["form"] == "hello"
    finally:
        conn.close()


def test_write_sqlite_overwrites_existing_file(tmp_path: Path):
    db_path = tmp_path / "words.db"
    db_path.write_bytes(b"garbage")  # leftover from prior run
    rows = [(42, '{"id":42}')]
    write_sqlite(db_path, rows)
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM word").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_write_sqlite_accepts_iterator(tmp_path: Path):
    db_path = tmp_path / "words.db"

    def gen():
        for i in range(10):
            yield (i, f'{{"id":{i}}}')

    write_sqlite(db_path, gen())
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM word").fetchone()[0]
        assert count == 10
    finally:
        conn.close()


def test_write_sqlite_rejects_non_string_json(tmp_path: Path):
    """Guard against accidentally writing rows where word_json is not a str."""
    db_path = tmp_path / "words.db"
    with pytest.raises((sqlite3.InterfaceError, sqlite3.ProgrammingError, TypeError)):
        write_sqlite(db_path, [(1, {"not": "a string"})])  # type: ignore[list-item]


def test_zip_db_produces_zip_with_words_db_entry(tmp_path: Path):
    src = tmp_path / "source.db"
    src.write_bytes(b"fake sqlite bytes")
    out = tmp_path / "out.zip"

    zip_db(src, out)

    assert out.exists()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert names == ["words.db"]  # single entry, fixed name
        assert z.read("words.db") == b"fake sqlite bytes"


def test_zip_db_overwrites_existing(tmp_path: Path):
    src = tmp_path / "source.db"
    src.write_bytes(b"v2")
    out = tmp_path / "out.zip"
    out.write_bytes(b"old-zip-bytes")
    zip_db(src, out)
    with zipfile.ZipFile(out) as z:
        assert z.read("words.db") == b"v2"


def test_zip_db_creates_parent_dir(tmp_path: Path):
    src = tmp_path / "source.db"
    src.write_bytes(b"x")
    out = tmp_path / "nested" / "dir" / "out.zip"
    zip_db(src, out)
    assert out.exists()
