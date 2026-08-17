"""Experiments request schemas (web M8)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExperimentRunRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    stage: str = Field(min_length=1, max_length=64)
    prompt_override: str | None = Field(default=None, max_length=20_000)
    word_count: int = Field(default=50, ge=1, le=200)
    seed: int = Field(default=42)
