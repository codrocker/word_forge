"""Cache layer for pipeline.external_call_cache.

This module owns two things that belong together:

1. `CacheStore` — dumb DB I/O (get/put/prune). Stores RAW external API
   responses (LLM JSON / dict HTML blobs) in JSONB. Parser output belongs
   in pipeline.stage_artifacts, never here.

2. `canonical_cache_key(...)` — the one place cache keys are computed.
   Called by LLMClient and every SourceClient so the hash is consistent
   across providers. Follows v9 spec §6: sha256 of canonical JSON of
   [kind, model, request_params, rendered_prompt, input_payload].

NOTE ON created_at SEMANTICS: on UPSERT we reset created_at to now()
because every `put()` reflects a fresh external response (cache miss or
explicit --bypass-cache re-fetch). `cache prune --older-than 30d` means
"delete rows whose LAST STORED response is older than 30d", which is the
intuitive TTL for this table.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine


def canonical_cache_key(
    *,
    kind: str,
    model: str,
    request_params: dict[str, Any],
    rendered_prompt: str,
    input_payload: dict[str, Any],
) -> str:
    """Sha256 hex of canonical JSON.

    Components: [kind, model, request_params, rendered_prompt, input_payload].

    v9 spec §6 Round 3 D1 battle: parser_version intentionally excluded —
    parser is zero-cost local logic that must not trigger external re-calls.
    """
    parts: list[Any] = [kind, model, request_params, rendered_prompt, input_payload]
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class CacheStore:
    engine: Engine

    def get(self, kind: str, cache_key: str) -> dict[str, Any] | None:
        """Return {'kind','response','cost_usd','created_at'} or None.

        The `AND kind = :kind` filter is redundant under the current cache_key
        formula (kind is hashed into cache_key, so PK collision across different
        kinds is impossible). It's kept as a fail-closed guard: if someone ever
        bypasses the CacheStore (e.g. hand-written SQL inserts) or the hash
        formula changes without updating all callers, we return None rather
        than a row whose kind doesn't match what the caller expected.
        """
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.text(
                        "SELECT kind, response, cost_usd, created_at "
                        "FROM pipeline.external_call_cache "
                        "WHERE cache_key = :k AND kind = :kind"
                    ),
                    {"k": cache_key, "kind": kind},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def put(
        self,
        *,
        kind: str,
        cache_key: str,
        response: dict[str, Any] | list[Any],
        cost_usd: float,
    ) -> None:
        """UPSERT: second call with same cache_key silently overwrites.

        created_at is reset to now() on overwrite — see module docstring.
        """
        with self.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO pipeline.external_call_cache "
                    "(cache_key, kind, response, cost_usd) "
                    "VALUES (:k, :kind, CAST(:resp AS jsonb), :cost) "
                    "ON CONFLICT (cache_key) DO UPDATE SET "
                    "  response = EXCLUDED.response, "
                    "  cost_usd = EXCLUDED.cost_usd, "
                    "  created_at = now()"
                ),
                {
                    "k": cache_key,
                    "kind": kind,
                    "resp": _json_dumps(response),
                    "cost": cost_usd,
                },
            )

    def prune(self, *, older_than: timedelta) -> int:
        """Delete rows with created_at < now() - older_than. Returns row count."""
        cutoff = datetime.now(tz=UTC) - older_than
        with self.engine.begin() as conn:
            r = conn.execute(
                sa.text("DELETE FROM pipeline.external_call_cache WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            )
            return r.rowcount or 0


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
