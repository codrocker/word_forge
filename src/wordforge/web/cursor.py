"""Keyset pagination cursor — plain base64(JSON), no HMAC (spec §3.2 Round 1)."""
from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ValidationError

Order = Literal["updated_at_desc"]  # MVP 单一 order；lemma_asc 后续迭代


class Cursor(BaseModel):
    o: Order
    u: str  # updated_at ISO-8601
    w: int  # word_id


def encode(order: Order, updated_at: str, word_id: int) -> str:
    payload = Cursor(o=order, u=updated_at, w=word_id).model_dump()
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(raw: str, expected_order: Order) -> Cursor:
    """Decode base64(JSON) cursor. Raises ValueError on any malformed input."""
    try:
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor encoding") from exc
    try:
        cursor = Cursor(**payload)
    except ValidationError as exc:
        raise ValueError(f"invalid cursor payload: {exc}") from exc
    if cursor.o != expected_order:
        raise ValueError(f"cursor order mismatch: expected {expected_order}, got {cursor.o}")
    return cursor
