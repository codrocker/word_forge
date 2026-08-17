"""Config center request schemas (M9)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProviderConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: str = Field(default="openai", pattern="^(openai|anthropic)$")
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(min_length=8, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class ProviderConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    transport: str | None = Field(default=None, pattern="^(openai|anthropic)$")
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    stage: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=20_000)
    description: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class PromptUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    description: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    provider_config_id: int = Field(ge=1)
    provider_config_version: int | None = Field(default=None, ge=1)
    model: str = Field(min_length=1, max_length=200)
    prompt_id: int = Field(ge=1)
    prompt_version: int | None = Field(default=None, ge=1)
    params: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AgentUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    provider_config_id: int = Field(ge=1)
    provider_config_version: int | None = Field(default=None, ge=1)
    model: str = Field(min_length=1, max_length=200)
    prompt_id: int = Field(ge=1)
    prompt_version: int | None = Field(default=None, ge=1)
    params: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=2000)
