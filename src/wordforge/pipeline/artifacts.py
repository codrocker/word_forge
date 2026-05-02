"""StageArtifactStore — pipeline.stage_artifacts I/O.

Thin wrapper around SQL: one row per (word_id, stage_name), UPSERT on PK.
Does NOT compute fingerprints (that's pipeline.fingerprint). Does NOT enforce
same-source invariants (that's the export stage's preflight, P5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine


@dataclass
class StageArtifactStore:
    engine: Engine

    def get(self, *, word_id: int, stage_name: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.text(
                        "SELECT word_id, stage_name, fingerprint, payload, "
                        "       source, model, prompt_version, updated_at "
                        "FROM pipeline.stage_artifacts "
                        "WHERE word_id = :w AND stage_name = :s"
                    ),
                    {"w": word_id, "s": stage_name},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def upsert(
        self,
        *,
        word_id: int,
        stage_name: str,
        fingerprint: str,
        payload: dict[str, Any] | list[Any],
        source: str,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO pipeline.stage_artifacts "
                    "(word_id, stage_name, fingerprint, payload, source, "
                    " model, prompt_version) "
                    "VALUES (:w, :s, :fp, CAST(:payload AS jsonb), :src, "
                    "        :model, :pv) "
                    "ON CONFLICT (word_id, stage_name) DO UPDATE SET "
                    "  fingerprint    = EXCLUDED.fingerprint, "
                    "  payload        = EXCLUDED.payload, "
                    "  source         = EXCLUDED.source, "
                    "  model          = EXCLUDED.model, "
                    "  prompt_version = EXCLUDED.prompt_version, "
                    "  updated_at     = now()"
                ),
                {
                    "w": word_id,
                    "s": stage_name,
                    "fp": fingerprint,
                    "payload": json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "src": source,
                    "model": model,
                    "pv": prompt_version,
                },
            )

    def should_skip(self, *, word_id: int, stage_name: str, expected_fingerprint: str) -> bool:
        """Return True iff an artifact exists AND its fingerprint matches expected.

        Spec §6 "重跑时的断点恢复"：runner 对每个 (word, stage) 算 expected；
        一致 → skip；缺失或不一致 → 从此 stage 往后串行跑。
        """
        row = self.get(word_id=word_id, stage_name=stage_name)
        return row is not None and row["fingerprint"] == expected_fingerprint
