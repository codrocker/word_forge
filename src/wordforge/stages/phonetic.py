"""PhoneticStage — parse fetch_dict raw_json for IPA + audio URLs.

Depends on fetch_dict having run first (upstream stage_artifacts present).
Reads `simple.word[0]` from Youdao's jsonapi response:
  - usphone / ukphone     → IPA strings
  - usspeech / ukspeech   → audio querystring, we compose the full URL
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from wordforge.pipeline.fingerprint import fingerprint
from wordforge.pipeline.protocols import StagePayload

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from wordforge.config import StageConfig
    from wordforge.pipeline.artifacts import StageArtifactStore


_DICTVOICE = "https://dict.youdao.com/dictvoice?audio="


def _audio_url(suffix: str | None) -> str | None:
    """Youdao returns `foo&type=2` — prepend full dictvoice URL."""
    if not suffix:
        return None
    return f"{_DICTVOICE}{suffix}"


def parse_phonetics(raw_json: dict[str, Any]) -> dict[str, str | None]:
    """Extract IPA + audio from Youdao jsonapi response. All fields optional."""
    simple = raw_json.get("simple") if isinstance(raw_json, dict) else None
    words = simple.get("word") if isinstance(simple, dict) else None
    w0: dict[str, Any] = words[0] if isinstance(words, list) and words else {}
    # Fallback: ec.word[0] carries the same keys for solo words; try it if simple missing.
    if not w0:
        ec = raw_json.get("ec") if isinstance(raw_json, dict) else None
        ec_words = ec.get("word") if isinstance(ec, dict) else None
        w0 = ec_words[0] if isinstance(ec_words, list) and ec_words else {}
    return {
        "phonetic_us": w0.get("usphone") or None,
        "phonetic_uk": w0.get("ukphone") or None,
        "audio_us": _audio_url(w0.get("usspeech")),
        "audio_uk": _audio_url(w0.get("ukspeech")),
    }


@dataclass
class PhoneticStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    name: str = field(default="phonetic", init=False)

    def expected_fingerprint(self, *, word_id: int) -> str:
        upstream = self.artifacts.get(word_id=word_id, stage_name="fetch_dict")
        upstream_fp = upstream["fingerprint"] if upstream is not None else ""
        return fingerprint(
            upstream_fingerprints=[upstream_fp] if upstream_fp else [],
            stage_config={"parser_version": self.config.parser_version},
            prompt_version=None,
            prompt_content_hash=None,
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        upstream = self.artifacts.get(word_id=word_id, stage_name="fetch_dict")
        if upstream is None:
            raise LookupError(
                f"fetch_dict artifact missing for word_id={word_id} — upstream must run first"
            )
        payload = upstream["payload"]
        raw_json = payload.get("raw_json", {}) if isinstance(payload, dict) else {}
        parsed = parse_phonetics(raw_json)
        return StagePayload(
            payload=parsed,
            source=f"pipeline:local:phonetic_v{self.config.parser_version}",
            model=None,
            prompt_version=None,
            cost_usd=0.0,
            tokens_in=None,
            tokens_out=None,
            duration_ms=None,
        )
