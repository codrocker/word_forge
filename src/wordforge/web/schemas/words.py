"""Words request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

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
