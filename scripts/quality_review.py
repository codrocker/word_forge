"""quality_review.py — LLM review of a word's final app.* data.

Reads the full stack (word + meanings + sentences + mnemonics + derivatives)
from `app.*`, wraps it into a structured JSON blob, and asks opus-4-7 to
identify issues at each JSON path. Prints the review to stdout.

Usage:
  uv run python scripts/quality_review.py --word apple
  uv run python scripts/quality_review.py --word-id 42
  uv run python scripts/quality_review.py --batch BATCH_CN01 --limit 20

Output format per reviewed word:
  word: "foo"
  issues:
    - path: meanings[2].cn_paraphrase
      severity: high
      issue: "..."
      suggestion: "..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import sqlalchemy as sa


REVIEW_PROMPT = """You are a senior bilingual lexicographer reviewing the quality of a vocabulary-app entry. Find substantive problems — not style preferences — and point to the exact JSON path.

Focus on these quality criteria (ordered by importance):
1. Meanings: cn_paraphrase is fluent, accurate, and appropriate register. Not Collins-raw verbosity like "用于名词词组前，指前面已经提及的人或物". Should read like a short gloss a learner would scan in 1 second.
2. Morphological tags: if a meaning is a variant form (e.g. "[give 过去式]"), that's correct only if the English form IS that variant. A noun shouldn't be tagged as "过去式".
3. Examples (sentences): natural English at B1-B2 level, fluent Chinese translation, NOT word-for-word. Each example should showcase a common collocation. CN translation must not be stiff.
4. Mnemonic: Chinese phonetic-pun that creates a memory hook. "Leverage association" — bridges the English sound + a vivid Chinese scene. Not a boring description of meaning. NOT offensive/political/inappropriate.
5. Derivatives (synonyms/antonyms): reasonable for the meaning's sense — not mismatched (e.g. synonyms of "apple" shouldn't be "wear, put on").
6. Pos consistency: pos tags match the actual grammatical role.

Input (the full app.* stack for one word):
```
{word_blob}
```

Output strict JSON, no markdown fence, no prose:
{
  "issues": [
    {
      "path": "<dotted path like meanings[0].cn_paraphrase or mnemonic.text>",
      "severity": "low|medium|high",
      "issue": "<1 sentence describing the problem>",
      "suggestion": "<concrete fix, one phrase or short sentence>"
    }
  ]
}

