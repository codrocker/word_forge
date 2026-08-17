"""Schema-contract meta tests: verify code assumptions match actual DB schema.

These tests query information_schema / pg_catalog to ensure FIELD_MAP, _PK_COL,
and rebuild_word_payload SQL stay in sync with the live database.
"""
import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.services.word_service import FIELD_MAP, _PK_COL


@pytest.fixture(scope="module")
def conn():
    """Single connection for all schema meta tests (read-only, no cleanup needed)."""
    e = make_engine()
    with e.connect() as c:
        yield c
    e.dispose()


class TestFieldMapColumnsExist:
    """Every (table, column) in FIELD_MAP must exist in the DB."""

    @pytest.mark.parametrize(
        "field_path,table_col",
        [(fp, (t, c)) for fp, (t, c, _) in FIELD_MAP.items()],
        ids=list(FIELD_MAP.keys()),
    )
    def test_field_map_columns_exist(self, conn, field_path, table_col):
        table, column = table_col
        schema, tname = table.split(".")
        row = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": tname, "c": column},
        ).first()
        assert row is not None, (
            f"FIELD_MAP['{field_path}'] references {table}.{column} "
            f"but column does not exist in DB"
        )


class TestPkColColumnsExist:
    """Every PK column in _PK_COL must exist in the DB."""

    @pytest.mark.parametrize(
        "table,pk_col",
        list(_PK_COL.items()),
        ids=list(_PK_COL.keys()),
    )
    def test_pk_col_columns_exist(self, conn, table, pk_col):
        schema, tname = table.split(".")
        row = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": tname, "c": pk_col},
        ).first()
        assert row is not None, (
            f"_PK_COL['{table}'] = '{pk_col}' but column does not exist in DB"
        )


class TestPkColIsPrimaryKey:
    """Every _PK_COL entry must actually be the primary key of that table."""

    @pytest.mark.parametrize(
        "table,pk_col",
        list(_PK_COL.items()),
        ids=list(_PK_COL.keys()),
    )
    def test_pk_col_is_actually_primary_key(self, conn, table, pk_col):
        schema, tname = table.split(".")
        row = conn.execute(
            text(
                "SELECT a.attname "
                "FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                "WHERE n.nspname = :s AND c.relname = :t AND i.indisprimary = true"
            ),
            {"s": schema, "t": tname},
        ).first()
        assert row is not None, (
            f"_PK_COL['{table}'] = '{pk_col}' but table has no primary key"
        )
        assert row.attname == pk_col, (
            f"_PK_COL['{table}'] = '{pk_col}' but actual PK column is '{row.attname}'"
        )


class TestRebuildWordPayloadColumns:
    """rebuild_word_payload queries specific columns; verify they exist."""

    # White-list of (schema.table, column) pairs queried in rebuild_word_payload
    _REBUILD_QUERIES = [
        # domain.words SELECT
        ("domain.words", "form"),
        ("domain.words", "type"),
        ("domain.words", "phonetic_us"),
        ("domain.words", "phonetic_uk"),
        ("domain.words", "audio_us"),
        ("domain.words", "audio_uk"),
        ("domain.words", "status"),
        ("domain.words", "quality_flag"),
        # domain.meanings SELECT
        ("domain.meanings", "meaning_id"),
        ("domain.meanings", "pos"),
        ("domain.meanings", "pos_sub"),
        ("domain.meanings", "cn_paraphrase"),
        ("domain.meanings", "en_paraphrase"),
        ("domain.meanings", "equivalents"),
        ("domain.meanings", "synonyms"),
        ("domain.meanings", "antonyms"),
        # domain.sentences SELECT
        ("domain.sentences", "sentence_id"),
        ("domain.sentences", "form"),
        ("domain.sentences", "translation"),
        # domain.mnemonics SELECT
        ("domain.mnemonics", "content"),
        ("domain.mnemonics", "type"),
        # domain.phrases SELECT
        ("domain.phrases", "phrase_id"),
        ("domain.phrases", "form"),
        ("domain.phrases", "meaning"),
        ("domain.phrases", "owner_word_id"),
        # domain.package_word SELECT
        ("domain.package_word", "package_id"),
        ("domain.package_word", "unit_id"),
        ("domain.package_word", "sort_order"),
        ("domain.package_word", "importance"),
        ("domain.package_word", "word_id"),
        # serving.word_payload UPSERT
        ("serving.word_payload", "word_id"),
        ("serving.word_payload", "form"),
        ("serving.word_payload", "type"),
        ("serving.word_payload", "payload"),
        ("serving.word_payload", "payload_schema_version"),
        ("serving.word_payload", "updated_at"),
    ]

    @pytest.mark.parametrize(
        "table_col",
        _REBUILD_QUERIES,
        ids=[f"{t}.{c}" for t, c in _REBUILD_QUERIES],
    )
    def test_rebuild_word_payload_queried_columns_exist(self, conn, table_col):
        table, column = table_col
        schema, tname = table.split(".")
        row = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": tname, "c": column},
        ).first()
        assert row is not None, (
            f"rebuild_word_payload references {table}.{column} "
            f"but column does not exist in DB"
        )


class TestNoPhantomFieldPath:
    """Every FIELD_MAP table.column must exist — no phantom references."""

    @pytest.mark.parametrize(
        "field_path",
        list(FIELD_MAP.keys()),
        ids=list(FIELD_MAP.keys()),
    )
    def test_no_phantom_field_path_in_field_map(self, conn, field_path):
        table, column, _ = FIELD_MAP[field_path]
        schema, tname = table.split(".")
        row = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": tname, "c": column},
        ).first()
        assert row is not None, (
            f"FIELD_MAP['{field_path}'] → {table}.{column} is a phantom: "
            f"column does not exist in information_schema"
        )


def test_domain_phrases_uses_owner_word_id_not_word_id(conn):
    """Pin: domain.phrases FK to words is owner_word_id, NOT word_id.

    This catches the recurring bug where someone writes domain.phrases.word_id
    (which does not exist) instead of domain.phrases.owner_word_id.
    """
    # owner_word_id MUST exist
    row = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'domain' AND table_name = 'phrases' "
            "AND column_name = 'owner_word_id'"
        ),
    ).first()
    assert row is not None, (
        "domain.phrases.owner_word_id column missing — schema drift!"
    )

    # word_id must NOT exist on domain.phrases
    bad = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'domain' AND table_name = 'phrases' "
            "AND column_name = 'word_id'"
        ),
    ).first()
    assert bad is None, (
        "domain.phrases has a 'word_id' column — this is wrong! "
        "The FK column is 'owner_word_id'. Someone may have added word_id by mistake."
    )
