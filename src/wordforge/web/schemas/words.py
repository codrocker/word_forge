"""Words request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class WordListItem(BaseModel):
    word_id: int
    form: str
    type: int
    status: int
    quality_flag: str
    updated_at: datetime
    meaning_count: int


class WordListResponse(BaseModel):
    items: list[WordListItem]
    next_cursor: str | None


class WordDetailResponse(BaseModel):
    word: dict[str, Any]
    meanings: list[dict[str, Any]]
    mnemonics: list[dict[str, Any]]
    sentences: list[dict[str, Any]]
    phrases: list[dict[str, Any]]


class PatchChange(BaseModel):
    field_path: str
    target_id: int | None = None
    op: Literal["update"]  # M4.3 MVP: update only
    old_value: Any
    new_value: Any


class PatchRequest(BaseModel):
    changes: list[PatchChange]


class StatusChangeRequest(BaseModel):
    old_value: Literal[0, 1, 2]
    new_value: Literal[0, 1, 2]


class QualityChangeRequest(BaseModel):
    old_value: Literal["none", "suspect", "fixed"]
    new_value: Literal["none", "suspect", "fixed"]
