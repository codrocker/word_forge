"""Shared LLM-stage helpers.

- load_prompt: read packaged prompt via importlib.resources
- compute_prompt_content_hash: sha256 of prompt text (fingerprint input)
- parse_llm_json: tolerant JSON decoder for LLM responses
- source_str: canonical `source` string for stage_artifacts.source
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources


def load_prompt(stage: str, version: str) -> str:
    ref = resources.files("wordforge.resources.prompts") / stage / f"{version}.md"
    if not ref.is_file():
        raise FileNotFoundError(f"prompt not found: resources/prompts/{stage}/{version}.md")
    return ref.read_text(encoding="utf-8")


def compute_prompt_content_hash(stage: str, version: str) -> str:
    return hashlib.sha256(load_prompt(stage, version).encode("utf-8")).hexdigest()


_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    re.DOTALL,
)


def parse_llm_json(raw: str) -> dict | list:
    """Parse JSON from an LLM response.

    Handles:
    - Plain JSON
    - Markdown-fenced: ```json ... ``` or ``` ... ```
    - Leading prose: "Here is the JSON: { ... }" — pick the first {/[ and
      attempt balanced-bracket substring extraction

    Raises ValueError if no valid JSON can be recovered.
    """
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to locate the first balanced JSON value.
    for start in range(len(text)):
        if text[start] in "{[":
            open_ch, close_ch = ("{", "}") if text[start] == "{" else ("[", "]")
            depth = 0
            for end in range(start, len(text)):
                if text[end] == open_ch:
                    depth += 1
                elif text[end] == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : end + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            # Common LLM bug: string values contain unescaped `"`
                            # (e.g. `"mnemonic": "他喊："停！""`). Try escaping
                            # inner quotes inside string values.
                            try:
                                return json.loads(_escape_inner_quotes(candidate))
                            except json.JSONDecodeError:
                                break
            continue
    raise ValueError(f"could not parse JSON from LLM response: {raw[:200]!r}") from None


def _escape_inner_quotes(text: str) -> str:
    """Escape stray `"` inside JSON string values.

    Walks the string and tracks whether we're inside a `"..."` value. If we
    encounter a `"` that isn't followed by `,`, `:`, `}`, `]`, or whitespace-
    then-those, it's almost certainly an unescaped inner quote — escape it.
    """
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            out.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            if not in_string:
                out.append(ch)
                in_string = True
                i += 1
                continue
            # We're inside a string. Look ahead: if the next non-space char is
            # a JSON structural delimiter, this `"` closes the string.
            j = i + 1
            while j < n and text[j] in " \t\n\r":
                j += 1
            if j >= n or text[j] in ",:}]":
                out.append(ch)
                in_string = False
                i += 1
                continue
            # Stray quote inside a string — escape it.
            out.append('\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def source_str(
    *,
    provider: str,
    model: str,
    stage: str,
    parser_version: str,
) -> str:
    return f"pipeline:{provider}:{model}:{stage}_v{parser_version}"
