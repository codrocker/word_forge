"""Stage contract types shared by runner and stage implementations.

Separated from runner.py so stages/ can import Stage / StagePayload without
pulling in the whole StageRunner execution machinery (which would create a
stages -> runner -> stages cycle via the `Sequence[Stage]` parameter).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(kw_only=True)
class StagePayload:
    # Round 3 R3-gem-4: kw_only + defaults removes boilerplate for non-LLM
    # stages (fetch_dict / phonetic / export) that only need payload + source.
    payload: dict[str, Any] | list[Any]
    source: str
    model: str | None = None
    prompt_version: str | None = None
    cost_usd: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int | None = None


class Stage(Protocol):
    name: str

    def expected_fingerprint(self, *, word_id: int) -> str: ...
    async def run_one(self, *, word_id: int) -> StagePayload: ...
