"""Backfill domain.sentences for meanings that currently have 0 sentences.

Uses sonnet-4-6 (quality/price sweet spot for sentence generation) with a
prompt that biases toward "classic / cite-able" examples (famous quotes,
well-known speeches, canonical collocations) when they fit the sense.

Dry-run by default. Pass --commit to actually INSERT into domain.sentences.

Usage:
  uv run python scripts/backfill_sentences.py --limit 5 [--commit]
  uv run python scripts/backfill_sentences.py --limit 5536 --commit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import sqlalchemy as sa


BACKFILL_PROMPT = """You are a lexicographer writing 2 example sentences for a specific sense of an English word. Your priority, in order:

1. Sense accuracy — sentences MUST illustrate exactly the given meaning, disambiguated from the word's other senses.
2. Cite-worthy / memorable when it fits: a famous quote, well-known speech, canonical collocation, or a line a learner might actually encounter in books/news/film. When no clean real quote fits, write a short, vivid, everyday B1-B2 sentence.
3. Natural Chinese translation: fluent, not word-for-word.
4. Brevity: ≤15 words per English sentence.

Input:
  word: "{form}"
  pos: "{pos_name}"
  target sense: "{cn_paraphrase}"
  en_gloss (Collins, truncated): "{en_paraphrase}"
  other senses of this word (DO NOT mix these in): {other_senses}

Output strict JSON (no markdown fence, no prose):
{
  "examples": [
    {"en": "<sentence 1>", "cn": "<translation 1>", "origin": "<'Einstein 1931' or 'Obama inaugural 2009' or 'everyday'>"},
    {"en": "<sentence 2>", "cn": "<translation 2>", "origin": "<...>"}
  ]
}

