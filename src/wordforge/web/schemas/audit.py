"""Audit log response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EditorBrief(BaseModel):
    id: int
    display_name: str


class AuditItem(BaseModel):
    id: int
    word_id: int
    field_path: str
    target_id: int | None
    op: str
    old_value: Any
    new_value: Any
    editor: EditorBrief
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditItem]
    next_cursor: str | None
