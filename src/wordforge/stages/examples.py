"""ExamplesStage — LLM-powered example sentence generation.

Upstream: paraphrase (meanings JSON) + fetch_dict (youdao raw_json —
optional reference examples). The LLM is instructed to use dictionary
examples as style inspiration but to REWRITE them, never copy verbatim,
and to keep surrounding vocabulary easier than the target word.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlalchemy as sa

from wordforge.pipeline.fingerprint import fingerprint
from wordforge.pipeline.protocols import StagePayload
from wordforge.stages._llm_base import (
    compute_prompt_content_hash,
    load_prompt,
    parse_llm_json,
    source_str,
)

# Upper bound on reference examples per word. Collins + ec + blng combined
# can exceed 30 sentences on polysemous words; feeding them all inflates
# the prompt and invites verbatim copying. 10 is enough for the LLM to
# grok style + collocations without drowning it.
_MAX_REFERENCE_EXAMPLES = 10
_BOLD_TAG_RE = re.compile(r"</?b>", re.IGNORECASE)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from wordforge.config import StageConfig
    from wordforge.llm.client import LLMClient
    from wordforge.pipeline.artifacts import StageArtifactStore


def _extract_reference_examples(raw_json: object) -> list[dict]:
    """Group example sentences by their own dictionary's sense label.

    Returns a list of sense-buckets, each::

        {"sense": "<dictionary's sense label, or null>",
         "examples": [{"en": "...", "cn": "..."}]}

    Sources:
      1. ec.word[*].trs[*].tr[*] — `l.i` is the Chinese sense gloss
         (e.g. "抬起，拾起：..."); `l.sentence[*]` are examples for that
         sense. Preserving the tr grouping keeps examples attached to
         their paraphrase.
      2. collins.collins_entries[*].entries.entry[*].tran_entry[*] —
         `tran` is the EN+CN gloss (e.g. "When you pick something up,
         you lift it up. 拾起"); `exam_sents.sent[*]` are its examples.
      3. blng_sents_part.sentence-pair[*] — no sense grouping. We put
         these in one bucket with sense=None; prompt tells the LLM
         these are un-aligned corpus, match by semantics, don't assume
         any particular meaning_index.

    Selection strategy (round-robin across sense-aligned buckets):
    - Materialize ec + collins buckets with ALL their candidate sentences.
    - Round-robin pick: round i takes each bucket's i-th unseen candidate
      before moving to round i+1. This guarantees every sense gets its
      first example before any sense gets a second — critical because a
      naive depth-first pass would let ec's first `tr` eat the whole 10-
      example budget on a polysemous word.
    - Blng bucket (sense=None, un-aligned corpus) fills leftover budget
      after sense-aligned picks are done.

    Post-processing:
    - `<b>...</b>` tags stripped (youdao highlights the headword).
    - Exact-match English dedupe (case-fold) across ALL buckets.
    - Total example count capped at _MAX_REFERENCE_EXAMPLES.
    - Empty buckets (sense with zero examples after dedupe) are dropped.
    """
    if not isinstance(raw_json, dict):
        return []

    def _clean(en: object, cn: object) -> tuple[str, str] | None:
        if not isinstance(en, str) or not isinstance(cn, str):
            return None
        en_clean = _BOLD_TAG_RE.sub("", en).strip()
        cn_clean = _BOLD_TAG_RE.sub("", cn).strip()
        if not en_clean or not cn_clean:
            return None
        return en_clean, cn_clean

    # Phase 1 — materialize sense-aligned buckets with all their candidates.
    # Buckets order inside this list encodes source preference for human
    # readers (ec first = most learner-familiar), but the round-robin below
    # is what actually guarantees per-sense coverage.
    #
    # Sources sometimes have entries WITHOUT a sense label (e.g. collins
    # ships secondary tran_entries for word-family variants with tran=None).
    # Those candidates do NOT belong in sense-aligned round-robin — they
    # go to the shared null-sense pool alongside blng, where the prompt
    # treats them as un-aligned corpus.
    sense_buckets: list[dict] = []
    unaligned_cands: list[tuple[str, str]] = []

    # ec — each `tr` is one sense bucket
    ec = raw_json.get("ec")
    if isinstance(ec, dict):
        for w in ec.get("word", []) or []:
            if not isinstance(w, dict):
                continue
            for trs_entry in w.get("trs", []) or []:
                for tr in (trs_entry or {}).get("tr", []) or []:
                    line = (tr or {}).get("l") or {}
                    gloss_list = line.get("i")
                    sense = None
                    if isinstance(gloss_list, list) and gloss_list:
                        first = gloss_list[0]
                        sense = first if isinstance(first, str) else None
                    elif isinstance(gloss_list, str):
                        sense = gloss_list
                    cands: list[tuple[str, str]] = []
                    for s in line.get("sentence", []) or []:
                        if isinstance(s, dict):
                            cleaned = _clean(s.get("en"), s.get("zh"))
                            if cleaned is not None:
                                cands.append(cleaned)
                    if not cands:
                        continue
                    if sense:
                        sense_buckets.append({"sense": sense, "candidates": cands})
                    else:
                        unaligned_cands.extend(cands)

    # collins — each `tran_entry` is one sense bucket
    col = raw_json.get("collins")
    if isinstance(col, dict):
        for ce in col.get("collins_entries", []) or []:
            inner = (ce or {}).get("entries")
            entry_list = []
            if isinstance(inner, dict):
                entry_list = inner.get("entry") or []
            elif isinstance(inner, list):
                entry_list = inner
            for ent_wrap in entry_list:
                for tr in (ent_wrap or {}).get("tran_entry", []) or []:
                    sense = tr.get("tran") if isinstance(tr, dict) else None
                    if isinstance(sense, str):
                        sense = _BOLD_TAG_RE.sub("", sense).strip() or None
                    cands2: list[tuple[str, str]] = []
                    exam = (tr or {}).get("exam_sents") or {}
                    for s in exam.get("sent", []) or []:
                        if isinstance(s, dict):
                            cleaned = _clean(s.get("eng_sent"), s.get("chn_sent"))
                            if cleaned is not None:
                                cands2.append(cleaned)
                    if not cands2:
                        continue
                    if sense:
                        sense_buckets.append({"sense": sense, "candidates": cands2})
                    else:
                        unaligned_cands.extend(cands2)

    # Phase 2 — round-robin pick: round i takes each bucket's i-th unseen
    # candidate before moving to round i+1. This maximizes sense coverage
    # when the budget (_MAX_REFERENCE_EXAMPLES) is smaller than the total
    # candidate pool — a word with 6 senses × 5 sentences each still gets
    # at least one example per sense before any sense gets a second.
    picked: list[list[dict]] = [[] for _ in sense_buckets]
    seen_en: set[str] = set()
    remaining = _MAX_REFERENCE_EXAMPLES
    round_idx = 0
    while remaining > 0:
        progress = False
        for bi, bucket in enumerate(sense_buckets):
            if remaining <= 0:
                break
            if round_idx >= len(bucket["candidates"]):
                continue
            en_clean, cn_clean = bucket["candidates"][round_idx]
            key = en_clean.casefold()
            progress = True  # had something to consider this round, even if dup
            if key in seen_en:
                continue
            seen_en.add(key)
            picked[bi].append({"en": en_clean, "cn": cn_clean})
            remaining -= 1
        if not progress:
            break
        round_idx += 1

    buckets: list[dict] = [
        {"sense": b["sense"], "examples": exs}
        for b, exs in zip(sense_buckets, picked, strict=True)
        if exs
    ]

    # Phase 3 — single un-aligned bucket: ec/collins entries with no sense
    # label (e.g. collins word-family variants) + blng_sents_part corpus.
    # Prompt treats this whole bucket as un-aligned, so merging them is
    # semantically correct and keeps the bucket list tidy.
    unaligned_exs: list[dict] = []
    for en_clean, cn_clean in unaligned_cands:
        if remaining <= 0:
            break
        key = en_clean.casefold()
        if key in seen_en:
            continue
        seen_en.add(key)
        unaligned_exs.append({"en": en_clean, "cn": cn_clean})
        remaining -= 1

    bl = raw_json.get("blng_sents_part")
    if isinstance(bl, dict) and remaining > 0:
        for sp in bl.get("sentence-pair", []) or []:
            if remaining <= 0:
                break
            if isinstance(sp, dict):
                cleaned = _clean(sp.get("sentence"), sp.get("sentence-translation"))
                if cleaned is None:
                    continue
                en_clean, cn_clean = cleaned
                key = en_clean.casefold()
                if key in seen_en:
                    continue
                seen_en.add(key)
                unaligned_exs.append({"en": en_clean, "cn": cn_clean})
                remaining -= 1

    if unaligned_exs:
        buckets.append({"sense": None, "examples": unaligned_exs})

    return buckets


def _normalize_examples_shape(parsed: object) -> dict:
    """Ensure LLM output is wrapped as {per_meaning: [...]}.

    Sonnet-4-6 sometimes returns a single-meaning object directly
    (`{"examples": [...], "meaning_index": 0}`) instead of the documented
    top-level `{"per_meaning": [...]}`. Wrap both shapes uniformly so
    downstream consumers (quality_gate, export) work without special-casing.
    """
    if not isinstance(parsed, dict):
        return {"per_meaning": []}
    if "per_meaning" in parsed and isinstance(parsed["per_meaning"], list):
        return parsed
    # Single-meaning shape: wrap into per_meaning with one entry.
    if "examples" in parsed and isinstance(parsed["examples"], list):
        entry = {
            "meaning_index": parsed.get("meaning_index", 0),
            "examples": parsed["examples"],
        }
        return {"per_meaning": [entry]}
    return {"per_meaning": []}


@dataclass
class ExamplesStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    llm: LLMClient
    name: str = field(default="examples", init=False)

    def expected_fingerprint(self, *, word_id: int) -> str:
        ups_fps: list[str] = []
        for up_name in ("paraphrase", "fetch_dict"):
            row = self.artifacts.get(word_id=word_id, stage_name=up_name)
            if row is not None and row["fingerprint"]:
                ups_fps.append(row["fingerprint"])
        prompt_version = self.config.prompt_version or "v1"
        return fingerprint(
            upstream_fingerprints=ups_fps,
            stage_config={
                "parser_version": self.config.parser_version,
                "model": self.config.model,
            },
            prompt_version=prompt_version,
            prompt_content_hash=compute_prompt_content_hash("examples", prompt_version),
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        import asyncio
        import time

        upstream = self.artifacts.get(word_id=word_id, stage_name="paraphrase")
        if upstream is None:
            raise LookupError(f"paraphrase missing for word_id={word_id}")
        meanings_payload = upstream["payload"]

        # fetch_dict is a soft dep: if available, we feed its examples as
        # style reference. Missing (test fixture without fetch_dict artifact,
        # or youdao miss) → reference_examples = [] and prompt falls back
        # to generate-from-scratch semantics.
        fd_row = self.artifacts.get(word_id=word_id, stage_name="fetch_dict")
        fd_payload = fd_row["payload"] if fd_row is not None else None
        raw_json = fd_payload.get("raw_json") if isinstance(fd_payload, dict) else None
        reference_examples = _extract_reference_examples(raw_json)

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT normalized_form FROM pipeline.words WHERE id = :id"),
                {"id": word_id},
            ).one()
        word = row[0]

        # Paraphrase stage already reorders meanings by modern-usage frequency
        # (via the haiku rerank pass). Send top 5 to examples — prompt allocates
        # 3/2/2/1/1 examples across them so the most common senses get the most
        # coverage.
        top_meanings = (
            meanings_payload.get("meanings", []) if isinstance(meanings_payload, dict) else []
        )[:5]
        prompt_version = self.config.prompt_version or "v1"
        prompt_template = load_prompt("examples", prompt_version)
        meanings_json = json.dumps(top_meanings, ensure_ascii=False)
        reference_json = json.dumps(reference_examples, ensure_ascii=False)
        rendered_prompt = (
            prompt_template.replace("{word}", word)
            .replace("{meanings_json}", meanings_json[:2500])
            .replace("{reference_examples_json}", reference_json[:3000])
        )

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        completion = await loop.run_in_executor(
            None,
            lambda: self.llm.complete(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                rendered_prompt=rendered_prompt,
                request_params={"temperature": 0, "max_tokens": 4096},
                input_payload={"word": word},
            ),
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        raw_text = completion.response.get("text", "")
        parsed = parse_llm_json(raw_text)
        normalized = _normalize_examples_shape(parsed)

        resp = completion.response if isinstance(completion.response, dict) else {}
        return StagePayload(
            payload=normalized if isinstance(normalized, dict) else {"raw": parsed},
            source=source_str(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                stage="examples",
                parser_version=self.config.parser_version,
            ),
            model=self.config.model,
            prompt_version=prompt_version,
            cost_usd=completion.cost_usd,
            tokens_in=resp.get("in_tok"),
            tokens_out=resp.get("out_tok"),
            duration_ms=elapsed_ms,
        )
