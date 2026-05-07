"""Auth request/response pydantic schemas."""
from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    # Plain str — EmailStr rejects .local/.test/.localhost (RFC 6761 reserved
    # TLDs) which are legitimate for internal admin accounts.
    email: str
    password: str


class EditorOut(BaseModel):
    id: int
    email: str
    display_name: str
