"""P5b Task 0: _llm_base shared helpers."""

from __future__ import annotations

import pytest

from wordforge.stages._llm_base import (
    compute_prompt_content_hash,
    load_prompt,
    parse_llm_json,
    source_str,
)


def test_load_prompt_paraphrase_v1():
    s = load_prompt("paraphrase", "v1")
    assert isinstance(s, str)
    assert len(s) > 10


def test_load_prompt_unknown_stage_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("no_such_stage", "v1")


def test_load_prompt_unknown_version_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("paraphrase", "v99")


def test_compute_prompt_content_hash_deterministic():
    h1 = compute_prompt_content_hash("paraphrase", "v1")
    h2 = compute_prompt_content_hash("paraphrase", "v1")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_parse_llm_json_raw():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_markdown_fence():
    # LLMs love wrapping JSON in ```json ... ``` fences.
    raw = '```json\n{"a": 1, "b": [2, 3]}\n```'
    assert parse_llm_json(raw) == {"a": 1, "b": [2, 3]}


def test_parse_llm_json_plain_fence():
    raw = '```\n{"a": 1}\n```'
    assert parse_llm_json(raw) == {"a": 1}


def test_parse_llm_json_with_prose_prefix():
    raw = 'Here is the JSON:\n\n{"a": 1}'
    assert parse_llm_json(raw) == {"a": 1}


def test_parse_llm_json_invalid_raises():
    with pytest.raises(ValueError, match="could not parse JSON"):
        parse_llm_json("not JSON at all")


def test_parse_llm_json_skips_non_json_bracket_to_real_payload():
    # LLM emits prose with a placeholder-looking brace before the real JSON.
    raw = 'Here is what you asked for: {not actual json} but here: {"real": "json", "count": 2}'
    result = parse_llm_json(raw)
    assert result == {"real": "json", "count": 2}


def test_parse_llm_json_multiple_bracket_candidates():
    # Three opens; only the third balances validly.
    raw = '{broken} [not valid json] {"ok": true}'
    result = parse_llm_json(raw)
    assert result == {"ok": True}


def test_parse_llm_json_escapes_nested_unescaped_quotes():
    """LLMs frequently emit `"mnemonic": "她喊："停！""` — unescaped inner `"`.

    The parser should recover by escaping stray quotes inside string values.
    """
    raw = '{"mnemonic": "阿婆拿苹果说："阿婆了！"", "kind": "phonetic"}'
    r = parse_llm_json(raw)
    assert r["kind"] == "phonetic"
    assert "阿婆了" in r["mnemonic"]


def test_parse_llm_json_handles_multiple_inner_quotes():
    raw = '{"a": "他说："你好"然后"再见"", "b": 1}'
    r = parse_llm_json(raw)
    assert r["b"] == 1
    assert "你好" in r["a"]


def test_source_str_format():
    s = source_str(
        provider="anthropic",
        model="claude-opus-4",
        stage="paraphrase",
        parser_version="2",
    )
    assert s == "pipeline:anthropic:claude-opus-4:paraphrase_v2"
