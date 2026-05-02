"""Per-word review worker + LLM call plumbing.

Two layers:
- llm_call_sync  — direct LLMClient.complete() call (cache-aware).
  Used when the caller is already in a background thread (e.g. the
  review script used to call it inside a ThreadPoolExecutor).
- llm_call_async — asyncio wrapper that holds a Semaphore + asyncio.wait_for,
  so 20 concurrent words share a bounded in-flight set and a half-dead
  proxy can't hang an individual call forever.

run_one_word does the full per-word lifecycle:
  blob → 5 haiku checkers in parallel → aggregate issues → opus fixer →
  JSON patches → apply_patches_for_word (in a single DB txn).

Each LLM call routes through wordforge.llm.client.LLMClient so responses
land in pipeline.external_call_cache — rerunning the same review word
is near-free after the first pass.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from wordforge.llm.client import LLMClient
from wordforge.reviewer.blob import build_word_blob, parse_llm_text
from wordforge.reviewer.config import CFG
from wordforge.reviewer.patch import apply_patches_for_word
from wordforge.reviewer.prompts import CHECKERS, OPUS_FIXER


def llm_call_sync(
    llm: LLMClient,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    checker: str,
) -> tuple[str, float]:
    """Sync LLM call (cache-aware). `checker` is hashed into input_payload
    so two checkers reusing the same (model, prompt) still cache separately.
    """
    request_params: dict[str, Any] = {"max_tokens": max_tokens}
    if "claude-opus-4-7" not in model:
        request_params["temperature"] = 0
    completion = llm.complete(
        provider="bedrock",
        model=model,
        rendered_prompt=prompt,
        request_params=request_params,
        input_payload={"script": "review_and_fix", "checker": checker},
    )
    resp = completion.response
    text = resp.get("text", "") if isinstance(resp, dict) else ""
    # Empty text is legal: the completer returns it for Bedrock
    # content_filtered — checker treats it as "no issues"; opus fixer
    # treats it as "no patches". Other empty cases raise in the completer.
    return text, float(completion.cost_usd or 0.0)


async def llm_call_async(
    llm: LLMClient,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    checker: str,
    sem: asyncio.Semaphore,
    timeout: float,
) -> tuple[str, float]:
    """asyncio wrapper. Global Semaphore caps concurrent in-flight calls.
    `timeout` is a hard per-call ceiling; boto3's internal timeouts handle
    the happy path, this catches proxy-died-mid-handshake pathology.
    """
    async with sem:
        return await asyncio.wait_for(
            asyncio.to_thread(
                llm_call_sync,
                llm,
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                checker=checker,
            ),
            timeout=timeout,
        )


async def _run_checker(
    llm: LLMClient,
    name: str,
    prompt: str,
    sem: asyncio.Semaphore,
    timeout: float,
) -> tuple[str, list[dict], float]:
    """Single haiku checker. Returns (name, tagged_issues, cost)."""
    text, cost = await llm_call_async(
        llm,
        model=CFG.HAIKU_MODEL,
        prompt=prompt,
        max_tokens=CFG.HAIKU_MAX_TOKENS,
        checker=name,
        sem=sem,
        timeout=timeout,
    )
    parsed = parse_llm_text(text)
    if not isinstance(parsed, dict) or "issues" not in parsed:
        return name, [], cost
    issues = parsed.get("issues") or []
    if not isinstance(issues, list):
        return name, [], cost
    tagged = [{"from": name, **i} for i in issues if isinstance(i, dict)]
    return name, tagged, cost


async def run_one_word(
    engine,
    llm: LLMClient,
    form: str | None,
    word_id: int | None,
    apply_: bool,
    sem: asyncio.Semaphore,
    call_timeout: float,
) -> dict:
    """End-to-end for one word. All blocking work goes through
    asyncio.to_thread so the event loop stays live.
    """
    blob = await asyncio.to_thread(build_word_blob, engine, form, word_id)
    if not blob:
        return {"form": form, "skipped": "not found in domain.words"}

    blob_json = json.dumps(blob, ensure_ascii=False)
    truncated = len(blob_json) > CFG.BLOB_CHAR_LIMIT
    blob_json_truncated = blob_json[:CFG.BLOB_CHAR_LIMIT] if truncated else blob_json

    # Phase 1: 5 checkers in parallel via gather.
    checker_coros = [
        _run_checker(
            llm,
            name,
            prompt_tmpl.replace("{blob_json}", blob_json_truncated),
            sem,
            call_timeout,
        )
        for name, prompt_tmpl in CHECKERS
    ]
    results = await asyncio.gather(*checker_coros)

    checker_results: dict[str, dict] = {n: {"issues": i, "cost": c} for n, i, c in results}
    all_issues: list[dict] = [i for _, issues, _ in results for i in issues]
    haiku_cost = sum(c for _, _, c in results)

    rec: dict = {
        "form": blob.get("form"),
        "word_id": blob.get("word_id"),
        "checker_costs": {k: round(v["cost"], 5) for k, v in checker_results.items()},
        "haiku_total": round(haiku_cost, 4),
        "issues": all_issues,
    }
    if truncated:
        rec["blob_truncated"] = True

    if not all_issues:
        return rec

    # Phase 2: opus fixer.
    opus_prompt = (
        OPUS_FIXER
        .replace("{blob_json}", blob_json_truncated)
        .replace(
            "{issues_json}",
            json.dumps(all_issues, ensure_ascii=False)[:CFG.ISSUES_CHAR_LIMIT],
        )
    )
    opus_text, opus_cost = await llm_call_async(
        llm,
        model=CFG.OPUS_MODEL,
        prompt=opus_prompt,
        max_tokens=CFG.OPUS_MAX_TOKENS,
        checker="opus_fixer",
        sem=sem,
        timeout=call_timeout,
    )
    rec["opus_cost"] = round(opus_cost, 4)
    opus_parsed = parse_llm_text(opus_text)
    if opus_parsed is None or "patches" not in opus_parsed:
        rec["opus_parse_err"] = opus_text[:CFG.OPUS_PARSE_ERR_CHAR_LIMIT]
        return rec

    patches = opus_parsed.get("patches") or []
    rec["patches"] = patches

    if not apply_:
        return rec

    # Phase 3: apply patches in one DB txn (off-loop).
    applied, drift_skipped = await asyncio.to_thread(
        apply_patches_for_word, engine, blob["word_id"], patches
    )
    rec["applied_count"] = applied
    if drift_skipped:
        rec["drift_skipped"] = drift_skipped
    return rec