If you use a real quote, cite its origin concisely (author + year or work name). If it's an everyday example, set origin to "everyday". Never fabricate an attribution — when unsure, use "everyday".
"""


# momo pos encoding (inferred empirically from meaning table samples):
#   1=noun, 2=verb, 3=adjective, 4=number, 5=pronoun, 6=adverb,
#   7=article, 8=preposition, 9=conjunction, 10=interjection,
#   201=phrasal verb
_POS_NAME = {
    1: "noun", 2: "verb", 3: "adjective", 4: "number", 5: "pronoun",
    6: "adverb", 7: "article", 8: "preposition", 9: "conjunction",
    10: "interjection", 201: "phrasal verb",
}


def call_bedrock(client, model: str, prompt: str, max_tokens: int) -> tuple[str, float, float]:
    cfg: dict[str, Any] = {"maxTokens": max_tokens}
    if "claude-opus-4-7" not in model:
        cfg["temperature"] = 0
    t0 = time.perf_counter()
    resp = client.converse(
        modelId=model,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=cfg,
    )
    elapsed = time.perf_counter() - t0
    content = resp.get("output", {}).get("message", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    usage = resp.get("usage", {})
    in_tok = usage.get("inputTokens", 0)
    out_tok = usage.get("outputTokens", 0)
    if "opus-4-7" in model:
        cost = (in_tok / 1e6 * 15) + (out_tok / 1e6 * 75)
    elif "haiku-4-5" in model:
        cost = (in_tok / 1e6 * 1) + (out_tok / 1e6 * 5)
    else:
        cost = (in_tok / 1e6 * 3) + (out_tok / 1e6 * 15)
    return text, cost, elapsed


def parse_json(text: str) -> Any | None:
    # Reuse the production parser which handles markdown fences, prose
    # prefixes, AND unescaped nested quotes (the exact bug that killed the
    # first 1k-word smoke).
    from wordforge.stages._llm_base import parse_llm_json
    try:
        return parse_llm_json(text)
    except (ValueError, Exception):  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="max meanings to backfill")
    ap.add_argument("--commit", action="store_true", help="actually INSERT (default is dry-run)")
    ap.add_argument("--output", default="/tmp/wordforge_smoke/backfill_sentences.jsonl")
    args = ap.parse_args()

    import boto3  # type: ignore[import-not-found]
    from wordforge.db.engine import make_engine

    engine = make_engine()
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    # Pull meanings without any sentence.
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                """
                SELECT m.meaning_id, m.word_id, m.pos, m.cn_paraphrase, m.en_paraphrase, w.form
                FROM domain.meanings m
                JOIN domain.words w ON w.word_id = m.word_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM domain.sentences s WHERE s.meaning_id = m.meaning_id
                )
                ORDER BY m.meaning_id
                LIMIT :lim
                """
            ),
            {"lim": args.limit},
        ).mappings().all()

    # Preload other_senses for each meaning in one query — much cheaper than
    # per-meaning lookups under high concurrency.
    other_senses_map: dict[int, list[str]] = {}
    with engine.connect() as conn:
        all_meanings = conn.execute(
            sa.text(
                "SELECT meaning_id, word_id, cn_paraphrase FROM domain.meanings "
                "WHERE word_id IN (SELECT DISTINCT word_id FROM domain.meanings m2 "
                "                   WHERE NOT EXISTS (SELECT 1 FROM domain.sentences s WHERE s.meaning_id=m2.meaning_id)"
                "                     AND m2.meaning_id IN :ids)"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": [r["meaning_id"] for r in rows]},
        ).all()
    by_word: dict[int, list[tuple[int, str]]] = {}
    for m in all_meanings:
        by_word.setdefault(m[1], []).append((m[0], m[2]))
    for r in rows:
        peers = by_word.get(r["word_id"], [])
        other_senses_map[r["meaning_id"]] = [cn for mid, cn in peers if mid != r["meaning_id"] and cn][:8]

    print(f"found {len(rows)} meanings to backfill (--commit={args.commit})")

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lock = threading.Lock()
    totals = {"cost": 0.0, "inserted": 0, "skipped": 0, "done": 0}

    def worker(r: dict) -> dict:
        prompt = (
            BACKFILL_PROMPT
            .replace("{form}", r["form"])
            .replace("{pos_name}", _POS_NAME.get(r["pos"], str(r["pos"])))
            .replace("{cn_paraphrase}", r["cn_paraphrase"] or "")
            .replace("{en_paraphrase}", (r["en_paraphrase"] or "")[:250])
            .replace("{other_senses}", json.dumps(other_senses_map.get(r["meaning_id"], []), ensure_ascii=False))
        )
        text, cost, _ = call_bedrock(
            client, "us.anthropic.claude-sonnet-4-6", prompt, 800
        )
        parsed = parse_json(text)
        record = {
            "meaning_id": r["meaning_id"], "word_id": r["word_id"],
            "form": r["form"], "cn": r["cn_paraphrase"],
            "cost": round(cost, 5),
        }
        ins = 0
        if parsed is None or "examples" not in parsed:
            record["error"] = text[:300]
            with lock:
                totals["skipped"] += 1
        else:
            examples = parsed.get("examples", [])
            record["examples"] = examples
            if args.commit and examples:
                with engine.begin() as conn:
                    for ex in examples:
                        en = ex.get("en")
                        cn = ex.get("cn")
                        origin = ex.get("origin", "everyday")
                        if not en or not cn:
                            continue
                        src = f"pipeline:bedrock:us.anthropic.claude-sonnet-4-6:backfill_v1[{origin}]"
                        conn.execute(
                            sa.text(
                                "INSERT INTO domain.sentences (meaning_id, form, translation, source) "
                                "VALUES (:mid, :en, :cn, :src)"
                            ),
                            {"mid": r["meaning_id"], "en": en, "cn": cn, "src": src[:255]},
                        )
                        ins += 1
        with lock:
            totals["cost"] += cost
            totals["inserted"] += ins
            totals["done"] += 1
            if totals["done"] % 50 == 0 or totals["done"] == len(rows):
                print(
                    f"  [{totals['done']}/{len(rows)}] cost=${totals['cost']:.2f}  "
                    f"inserted={totals['inserted']}  skipped={totals['skipped']}"
                )
        return record

    with open(args.output, "w", encoding="utf-8") as out, \
         ThreadPoolExecutor(max_workers=15) as ex:
        futures = [ex.submit(worker, dict(r)) for r in rows]
        for fut in as_completed(futures):
            # fail-loud: raise any worker exception so the operator sees it,
            # instead of burying 1000 failures in the jsonl.
            rec = fut.result()
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

    print(f"\nDone. total cost=${totals['cost']:.4f}  inserted={totals['inserted']}  skipped={totals['skipped']}")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
