"""ParaphraseStage — LLM-powered structured meaning extraction.

Upstream: fetch_dict (raw_json). Extracts `ec` + `ee` + `phrs` into a compact
summary and asks the LLM to structure it into {meanings: [{pos, cn, en}]}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from wordforge.pipeline.fingerprint import fingerprint
from wordforge.pipeline.protocols import StagePayload
from wordforge.stages._llm_base import (
    compute_prompt_content_hash,
    load_prompt,
    parse_llm_json,
    source_str,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from wordforge.config import StageConfig
    from wordforge.llm.client import LLMClient
    from wordforge.pipeline.artifacts import StageArtifactStore


def summarize_youdao_json(raw_json: dict[str, Any]) -> str:
    """Reduce raw Youdao JSON to a ~1-2KB prompt-ready text block.

    Picks only fields useful for meaning extraction: ec (EN→CN), ee (EN→EN),
    phrs (phrases/collocations). Skips advertising, exam stats, etc.
    """
    if not isinstance(raw_json, dict):
        return ""
    chunks: list[str] = []

    def _first_word_dict(section: Any) -> dict:
        """Youdao inconsistently wraps `word` as list[dict], dict, or string."""
        if not isinstance(section, dict):
            return {}
        w = section.get("word")
        if isinstance(w, list) and w and isinstance(w[0], dict):
            return w[0]
        if isinstance(w, dict):
            return w
        return {}

    ec = raw_json.get("ec", {})
    ec_words = _first_word_dict(ec)
    if ec_words:
        w0 = ec_words
        for tr_group in w0.get("trs", []) or []:
            for tr in tr_group.get("tr", []) or []:
                items = tr.get("l", {}).get("i", []) if isinstance(tr.get("l"), dict) else []
                for item in items:
                    if isinstance(item, str) and item.strip():
                        chunks.append(f"[ec] {item.strip()}")
        wfs = w0.get("wfs", [])
        if wfs:
            wf_parts: list[str] = []
            for wf_wrap in wfs:
                wf = wf_wrap.get("wf", {}) if isinstance(wf_wrap, dict) else {}
                name = wf.get("name")
                value = wf.get("value")
                if name and value:
                    wf_parts.append(f"{name}={value}")
            if wf_parts:
                chunks.append("[wfs] " + ", ".join(wf_parts))

    ee = raw_json.get("ee", {})
    ee_w = _first_word_dict(ee)
    for tr_group in ee_w.get("trs", []) or []:
        for tr in (tr_group.get("tr") or []) if isinstance(tr_group, dict) else []:
            if not isinstance(tr, dict):
                continue
            pos = tr.get("pos", "")
            gloss_l = tr.get("l") if isinstance(tr.get("l"), dict) else {}
            items = gloss_l.get("i", []) if isinstance(gloss_l, dict) else []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, str) and item.strip():
                    chunks.append(f"[ee] {pos} {item.strip()}".strip())
                elif isinstance(item, dict) and item.get("#text"):
                    chunks.append(f"[ee] {pos} {item['#text'].strip()}".strip())

    phrs = raw_json.get("phrs", {})
    phrase_list = phrs.get("phrs", []) if isinstance(phrs, dict) else []
    if not isinstance(phrase_list, list):
        phrase_list = []
    for p_wrap in phrase_list[:10]:
        p = p_wrap.get("phr", {}) if isinstance(p_wrap, dict) else {}
        headword = p.get("headword", {}) if isinstance(p.get("headword"), dict) else {}
        head = headword.get("l", {}).get("i", "") if isinstance(headword.get("l"), dict) else ""
        trs = p.get("trs", [])
        if trs and isinstance(trs[0], dict):
            tr_l = trs[0].get("tr", {}).get("l", {})
            tr0 = tr_l.get("i", "") if isinstance(tr_l, dict) else ""
            if head and tr0:
                chunks.append(f"[phrs] {head} → {tr0}")

    return "\n".join(chunks)[:3500]


_MOMO_REF_CACHE: dict[str, list[dict]] | None = None


_POS_PREFIX_RE = re.compile(r"^(n|v|vt|vi|adj|adv|prep|conj|pron|interj|abbr|aux|art)\.\s*")
_DERIVATION_HINT_RE = re.compile(
    r"（[^）]{1,40}的(现在分词|过去式|过去分词|比较级|最高级|名词|动词|形容词|副词)[^）]*）"
)
_POS_NORMALIZE = {"vt": "v", "vi": "v"}


def extract_meanings_from_ec(raw_json: dict[str, Any]) -> list[dict] | None:
    """Pull structured [{pos, cn}] out of Youdao's `ec` block.

    Youdao ec format is `[{"trs":[{"tr":[{"l":{"i":["v. 跑，奔跑；参加...","n. 跑步..."]}}]}]}]`.
    Each string in `i` is one pos + multiple Chinese glosses separated by `；`.
    Returns None if ec is unusable (Chinese-annotated pos tags like 【名】, no pos, etc.).
    """
    ec = raw_json.get("ec") if isinstance(raw_json, dict) else None
    if not isinstance(ec, dict):
        return None
    words = ec.get("word")
    if not (isinstance(words, list) and words and isinstance(words[0], dict)):
        return None
    meanings: list[dict] = []
    for tr_group in words[0].get("trs", []) or []:
        if not isinstance(tr_group, dict):
            continue
        for tr in tr_group.get("tr", []) or []:
            if not isinstance(tr, dict):
                continue
            items = tr.get("l", {}).get("i", []) if isinstance(tr.get("l"), dict) else []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, str):
                    continue
                stripped = item.strip()
                m = _POS_PREFIX_RE.match(stripped)
                if not m:
                    continue
                pos_raw = m.group(1)
                pos = _POS_NORMALIZE.get(pos_raw, pos_raw)
                body = stripped[m.end():].strip()

                # Check if this whole i-string is a variant (hint appears at
                # the tail). Youdao groups by pos-per-i-string — "saw" has
                # n.锯 / v.锯开 / v.看见(see 的过去式) in three separate i's,
                # so we tag per-i, not across all meanings of the word.
                hint_m = _DERIVATION_HINT_RE.search(body)
                tag = ""
                if hint_m:
                    base_word = hint_m.group(0).split("的")[0].lstrip("（").strip()
                    variant = hint_m.group(1)
                    tag = f"[{base_word} {variant}] "
                    body = _DERIVATION_HINT_RE.sub("", body).strip()

                parts = [p.strip() for p in body.split("；") if p.strip()]
                for part in parts:
                    if part:
                        meanings.append({"pos": pos, "cn": f"{tag}{part}", "en": None})
    return meanings or None


async def _rerank_meanings(
    *,
    word: str,
    meanings: list[dict],
    llm: LLMClient,
    rerank_cfg: StageConfig | None = None,
) -> tuple[list[dict], float, int]:
    """Reorder meanings by modern usage frequency.

    Model/provider read from `[stages.paraphrase_rerank]` in config. If the
    config section is absent (e.g. older deployments), we fall back to the
    historic haiku-4-5-on-bedrock default so nothing breaks silently.

    Returns (reordered_meanings, cost_usd, duration_ms). If meanings <= 3 or
    the LLM output is unusable, returns the input unchanged with 0 cost.
    """
    import asyncio
    import json
    import time

    if len(meanings) <= 3:
        return meanings, 0.0, 0

    version = (rerank_cfg.prompt_version if rerank_cfg else None) or "v1"
    prompt_template = load_prompt("paraphrase_rerank", version)
    meanings_json = json.dumps(
        [{"cn": m.get("cn"), "en": m.get("en"), "pos": m.get("pos")} for m in meanings],
        ensure_ascii=False,
    )
    rendered = prompt_template.replace("{word}", word).replace(
        "{meanings_json}", meanings_json[:3500]
    )

    import logging

    provider = (rerank_cfg.provider if rerank_cfg else None) or "openai"
    model = (rerank_cfg.model if rerank_cfg else None) or "deepseek-chat"

    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    try:
        completion = await loop.run_in_executor(
            None,
            lambda: llm.complete(
                provider=provider,
                model=model,
                rendered_prompt=rendered,
                request_params={"temperature": 0, "max_tokens": 512},
                input_payload={"word": word, "rerank": True},
            ),
        )
    except Exception:  # noqa: BLE001
        # Rerank is best-effort enhancement; on LLM failure we fall back to
        # the original meaning order so the pipeline keeps moving. Log at
        # WARNING so operators see systemic rerank outages in stderr.
        logging.warning(
            "rerank failed for word=%r — falling back to input order",
            word, exc_info=True,
        )
        return meanings, 0.0, int((time.perf_counter() - t0) * 1000)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    raw_text = completion.response.get("text", "")
    try:
        parsed = parse_llm_json(raw_text)
        order = parsed.get("order") if isinstance(parsed, dict) else None
        if not isinstance(order, list):
            logging.warning(
                "rerank parsed but 'order' missing/invalid for word=%r — input order", word
            )
            return meanings, completion.cost_usd, elapsed_ms
        seen: set[int] = set()
        clean_order: list[int] = []
        for i in order:
            if isinstance(i, int) and 0 <= i < len(meanings) and i not in seen:
                seen.add(i)
                clean_order.append(i)
        if len(clean_order) != len(meanings):
            missing = [i for i in range(len(meanings)) if i not in seen]
            clean_order = clean_order + missing
        reordered = [meanings[i] for i in clean_order]
        return reordered, completion.cost_usd, elapsed_ms
    except (ValueError, KeyError, TypeError):
        logging.warning(
            "rerank response unparseable for word=%r — input order", word, exc_info=True
        )
        return meanings, completion.cost_usd, elapsed_ms


def _load_momo_ref(word: str) -> list[dict] | None:
    """Read momo meaning reference for `word` from the env-pointed JSON file.

    Returns list[{pos, cn, en}] on hit (cn non-empty), None on miss.
    Cached in memory after first read.

    Cleans Collins-style dross: strips `<b>` HTML, drops parenthetical
    usage-context prefixes like `"(表示歉意)"` that leak from Collins' raw
    entries. The goal is that cn_paraphrase reads like a short, standalone
    gloss — not a dictionary entry fragment.
    """
    global _MOMO_REF_CACHE
    import json
    import os
    import re

    ref_path = os.environ.get("WORDFORGE_MOMO_REF_FILE")
    if not ref_path:
        return None
    if _MOMO_REF_CACHE is None:
        try:
            with open(ref_path, encoding="utf-8") as f:
                _MOMO_REF_CACHE = json.load(f)
        except (OSError, json.JSONDecodeError):
            _MOMO_REF_CACHE = {}
    entries = _MOMO_REF_CACHE.get(word) or []

    def _clean_cn(cn: str) -> str:
        # Drop leading parenthetical usage hint like "(表示歉意) 不过" → "不过".
        cn = re.sub(r"^[（(][^）)]{1,20}[）)]\s*", "", cn)
        return cn.strip()

    def _clean_en(en: str | None) -> str | None:
        if not en:
            return None
        en = re.sub(r"<[^>]+>", "", en).strip()
        return en or None

    cleaned: list[dict] = []
    for e in entries:
        cn_raw = e.get("cn")
        if not cn_raw:
            continue
        cn = _clean_cn(cn_raw)
        if not cn:
            continue
        cleaned.append({"pos": e.get("pos"), "cn": cn, "en": _clean_en(e.get("en"))})
    return cleaned or None


@dataclass
class ParaphraseStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    llm: LLMClient
    # Optional companion config for the rerank sub-call. Picked up from
    # [stages.paraphrase_rerank] in TOML (not registered as a real stage).
    # None → fall back to the historic haiku-4-5 on bedrock default.
    rerank_config: StageConfig | None = None
    name: str = field(default="paraphrase", init=False)

    def expected_fingerprint(self, *, word_id: int) -> str:
        upstream = self.artifacts.get(word_id=word_id, stage_name="fetch_dict")
        upstream_fp = upstream["fingerprint"] if upstream is not None else ""
        prompt_version = self.config.prompt_version or "v1"
        return fingerprint(
            upstream_fingerprints=[upstream_fp] if upstream_fp else [],
            stage_config={
                "parser_version": self.config.parser_version,
                "model": self.config.model,
            },
            prompt_version=prompt_version,
            prompt_content_hash=compute_prompt_content_hash("paraphrase", prompt_version),
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        import asyncio
        import time

        upstream = self.artifacts.get(word_id=word_id, stage_name="fetch_dict")
        if upstream is None:
            raise LookupError(f"fetch_dict missing for word_id={word_id}")
        raw_payload = upstream["payload"]
        raw_json = raw_payload.get("raw_json", {}) if isinstance(raw_payload, dict) else {}
        dict_summary = summarize_youdao_json(raw_json)

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT normalized_form FROM pipeline.words WHERE id = :id"),
                {"id": word_id},
            ).one()
        word = row[0]

        # Shortcut: if WORDFORGE_MOMO_REF_FILE is set and contains meanings
        # for this word, bypass the heavy paraphrase LLM. The legacy MySQL
        # word.meaning table already holds ~83% of vocabulary with Collins-
        # sourced cn/en/pos.
        #
        # BUT Collins orders meanings by dictionary tradition (historical
        # first), not modern usage frequency. If we pass that order to
        # downstream examples/mnemonic stages, they faithfully work on
        # archaic senses while skipping the common ones. Call a cheap
        # haiku-4-5 pass here to re-rank indices by modern-usage frequency.
        ref_meanings = _load_momo_ref(word)
        shortcut_source = None
        if ref_meanings:
            shortcut_source = "momo"
        else:
            # momo is missing cn for ~52% of vocabulary — but Youdao's `ec`
            # block usually has "v. 跑，奔跑；参加..." strings we can parse
            # deterministically. Free path; only falls through to opus LLM
            # if Youdao also has nothing structured.
            ec_meanings = extract_meanings_from_ec(raw_json)
            if ec_meanings:
                ref_meanings = ec_meanings
                shortcut_source = "youdao_ec"

        if ref_meanings:
            reordered, rerank_cost, rerank_ms = await _rerank_meanings(
                word=word,
                meanings=ref_meanings,
                llm=self.llm,
                rerank_cfg=self.rerank_config,
            )
            rerank_model = (self.rerank_config.model if self.rerank_config else None) or (
                "us.anthropic.claude-haiku-4-5-20251001-v1:0"
            )
            # Source tag carries the rerank provider+model so downstream
            # audits see "pipeline:youdao_ec+gemini:paraphrase_v1" etc.
            short_model_tag = rerank_model.rsplit(".", 1)[-1].rsplit("-", 1)[0]
            return StagePayload(
                payload={"meanings": reordered},
                source=(
                    f"pipeline:{shortcut_source}+{short_model_tag}:"
                    f"paraphrase_v{self.config.parser_version}"
                ),
                model=rerank_model if rerank_cost > 0 else None,
                prompt_version=self.config.prompt_version or "v1",
                cost_usd=rerank_cost,
                tokens_in=None,
                tokens_out=None,
                duration_ms=rerank_ms,
            )

        prompt_version = self.config.prompt_version or "v1"
        prompt_template = load_prompt("paraphrase", prompt_version)
        rendered_prompt = prompt_template.replace("{word}", word).replace(
            "{dict_summary}", dict_summary or "(no dictionary data)"
        )

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        completion = await loop.run_in_executor(
            None,
            lambda: self.llm.complete(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                rendered_prompt=rendered_prompt,
                request_params={"temperature": 0, "max_tokens": 2048},
                input_payload={"word": word, "summary_hash": str(hash(dict_summary))},
            ),
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        raw_text = completion.response.get("text", "")
        parsed = parse_llm_json(raw_text)

        resp = completion.response if isinstance(completion.response, dict) else {}
        return StagePayload(
            payload=parsed if isinstance(parsed, dict) else {"raw": parsed},
            source=source_str(
                provider=self.config.provider or "anthropic",
                model=self.config.model or "claude-opus-4",
                stage="paraphrase",
                parser_version=self.config.parser_version,
            ),
            model=self.config.model,
            prompt_version=prompt_version,
            cost_usd=completion.cost_usd,
            tokens_in=resp.get("in_tok"),
            tokens_out=resp.get("out_tok"),
            duration_ms=elapsed_ms,
        )
