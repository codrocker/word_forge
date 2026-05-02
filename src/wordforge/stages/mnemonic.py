"""MnemonicStage — LLM-powered memory hook generation.

Upstream: paraphrase + phonetic (two upstream fingerprints). Calls LLMClient
to produce a Chinese phonetic-pun mnemonic.
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
class MnemonicStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    llm: LLMClient
    name: str = field(default="mnemonic", init=False)

    def expected_fingerprint(self, *, word_id: int) -> str:
        paraphrase = self.artifacts.get(word_id=word_id, stage_name="paraphrase")
        phonetic = self.artifacts.get(word_id=word_id, stage_name="phonetic")
        upstream_fps = []
        if paraphrase is not None and paraphrase["fingerprint"]:
            upstream_fps.append(paraphrase["fingerprint"])
        if phonetic is not None and phonetic["fingerprint"]:
            upstream_fps.append(phonetic["fingerprint"])
        prompt_version = self.config.prompt_version or "v1"
        return fingerprint(
            upstream_fingerprints=upstream_fps,
            stage_config={
                "parser_version": self.config.parser_version,
                "model": self.config.model,
            },
            prompt_version=prompt_version,
            prompt_content_hash=compute_prompt_content_hash("mnemonic", prompt_version),
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        import asyncio
        import time

        paraphrase = self.artifacts.get(word_id=word_id, stage_name="paraphrase")
        if paraphrase is None:
            raise LookupError(f"paraphrase missing for word_id={word_id}")
        phonetic = self.artifacts.get(word_id=word_id, stage_name="phonetic")
        if phonetic is None:
            raise LookupError(f"phonetic missing for word_id={word_id}")

        meanings_payload = paraphrase["payload"]
        phonetic_payload = phonetic["payload"]
        phonetic_us = (
            phonetic_payload.get("phonetic_us", "") if isinstance(phonetic_payload, dict) else ""
        )

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT normalized_form FROM pipeline.words WHERE id = :id"),
                {"id": word_id},
            ).one()
        word = row[0]

        prompt_version = self.config.prompt_version or "v1"
        prompt_template = load_prompt("mnemonic", prompt_version)
        meanings_json = json.dumps(meanings_payload, ensure_ascii=False)
        rendered_prompt = (
            prompt_template.replace("{word}", word)
            .replace("{phonetic_us}", phonetic_us or "")
            .replace("{meanings_json}", meanings_json[:4000])
        )

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        completion = await loop.run_in_executor(
            None,
            lambda: self.llm.complete(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                rendered_prompt=rendered_prompt,
                request_params={"temperature": 0, "max_tokens": 1024},
                input_payload={"word": word, "phonetic_us": phonetic_us},
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
                stage="mnemonic",
                parser_version=self.config.parser_version,
            ),
            model=self.config.model,
            prompt_version=prompt_version,
            cost_usd=completion.cost_usd,
            tokens_in=resp.get("in_tok"),
            tokens_out=resp.get("out_tok"),
            duration_ms=elapsed_ms,
        )
