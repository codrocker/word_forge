"""DerivativesStage — LLM-powered word-form + semantic derivatives.

Upstream: paraphrase (meanings JSON). Calls LLMClient to produce
word_forms and per-meaning synonyms/antonyms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlalchemy as sa

from wordforge.pipeline.fingerprint import fingerprint
from wordforge.pipeline.runner import StagePayload
from wordforge.stages._llm_base import (
    compute_prompt_content_hash,
    load_prompt,
    parse_llm_json,
    source_str,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from wordforge.config import StageConfig
    from wordforge.llm.client import LLMClient
    from wordforge.pipeline.artifacts import StageArtifactStore


@dataclass
class DerivativesStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    llm: LLMClient
    name: str = field(default="derivatives", init=False)

    def expected_fingerprint(self, *, word_id: int) -> str:
        upstream = self.artifacts.get(word_id=word_id, stage_name="paraphrase")
        upstream_fp = upstream["fingerprint"] if upstream is not None else ""
        prompt_version = self.config.prompt_version or "v1"
        return fingerprint(
            upstream_fingerprints=[upstream_fp] if upstream_fp else [],
            stage_config={
                "parser_version": self.config.parser_version,
                "model": self.config.model,
            },
            prompt_version=prompt_version,
            prompt_content_hash=compute_prompt_content_hash("derivatives", prompt_version),
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        import asyncio
        import time

        upstream = self.artifacts.get(word_id=word_id, stage_name="paraphrase")
        if upstream is None:
            raise LookupError(f"paraphrase missing for word_id={word_id}")
        meanings_payload = upstream["payload"]

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT normalized_form FROM pipeline.words WHERE id = :id"),
                {"id": word_id},
            ).one()
        word = row[0]

        prompt_version = self.config.prompt_version or "v1"
        prompt_template = load_prompt("derivatives", prompt_version)
        meanings_json = json.dumps(meanings_payload, ensure_ascii=False)
        rendered_prompt = prompt_template.replace("{word}", word).replace(
            "{meanings_json}", meanings_json[:4000]
        )

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        completion = await loop.run_in_executor(
            None,
            lambda: self.llm.complete(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                rendered_prompt=rendered_prompt,
                request_params={"temperature": 0, "max_tokens": 2048},
                input_payload={"word": word},
            ),
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        raw_text = completion.response.get("text", "")
        parsed = parse_llm_json(raw_text)

        resp = completion.response if isinstance(completion.response, dict) else {}
        return StagePayload(
            payload=parsed if isinstance(parsed, dict) else {"raw": parsed},
            source=source_str(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                stage="derivatives",
                parser_version=self.config.parser_version,
            ),
            model=self.config.model,
            prompt_version=prompt_version,
            cost_usd=completion.cost_usd,
            tokens_in=resp.get("in_tok"),
            tokens_out=resp.get("out_tok"),
            duration_ms=elapsed_ms,
        )
