"""One-shot script: shift OSS package-words word_id by -999_900_000.

Spec: docs/superpowers/specs/2026-05-02-shift-oss-package-word-ids-design.md
Plan: docs/superpowers/plans/2026-05-02-shift-oss-package-word-ids.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

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
      - status="ok": new_body is the shifted JSON string. If some ids were filtered
        out (not in valid_new_ids), details contains:
          filtered_count, filtered_unique_missing, filtered_sample (up to 10 ids)
      - status="already_shifted": new_body is None, caller skips upload.
      - status="dead_letter": new_body is None; every word in the package was
        filtered out (nothing left to upload). details has filtered_unique_missing.

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

    missing: list[int] = []
    kept_total = 0
    for u in parsed:
        new_words = []
        for w in u["words"]:
            new_id = w["id"] - WORD_ID_OFFSET
            if new_id in valid_new_ids:
                w["id"] = new_id
                new_words.append(w)
            else:
                missing.append(new_id)
        u["words"] = new_words
        kept_total += len(new_words)

    unique_missing = sorted(set(missing))
    if kept_total == 0:
        return (
            None,
            "dead_letter",
            {
                "filtered_unique_missing": unique_missing,
                "note": "all word ids filtered out; nothing to upload",
            },
        )

    details: dict = {}
    if missing:
        details = {
            "filtered_count": len(missing),
            "filtered_unique_missing_count": len(unique_missing),
            "filtered_sample": unique_missing[:10],
        }

    new_body = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return new_body, "ok", details


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Shift word_id by -999_900_000 across all package objects in "
        "OSS bucket sailing-words-package-words. Idempotent.",
    )
    p.add_argument("--bak-dir", type=Path, default=Path("./bak"))
    p.add_argument(
        "--dead-letter", type=Path, default=Path("./oss_shift_dead_letter.jsonl")
    )
    p.add_argument(
        "--filter-audit",
        type=Path,
        default=Path("./oss_shift_filter_audit.jsonl"),
        help="ok packages that had some ids filtered out (方案 A decision).",
    )
    p.add_argument(
        "--i-am-writing-prod",
        action="store_true",
        help="Without this flag it is a dry run (backup + validate + transform only).",
    )
    return p.parse_args(argv)


def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"ERROR: missing env vars: {missing}. Did you source oss.env / prod.env?")
    return {n: os.environ[n] for n in names}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    args.bak_dir.mkdir(parents=True, exist_ok=True)

    env = _require_env(
        "DATABASE_URL",
        "OSS_ENDPOINT",
        "OSS_BUCKET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
    )

    _log(f"mode={'WRITE-PROD' if args.i_am_writing_prod else 'DRY-RUN'}")
    _log("stage 0: loading valid_new_ids from PG domain.words ...")
    valid_new_ids = load_valid_word_ids(env["DATABASE_URL"])
    _log(f"  loaded {len(valid_new_ids)} word ids")

    _log("stage 1: listing OSS bucket ...")
    bucket = make_bucket(
        env["OSS_ENDPOINT"],
        env["OSS_BUCKET"],
        env["OSS_ACCESS_KEY_ID"],
        env["OSS_ACCESS_KEY_SECRET"],
    )
    keys = [obj.key for obj in oss2.ObjectIterator(bucket)]
    _log(f"  found {len(keys)} objects")

    counts = {"ok": 0, "ok_with_filter": 0, "already_shifted": 0, "dead_letter": 0, "error": 0}
    with (
        args.dead_letter.open("a", encoding="utf-8") as dl_fp,
        args.filter_audit.open("a", encoding="utf-8") as fa_fp,
    ):
        for i, key in enumerate(keys, 1):
            try:
                body = bucket.get_object(key).read().decode("utf-8")
            except oss2.exceptions.OssError as e:
                _log(f"  [{key}] download failed: {e}")
                dl_fp.write(json.dumps({"package_id": key, "reason": f"download: {e}"}) + "\n")
                counts["error"] += 1
                continue

            bak_path = args.bak_dir / f"{key}.json"
            if not bak_path.exists():
                bak_path.write_text(body, encoding="utf-8")

            try:
                new_body, status, details = transform_body(body, valid_new_ids=valid_new_ids)
            except InvalidMixedIdRangeError as e:
                _log(f"  [{key}] MIXED IDS: {e}")
                dl_fp.write(json.dumps({"package_id": key, "reason": f"mixed: {e}"}) + "\n")
                counts["error"] += 1
                continue

            if status == "dead_letter":
                dl_fp.write(json.dumps({"package_id": key, **details}) + "\n")
                counts["dead_letter"] += 1
            elif status == "already_shifted":
                counts["already_shifted"] += 1
            elif status == "ok":
                if details:
                    fa_fp.write(json.dumps({"package_id": key, **details}) + "\n")
                    counts["ok_with_filter"] += 1
                else:
                    counts["ok"] += 1
                if args.i_am_writing_prod:
                    # put_object 在 Task 5 启用;这里先占位,保证 dry-run 路径干净.
                    pass

            if i % 100 == 0:
                _log(f"  progress: {i}/{len(keys)}  {counts}")

    _log(f"stage 3: summary = {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
