"""Unit tests for scripts/packaging/shift_oss_package_word_ids.py.

Tests the pure `transform_body` function. No real OSS/PG; fixtures only.
"""

from __future__ import annotations

import json

import pytest

from scripts.packaging.shift_oss_package_word_ids import (
    WORD_ID_OFFSET,
    InvalidMixedIdRangeError,
    transform_body,
)


def _body(words_per_unit: list[list[int]]) -> str:
    """Build a minimal JSON body with the given word ids (flat lists per unit)."""
    return json.dumps(
        [
            {
                "id": 10 + i,
                "title": f"unit {i}",
                "words": [{"id": wid, "weight": 0} for wid in ids],
            }
            for i, ids in enumerate(words_per_unit)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_transform_ok_all_original_ids():
    valid = {100003, 100063, 103995}
    body = _body([[1_000_000_003, 1_000_000_063], [1_000_003_995]])

    new_body, status, details = transform_body(body, valid_new_ids=valid)

    assert status == "ok"
    assert details == {}
    parsed = json.loads(new_body)
    got_ids = [w["id"] for u in parsed for w in u["words"]]
    assert got_ids == [100003, 100063, 103995]
    assert [u["id"] for u in parsed] == [10, 11]
    assert [u["title"] for u in parsed] == ["unit 0", "unit 1"]
    assert all(w["weight"] == 0 for u in parsed for w in u["words"])