Rules:
- If the entry is fine, return `{"issues": []}`.
- Do NOT flag stylistic preferences (e.g. "could be more elegant"). Flag only: factual errors, unnatural Chinese, wrong pos, mismatched derivatives, offensive mnemonics, morphological tag errors, or truly broken fields.
- Keep each issue specific to ONE path.
- Max 10 issues per word.
"""


def build_word_blob(engine, form: str | None, word_id: int | None) -> dict[str, Any] | None:
    """Pull word + meanings + sentences + mnemonics from app.* into one dict."""
    with engine.connect() as conn:
        if word_id is not None:
            w_row = conn.execute(
                sa.text("SELECT * FROM domain.words WHERE word_id = :w"),
                {"w": word_id},
            ).mappings().first()
        else:
            w_row = conn.execute(
                sa.text("SELECT * FROM domain.words WHERE form = :f ORDER BY word_id LIMIT 1"),
                {"f": form},
            ).mappings().first()
        if not w_row:
            return None
        w_dict = dict(w_row)
        wid = w_dict["word_id"]
        # Don't include verbose derivatives JSON in the blob we send to LLM —
        # too many tokens. Keep it as a compact summary.
        deriv = w_dict.get("derivatives")
        if isinstance(deriv, dict):
            per = deriv.get("per_meaning", [])
            w_dict["derivatives_summary"] = [
                {"i": p.get("meaning_index"), "syn": p.get("synonyms"), "ant": p.get("antonyms")}
                for p in per[:6]
            ]
        w_dict.pop("derivatives", None)
        w_dict.pop("created_at", None)
        w_dict.pop("updated_at", None)

        ms = conn.execute(
            sa.text(
                "SELECT meaning_id, pos, cn_paraphrase, en_paraphrase "
                "FROM domain.meanings WHERE word_id=:w ORDER BY meaning_id"
            ),
            {"w": wid},
        ).mappings().all()
        meanings: list[dict] = []
        for m in ms:
            mid = m["meaning_id"]
            sents = conn.execute(
                sa.text(
                    "SELECT form AS en, translation AS cn FROM domain.sentences "
                    "WHERE meaning_id=:mid ORDER BY sentence_id"
                ),
                {"mid": mid},
            ).all()
            meanings.append(
                {
                    "pos": m["pos"],
                    "cn": m["cn_paraphrase"],
                    "en": (m["en_paraphrase"] or "")[:200],
                    "examples": [{"en": s[0], "cn": s[1]} for s in sents],
                }
            )
        w_dict["meanings"] = meanings

        mn = conn.execute(
            sa.text("SELECT content FROM domain.mnemonics WHERE word_id=:w"),
            {"w": wid},
        ).first()
        if mn:
            content = mn[0] if isinstance(mn[0], dict) else json.loads(mn[0])
            w_dict["mnemonic"] = content

    return w_dict


def review_word(blob: dict, model: str = "us.anthropic.claude-opus-4-7") -> dict:
    import boto3  # type: ignore[import-not-found]

    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    prompt = REVIEW_PROMPT.replace("{word_blob}", json.dumps(blob, ensure_ascii=False, indent=2))
    cfg = {"maxTokens": 1500}
    if "claude-opus-4-7" not in model:
        cfg["temperature"] = 0
    t0 = time.perf_counter()
    response = client.converse(
        modelId=model,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=cfg,
    )
    elapsed = time.perf_counter() - t0
    content = response.get("output", {}).get("message", {}).get("content", [])
    if not content:
        return {"issues": [], "_error": f"empty content, stop_reason={response.get('stopReason')}"}
    text = content[0].get("text", "")
    usage = response.get("usage", {})
    in_tok = usage.get("inputTokens", 0)
    out_tok = usage.get("outputTokens", 0)
    # rough cost
    cost = (in_tok / 1_000_000 * 15.0) + (out_tok / 1_000_000 * 75.0)
    # strip markdown fence if present
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = {"issues": [], "_parse_error": text[:300]}
    parsed["_cost"] = cost
    parsed["_in_tok"] = in_tok
    parsed["_out_tok"] = out_tok
    parsed["_elapsed_s"] = round(elapsed, 2)
    return parsed


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--word", help="form to review (lookup in domain.words)")
    ap.add_argument("--word-id", type=int, help="domain.words.word_id to review")
    ap.add_argument("--batch", help="batch_id to review from pipeline.words → domain.words")
    ap.add_argument("--limit", type=int, default=1, help="max words to review (for --batch)")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    from wordforge.db.engine import make_engine  # local import to avoid top-level cost

    engine = make_engine()

    targets: list[tuple[str, int | None]] = []
    if args.batch:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT w.normalized_form, aw.word_id "
                    "FROM pipeline.words w "
                    "JOIN domain.words aw ON aw.word_id = w.app_word_id "
                    "WHERE w.batch_id=:b AND w.status='done' "
                    "ORDER BY w.id LIMIT :lim"
                ),
                {"b": args.batch, "lim": args.limit},
            ).all()
        targets = [(r[0], r[1]) for r in rows]
    elif args.word:
        targets = [(args.word, None)]
    elif args.word_id:
        targets = [(None, args.word_id)]  # type: ignore[list-item]
    else:
        print("error: pass --word, --word-id, or --batch", file=sys.stderr)
        return 2

    total_cost = 0.0
    for form, wid in targets:
        blob = build_word_blob(engine, form=form, word_id=wid)
        if not blob:
            print(f"[{form or wid}] not found in domain.words", file=sys.stderr)
            continue
        print("=" * 70)
        print(f"reviewing: form={blob.get('form')!r} word_id={blob.get('word_id')}")
        print("=" * 70)
        result = review_word(blob)
        total_cost += result.get("_cost", 0)
        # Print the blob so user can compare
        print("\n--- INPUT BLOB ---")
        print(json.dumps({k: v for k, v in blob.items() if not k.startswith("_")}, ensure_ascii=False, indent=2)[:2500])
        print("\n--- REVIEW ---")
        print(f"cost=${result.get('_cost', 0):.4f}  in={result.get('_in_tok')}  out={result.get('_out_tok')}  elapsed={result.get('_elapsed_s')}s")
        issues = result.get("issues", [])
        if not issues:
            print("  ✅ no issues found")
        for i, issue in enumerate(issues):
            print(f"\n  #{i+1} [{issue.get('severity', '?')}] path={issue.get('path')}")
            print(f"      issue: {issue.get('issue')}")
            print(f"      fix:   {issue.get('suggestion')}")
        if "_parse_error" in result:
            print(f"\n  (parse warn: {result['_parse_error'][:200]})")
        print()
    print(f"\n=== TOTAL cost: ${total_cost:.4f} for {len(targets)} words ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
