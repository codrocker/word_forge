"""FetchDictStage — wrap YoudaoClient, write raw_json into stage_artifacts.

Zero parser here. Parsing into structured meanings is `paraphrase` stage's
job (P5b) using raw_json as upstream artifact. P5a only ensures raw_json is
in the DB so downstream stages can consume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from wordforge.pipeline.fingerprint import fingerprint
from wordforge.pipeline.protocols import StagePayload

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from wordforge.config import StageConfig
    from wordforge.pipeline.artifacts import StageArtifactStore


@dataclass
class FetchDictStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    client: Any = None
    name: str = field(default="fetch_dict", init=False)

    def __post_init__(self) -> None:
        if self.client is None:
            import json
            import os

            stub_json = os.environ.get("WORDFORGE_STUB_YOUDAO_JSON")
            if stub_json:

                class _StubClient:
                    def __init__(self, payload: dict) -> None:
                        self._payload = payload

                    def fetch(self, word: str) -> dict:  # noqa: ARG002
                        return {"raw_json": self._payload}

                self.client = _StubClient(json.loads(stub_json))
                return

            import httpx

            from wordforge.cache import CacheStore
            from wordforge.sources.youdao import YoudaoClient

            base_url = os.environ.get("WORDFORGE_YOUDAO_BASE", "https://dict.youdao.com")
            http = httpx.Client(
                base_url=base_url,
                timeout=15.0,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            )
            self.client = YoudaoClient(
                store=CacheStore(self.engine),
                http=http,
            )

    def close(self) -> None:
        http = getattr(self.client, "http", None)
        if http is not None and hasattr(http, "close"):
            http.close()

    def __del__(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self.close()

    def expected_fingerprint(self, *, word_id: int) -> str:  # noqa: ARG002
        stage_config = {
            "parser_version": self.config.parser_version,
            "source": "youdao",
        }
        return fingerprint(
            upstream_fingerprints=[],
            stage_config=stage_config,
            prompt_version=None,
            prompt_content_hash=None,
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        import asyncio

        with self.engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT normalized_form FROM pipeline.words WHERE id = :id"),
                {"id": word_id},
            ).one()
        form = row[0]
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, self.client.fetch, form)
        if not isinstance(raw, dict) or "raw_json" not in raw:
            raise ValueError(f"YoudaoClient.fetch({form!r}) must return dict with 'raw_json'")
        return StagePayload(
            payload={"raw_json": raw["raw_json"], "form": form},
            source=f"pipeline:youdao:fetch_dict_v{self.config.parser_version}",
            model=None,
            prompt_version=None,
            cost_usd=0.0,
            tokens_in=None,
            tokens_out=None,
            duration_ms=None,
        )
