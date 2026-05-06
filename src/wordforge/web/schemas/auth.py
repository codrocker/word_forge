"""Auth request/response pydantic schemas."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EditorOut(BaseModel):
    id: int
    email: str
    display_name: str
