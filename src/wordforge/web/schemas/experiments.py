"""Experiments request schemas (web M8)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExperimentRunRequest(BaseModel):
    # agent mode: when agent_id is set, provider/model/stage/template all
    # come from the agent's pinned versions (config center); the three
    # fields below are then ignored by the server.
    agent_id: int | None = Field(default=None, ge=1)
    provider: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=200)
    stage: str = Field(default="", max_length=64)
    prompt_override: str | None = Field(default=None, max_length=20_000)
    word_count: int = Field(default=50, ge=1, le=200)
    seed: int = Field(default=42)
