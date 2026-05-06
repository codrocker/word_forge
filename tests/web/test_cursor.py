"""Cursor encode/decode roundtrip + error paths."""
import base64
import json

import pytest

from wordforge.web.cursor import decode, encode


def test_encode_decode_roundtrip():
    c = encode("updated_at_desc", "2026-05-06T10:00:00Z", 12345)
    d = decode(c, "updated_at_desc")
    assert d.u == "2026-05-06T10:00:00Z"
    assert d.w == 12345
    assert d.o == "updated_at_desc"


def test_decode_rejects_garbage_base64():
    with pytest.raises(ValueError):
        decode("not@@valid!!base64", "updated_at_desc")


def test_decode_rejects_non_json_payload():
    # valid base64 but not JSON
    raw = base64.urlsafe_b64encode(b"not-json-content").decode().rstrip("=")
    with pytest.raises(ValueError):
        decode(raw, "updated_at_desc")


def test_decode_rejects_missing_fields():
    # valid JSON but missing required keys
    raw = base64.urlsafe_b64encode(
        json.dumps({"o": "updated_at_desc"}).encode()
    ).decode().rstrip("=")
    with pytest.raises(ValueError):
        decode(raw, "updated_at_desc")


def test_decode_rejects_wrong_order():
    # construct a cursor with an unknown order (bypass Literal via inline JSON)
    raw = base64.urlsafe_b64encode(
        json.dumps({"o": "lemma_asc", "u": "2026-05-06T10:00:00Z", "w": 1}).encode()
    ).decode().rstrip("=")
    with pytest.raises(ValueError):
        decode(raw, "updated_at_desc")


def test_encode_produces_url_safe_ascii():
    c = encode("updated_at_desc", "2026-05-06T10:00:00Z", 99999999)
    # url-safe base64 without padding should have only [A-Za-z0-9_-]
    assert all(ch.isalnum() or ch in "-_" for ch in c)
    assert "=" not in c
