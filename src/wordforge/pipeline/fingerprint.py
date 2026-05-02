"""Stage-level fingerprint (v9 spec §6).

    fingerprint = sha256(canonical_json([
        sorted(upstream_fingerprints),
        stage_config,
        prompt_version,
        prompt_content_hash,
        parser_version,
    ]))

Pure function. Does not touch DB, does not touch cache, does not read the
filesystem. Called by:
- runner, before running a stage, to compute expected_fingerprint
- runner, to compare against stage_artifacts.fingerprint for skip decision

Spec §10 #11: `code_hash` is deliberately NOT an input. A ruff format pass or
comment edit must never invalidate 10万词 of fingerprints. Behavior changes in
parser code are signalled by bumping `parser_version` in configs/default.toml
(handled by P5 stage config loading).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint(
    *,
    upstream_fingerprints: list[str],
    stage_config: dict[str, Any],
    prompt_version: str | None,
    prompt_content_hash: str | None,
    parser_version: str,
) -> str:
    # Round 3 R3-gem-5: non-LLM stages (fetch_dict/phonetic/export) have no
    # prompt; accept None and let json.dumps serialize it to null deterministically.
    parts: list[Any] = [
        sorted(upstream_fingerprints),
        stage_config,
        prompt_version,
        prompt_content_hash,
        parser_version,
    ]
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
