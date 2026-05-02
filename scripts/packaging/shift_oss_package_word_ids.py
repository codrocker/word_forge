"""One-shot script: shift OSS package-words word_id by -999_900_000.

Spec: docs/superpowers/specs/2026-05-02-shift-oss-package-word-ids-design.md
Plan: docs/superpowers/plans/2026-05-02-shift-oss-package-word-ids.md
"""

from __future__ import annotations

import json
import os
import sys
import time

import oss2
from sqlalchemy import create_engine, text

# ruff: noqa: E501 — long prompt/log strings for readability

# 和 scripts/mirror_momo_packages.py::WORDFORGE_WORD_ID_SHIFT /
# words_core/scripts/migrate_two_packages/migrate.py::WORD_ID_OFFSET 一致。
WORD_ID_OFFSET = 999_900_000

_THRESHOLD = 10**9  # 原始 id >= 10^9,已 shift id < 10^9


class InvalidMixedIdRangeError(ValueError):
    """同一 package 内同时出现原始(10^9+) 和已 shift(10^5) 两种 id — 异常数据。"""


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_valid_word_ids(database_url: str) -> set[int]:
    """Load all word_id from domain.words into memory."""
    engine = create_engine(database_url, future=True)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT word_id FROM domain.words")).scalars().all()
    engine.dispose()
    return set(rows)


def make_bucket(endpoint: str, bucket_name: str, ak: str, sk: str) -> oss2.Bucket:
    """Construct an oss2.Bucket client."""
    auth = oss2.Auth(ak, sk)
    return oss2.Bucket(auth, endpoint, bucket_name)


def transform_body(
    raw_body: str, *, valid_new_ids: set[int]
) -> tuple[str | None, str, dict]:
    """Transform one OSS object body.

    Returns (new_body, status, details):
      - status="ok": new_body is the shifted JSON string
      - status="already_shifted": new_body is None, caller skips upload
      - status="dead_letter": new_body is None, details has missing_new_ids + source_old_ids

    Raises InvalidMixedIdRangeError when the body has a mix of 10^9+ and 10^5 ids.
    """
    parsed = json.loads(raw_body)
    all_ids = [w["id"] for u in parsed for w in u["words"]]
    if not all_ids:
        return raw_body, "ok", {}

    has_original = any(i >= _THRESHOLD for i in all_ids)
    has_shifted = any(i < _THRESHOLD for i in all_ids)
    if has_original and has_shifted:
        raise InvalidMixedIdRangeError(
            f"mixed id range: min={min(all_ids)} max={max(all_ids)}"
        )

    if not has_original:
        return None, "already_shifted", {}

    new_ids = [i - WORD_ID_OFFSET for i in all_ids]
    missing = sorted({n for n in new_ids if n not in valid_new_ids})
    if missing:
        return (
            None,
            "dead_letter",
            {
                "missing_new_ids": missing,
                "source_old_ids": [m + WORD_ID_OFFSET for m in missing],
            },
        )

    for u in parsed:
        for w in u["words"]:
            w["id"] -= WORD_ID_OFFSET

    new_body = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return new_body, "ok", {}
