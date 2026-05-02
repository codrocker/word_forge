"""fingerprint(): v9 spec §6 公式

fingerprint = sha256(canonical_json([
  sorted(upstream_fingerprints),
  stage_config,
  prompt_version,
  prompt_content_hash,
  parser_version,
]))

Invariants verified here:
- deterministic across calls with same inputs
- JSON key order in stage_config doesn't affect output
- upstream_fingerprints is sorted internally (caller can pass unsorted)
- parser_version changes yield different fingerprint (guarantees "parser bug fix
  bump → stage rerun")
- prompt_content_hash changes yield different fingerprint
- code_hash is NOT a parameter (spec §10 #11: ruff format must not flip 10万词
  fingerprint). Behavioral guard: kwargs-only signature rejects `code_hash=...`.
"""

from __future__ import annotations

import pytest

from wordforge.pipeline.fingerprint import fingerprint


def test_is_deterministic():
    fp1 = fingerprint(
        upstream_fingerprints=["a", "b"],
        stage_config={"model": "claude-opus-4", "temperature": 0},
        prompt_version="v2",
        prompt_content_hash="deadbeef",
        parser_version="1",
    )
    fp2 = fingerprint(
        upstream_fingerprints=["a", "b"],
        stage_config={"model": "claude-opus-4", "temperature": 0},
        prompt_version="v2",
        prompt_content_hash="deadbeef",
        parser_version="1",
    )
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_upstream_fingerprints_sorted_internally():
    a = fingerprint(
        upstream_fingerprints=["b", "a"],
        stage_config={},
        prompt_version="v1",
        prompt_content_hash="",
        parser_version="1",
    )
    b = fingerprint(
        upstream_fingerprints=["a", "b"],
        stage_config={},
        prompt_version="v1",
        prompt_content_hash="",
        parser_version="1",
    )
    assert a == b


def test_stage_config_key_order_irrelevant():
    a = fingerprint(
        upstream_fingerprints=[],
        stage_config={"temperature": 0, "max_tokens": 2048},
        prompt_version="v1",
        prompt_content_hash="",
        parser_version="1",
    )
    b = fingerprint(
        upstream_fingerprints=[],
        stage_config={"max_tokens": 2048, "temperature": 0},
        prompt_version="v1",
        prompt_content_hash="",
        parser_version="1",
    )
    assert a == b


def test_parser_version_bump_changes_fingerprint():
    base = dict(
        upstream_fingerprints=[],
        stage_config={},
        prompt_version="v1",
        prompt_content_hash="",
    )
    a = fingerprint(parser_version="1", **base)
    b = fingerprint(parser_version="2", **base)
    assert a != b


def test_prompt_content_hash_changes_fingerprint():
    base = dict(
        upstream_fingerprints=[],
        stage_config={},
        prompt_version="v1",
        parser_version="1",
    )
    a = fingerprint(prompt_content_hash="aaaa", **base)
    b = fingerprint(prompt_content_hash="bbbb", **base)
    assert a != b


def test_prompt_version_changes_fingerprint():
    base = dict(
        upstream_fingerprints=[],
        stage_config={},
        prompt_content_hash="",
        parser_version="1",
    )
    a = fingerprint(prompt_version="v1", **base)
    b = fingerprint(prompt_version="v2", **base)
    assert a != b


def test_stage_config_nested_dict_order_irrelevant():
    """Round 3 R3-arch-3: json.dumps(sort_keys=True) applies recursively.
    Lock the invariant that nested stage_config keys have deterministic
    serialization, so parser can nest model params without breaking fingerprint.
    """
    a = fingerprint(
        upstream_fingerprints=[],
        stage_config={"params": {"z": 1, "a": 0, "m": {"y": 2, "x": 1}}},
        prompt_version="v1",
        prompt_content_hash="",
        parser_version="1",
    )
    b = fingerprint(
        upstream_fingerprints=[],
        stage_config={"params": {"a": 0, "m": {"x": 1, "y": 2}, "z": 1}},
        prompt_version="v1",
        prompt_content_hash="",
        parser_version="1",
    )
    assert a == b


def test_rejects_code_hash_kwarg():
    """Behavioral guard: spec §10 #11 says code_hash must not exist. The
    kwargs-only signature has no `code_hash` parameter, so passing it raises
    TypeError. If someone ever adds it, this test fails loud."""
    with pytest.raises(TypeError):
        fingerprint(
            upstream_fingerprints=[],
            stage_config={},
            prompt_version="v1",
            prompt_content_hash="",
            parser_version="1",
            code_hash="abc",  # type: ignore[call-arg]
        )
