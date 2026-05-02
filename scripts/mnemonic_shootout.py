"""Mnemonic model shootout — blind A/B/C comparison with opus-4-7 judge.

Problem: we want to pick the best model for the `mnemonic` stage under the
rule "quality weight = 2x cost weight". codex + gemini both recommended
qwen3-max over the current gemini-2.5-flash, but that's a $536 per-batch
increment without empirical backing. This script runs a blind evaluation
so the decision is data-driven.

Design:
- Seed word list: ~50 diverse English words drawn from multiple difficulty
  tiers (common / academic / idiomatic / tricky phonology) — hard-coded,
  not random, so re-runs are comparable.
- Phonetic: fetched via existing YoudaoClient (CacheStore-backed, free
  on cache hit). Each word needs phonetic_us to feed into the mnemonic
  prompt.
- Meanings: a one-line stub per word so the prompt has something in
  `meanings_json`. mnemonic prompt only uses meanings as context, not
  ground truth, so a compact pos+gloss is sufficient for evaluation.
- Candidate models (same v1 mnemonic prompt, same params):
    A = gemini-2.5-flash (current prod)
    B = qwen3-max (codex/gemini recommendation)
    C = claude-sonnet-4-6 (control, generalist strong)
- Judge: claude-opus-4-7 via Bedrock. Sees {word, phonetic, meanings,
  [M1, M2, M3]} in RANDOMLY SHUFFLED order with neutral labels. Scores
  each on three axes:
    1. hook_strength   (1-5: does the phonetic anchor actually trigger recall?)
    2. scene_vividness (1-5: aha-moment vs. flat description)
    3. chinese_natural (1-5: reads like natural Chinese, not translated English)
  Then picks a single winner.
- Output: jsonl (one row per word) + summary table (mean score per
  model, win count, $ per 50-word sample).

Why this design:
- Blind shuffle removes position + model bias in the judge.
- Opus judge burns ~$0.30 for the whole run (50 words × 3-way prompt)
  — cost is negligible compared to the $500+ decision at stake.
- Caches hit on re-runs, so iterating on prompt / adding models is cheap.
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from wordforge.cache import CacheStore
from wordforge.db.engine import make_engine
from wordforge.llm.anthropic_completer import register_if_env_key as _reg_anth
from wordforge.llm.bedrock_completer import register_if_env_key as _reg_bedrock
from wordforge.llm.client import LLMClient
from wordforge.llm.gemini_completer import register_if_env_key as _reg_gemini
from wordforge.llm.openai_completer import register_if_env_key as _reg_openai
from wordforge.llm.qwen_completer import register_if_env_key as _reg_qwen
from wordforge.sources.youdao import YoudaoClient

# Seed 50 words chosen for mnemonic difficulty diversity:
# - common/short (low phonetic pun difficulty): apple, water, happy
# - academic (high-value for learners): paradigm, hypothesis, synthesize
# - tricky phonology (where phonetic puns are hard): colonel, choir, yacht
# - idiomatic (multi-sense, harder to anchor): compound, leverage, subject
# - emotion/abstract (vivid-scene reward): nostalgia, melancholy, rapture
# Each entry: (form, pos, one_line_gloss_cn) — gloss is stub-quality, the
# judge only reads mnemonic output anyway.
SEED_WORDS: list[tuple[str, str, str]] = [
    ("apple", "n", "苹果"),
    ("water", "n", "水"),
    ("happy", "adj", "快乐的"),
    ("paradigm", "n", "范式、典型例子"),
    ("hypothesis", "n", "假设"),
    ("synthesize", "v", "合成、综合"),
    ("colonel", "n", "上校"),
    ("choir", "n", "唱诗班、合唱团"),
    ("yacht", "n", "游艇"),
    ("compound", "n/v", "化合物;使复杂"),
    ("leverage", "n/v", "杠杆作用;利用"),
    ("subject", "n/v", "主题;使遭受"),
    ("nostalgia", "n", "怀旧、乡愁"),
    ("melancholy", "n", "忧郁"),
    ("rapture", "n", "极度喜悦"),
    ("ephemeral", "adj", "短暂的"),
    ("ubiquitous", "adj", "无处不在的"),
    ("serendipity", "n", "意外发现美好事物的运气"),
    ("quintessential", "adj", "典型的、精髓的"),
    ("cacophony", "n", "刺耳的噪音"),
    ("juxtapose", "v", "并置"),
    ("procrastinate", "v", "拖延"),
    ("meticulous", "adj", "细致的"),
    ("obfuscate", "v", "使混淆"),
    ("pernicious", "adj", "有害的"),
    ("ameliorate", "v", "改善"),
    ("sanguine", "adj", "乐观的"),
    ("vicissitude", "n", "变迁、起伏"),
    ("penchant", "n", "癖好、嗜好"),
    ("ostensible", "adj", "表面上的"),
    ("zeitgeist", "n", "时代精神"),
    ("schadenfreude", "n", "幸灾乐祸"),
    ("laconic", "adj", "简洁的"),
    ("garrulous", "adj", "话多的"),
    ("pusillanimous", "adj", "胆小的"),
    ("magnanimous", "adj", "宽宏大量的"),
    ("discombobulate", "v", "使困惑"),
    ("ignominious", "adj", "可耻的"),
    ("salubrious", "adj", "有益健康的"),
    ("perfunctory", "adj", "敷衍的"),
    ("surreptitious", "adj", "偷偷摸摸的"),
    ("ostracize", "v", "排斥"),
    ("capitulate", "v", "投降"),
    ("recalcitrant", "adj", "倔强的"),
    ("propitious", "adj", "吉利的"),
    ("bellicose", "adj", "好斗的"),
    ("indefatigable", "adj", "不知疲倦的"),
    ("consternation", "n", "惊愕"),
    ("verisimilitude", "n", "逼真"),
    ("ignoramus", "n", "无知的人"),
]


MNEMONIC_PROMPT_PATH = Path(__file__).resolve().parents[1] / "src/wordforge/configs/prompts/mnemonic/v1.md"


@dataclass
class Candidate:
    label: str
    provider: str
    model: str
    text: str
    sound_alike: str
    cost_usd: float


def build_mnemonic_prompt(word: str, phonetic_us: str, meanings: list[dict]) -> str:
    template = MNEMONIC_PROMPT_PATH.read_text(encoding="utf-8")
    meanings_json = json.dumps(meanings, ensure_ascii=False)
    return (
        template.replace("{word}", word)
        .replace("{phonetic_us}", phonetic_us or "")
        .replace("{meanings_json}", meanings_json[:4000])
    )


async def fetch_phonetic(youdao: YoudaoClient, word: str) -> str:
    """Return phonetic_us or empty string (for words Youdao doesn't carry)."""
    try:
        result = await asyncio.to_thread(youdao.fetch, word)
    except Exception as e:  # noqa: BLE001
        print(f"  youdao fetch {word!r} failed: {e}", file=sys.stderr, flush=True)
        return ""
    raw = result.get("raw_json", {}) if isinstance(result, dict) else {}
    from wordforge.stages.phonetic import parse_phonetics
    parsed = parse_phonetics(raw)
    return parsed.get("phonetic_us") or ""


def parse_mnemonic_json(raw: str) -> tuple[str, str]:
    """Extract (mnemonic_text, sound_alike) from LLM output. Tolerant of fences."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json\n"):
            s = s[5:]
        s = s.rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        # Salvage: find first {...} block
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                return ("[parse-fail]", "")
        else:
            return ("[parse-fail]", "")
    return (
        str(obj.get("mnemonic", "")).strip(),
        str(obj.get("sound_alike", "")).strip(),
    )


async def run_one_candidate(
    llm: LLMClient, provider: str, model: str, label: str,
    word: str, phonetic_us: str, meanings: list[dict],
) -> Candidate:
    rendered = build_mnemonic_prompt(word, phonetic_us, meanings)
    t0 = time.perf_counter()
    try:
        completion = await asyncio.to_thread(
            llm.complete,
            provider=provider,
            model=model,
            rendered_prompt=rendered,
            request_params={"temperature": 0, "max_tokens": 1024},
            input_payload={"word": word, "phonetic_us": phonetic_us, "shootout": "mnemonic_v1"},
        )
    except Exception as e:  # noqa: BLE001
        print(f"  {label} ({provider}/{model}) failed on {word!r}: {e}", file=sys.stderr, flush=True)
        return Candidate(label=label, provider=provider, model=model, text="[error]", sound_alike="", cost_usd=0.0)
    elapsed = (time.perf_counter() - t0) * 1000
    raw_text = completion.response.get("text", "") if isinstance(completion.response, dict) else ""
    mnemonic, sound_alike = parse_mnemonic_json(raw_text)
    print(f"  {label} ({model}) {elapsed:.0f}ms ${completion.cost_usd:.5f} → {mnemonic[:40]}", flush=True)
    return Candidate(
        label=label, provider=provider, model=model,
        text=mnemonic, sound_alike=sound_alike, cost_usd=completion.cost_usd,
    )


JUDGE_PROMPT = """你是一位双语母语者,正在评估三个针对英文单词的中文谐音记忆术。你必须严格按要求输出,不加评论。

单词: {word}
音标: {phonetic_us}
释义: {meanings}

以下是三个候选,顺序已打乱,标记为 M1/M2/M3 (你不知道是哪家模型生成的):

M1 (谐音={m1_sa}): {m1_text}
M2 (谐音={m2_sa}): {m2_text}
M3 (谐音={m3_sa}): {m3_text}

评分规则(每个维度 1-5 分,5 最好):
- hook_strength   — 谐音是否真的能触发记忆关联?声音相似度 + 联想桥梁
- scene_vividness — 有没有"啊哈时刻"的画面感?vs 生硬描述单词意思
- chinese_natural — 中文读起来是否自然、像母语者写的?vs 翻译腔/生硬

然后从 M1/M2/M3 里选一个综合最佳。
如果有候选是 "[parse-fail]" 或 "[error]",相应项打 1 分,其他项按实际评。

严格输出 JSON,不要 markdown fence 和任何额外文字。
注意:字符串值里禁止出现英文双引号 " — 必须用中文引号 「」 或单引号 '。英文 " 会破坏 JSON 解析。
winner 字段必须出现在 reason 字段之前(方便解析)。

{{
  "M1": {{"hook_strength": <1-5>, "scene_vividness": <1-5>, "chinese_natural": <1-5>}},
  "M2": {{"hook_strength": <1-5>, "scene_vividness": <1-5>, "chinese_natural": <1-5>}},
  "M3": {{"hook_strength": <1-5>, "scene_vividness": <1-5>, "chinese_natural": <1-5>}},
  "winner": "M1",
  "reason": "一句话,用 「」 不用 \""
}}
"""


async def judge(
    llm: LLMClient, word: str, phonetic_us: str, meanings: list[dict],
    shuffled: list[Candidate],
) -> dict:
    """shuffled is a list of 3 Candidates in the order M1/M2/M3."""
    meanings_short = json.dumps(meanings, ensure_ascii=False)[:500]
    prompt = JUDGE_PROMPT.format(
        word=word, phonetic_us=phonetic_us or "?", meanings=meanings_short,
        m1_text=shuffled[0].text or "[empty]", m1_sa=shuffled[0].sound_alike or "",
        m2_text=shuffled[1].text or "[empty]", m2_sa=shuffled[1].sound_alike or "",
        m3_text=shuffled[2].text or "[empty]", m3_sa=shuffled[2].sound_alike or "",
    )
    try:
        completion = await asyncio.to_thread(
            llm.complete,
            provider="bedrock",
            model="us.anthropic.claude-opus-4-7",
            rendered_prompt=prompt,
            request_params={"temperature": 0, "max_tokens": 1024},
            input_payload={"word": word, "shootout_judge": "mnemonic_v1"},
        )
    except Exception as e:  # noqa: BLE001
        print(f"  JUDGE failed on {word!r}: {e}", file=sys.stderr, flush=True)
        return {"word": word, "error": str(e)}
    raw = completion.response.get("text", "") if isinstance(completion.response, dict) else ""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json\n"):
            s = s[5:]
        s = s.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                parsed = _salvage_judge(raw)
                if parsed is None:
                    return {"word": word, "error": "judge-json-parse-fail", "raw": raw[:300]}
        else:
            parsed = _salvage_judge(raw)
            if parsed is None:
                return {"word": word, "error": "judge-no-json", "raw": raw[:300]}
    parsed["_judge_cost"] = completion.cost_usd
    return parsed


_JUDGE_SCORE_RE = __import__("re").compile(
    r'"(M[123])"\s*:\s*\{[^}]*?"hook_strength"\s*:\s*(\d)[^}]*?"scene_vividness"\s*:\s*(\d)'
    r'[^}]*?"chinese_natural"\s*:\s*(\d)',
    __import__("re").DOTALL,
)
_JUDGE_WINNER_RE = __import__("re").compile(r'"winner"\s*:\s*"(M[123])"')


def _salvage_judge(raw: str) -> dict | None:
    """Regex-based fallback when JSON is malformed (usually unescaped \" in reason).

    We tolerate any damage to `reason` because the shootout doesn't need it —
    wins + scores are what the summary consumes.
    """
    out: dict = {}
    found_any = False
    for m in _JUDGE_SCORE_RE.finditer(raw):
        label, h, s, c = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        out[label] = {"hook_strength": h, "scene_vividness": s, "chinese_natural": c}
        found_any = True
    w = _JUDGE_WINNER_RE.search(raw)
    if w:
        out["winner"] = w.group(1)
    if not found_any:
        return None
    out["_salvaged"] = True
    return out


async def process_word(
    llm: LLMClient, youdao: YoudaoClient, form: str, pos: str, gloss_cn: str,
    out_fh, out_lock: asyncio.Lock, sem: asyncio.Semaphore,
) -> dict:
    async with sem:
        phonetic_us = await fetch_phonetic(youdao, form)
        meanings = [{"pos": pos, "cn_paraphrase": gloss_cn}]
        print(f"[{form}] phonetic={phonetic_us!r}", flush=True)
        cands = await asyncio.gather(
            run_one_candidate(llm, "gemini", "gemini-2.5-flash",       "flash",   form, phonetic_us, meanings),
            run_one_candidate(llm, "qwen",   "qwen3-max",              "qwen",    form, phonetic_us, meanings),
            run_one_candidate(llm, "bedrock", "us.anthropic.claude-sonnet-4-6", "sonnet", form, phonetic_us, meanings),
        )

        # Blind shuffle: map M1/M2/M3 → random candidates
        shuffled = list(cands)
        random.shuffle(shuffled)
        label_map = {"M1": shuffled[0].label, "M2": shuffled[1].label, "M3": shuffled[2].label}

        verdict = await judge(llm, form, phonetic_us, meanings, shuffled)

        record = {
            "word": form,
            "phonetic_us": phonetic_us,
            "pos": pos,
            "gloss_cn": gloss_cn,
            "candidates": {
                c.label: {
                    "model": c.model, "text": c.text,
                    "sound_alike": c.sound_alike, "cost": c.cost_usd,
                } for c in cands
            },
            "blind_label_map": label_map,  # M1 → 'flash' | 'qwen' | 'sonnet'
            "verdict": verdict,
        }
        async with out_lock:
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_fh.flush()
        return record


def summarize(records: list[dict]) -> None:
    labels = ["flash", "qwen", "sonnet"]
    scores: dict[str, dict[str, list[int]]] = {
        lab: {"hook_strength": [], "scene_vividness": [], "chinese_natural": []} for lab in labels
    }
    wins = {lab: 0 for lab in labels}
    costs = {lab: 0.0 for lab in labels}
    parse_failures = {lab: 0 for lab in labels}
    judge_cost_total = 0.0
    judge_failures = 0

    for rec in records:
        v = rec.get("verdict", {}) or {}
        if v.get("error"):
            judge_failures += 1
            continue
        judge_cost_total += v.get("_judge_cost", 0.0)
        mp = rec["blind_label_map"]  # {"M1": "flash", ...}
        for mx in ("M1", "M2", "M3"):
            real_label = mp[mx]
            s = v.get(mx, {})
            for axis in ("hook_strength", "scene_vividness", "chinese_natural"):
                val = s.get(axis)
                if isinstance(val, int):
                    scores[real_label][axis].append(val)
        win_mx = v.get("winner")
        if win_mx in mp:
            wins[mp[win_mx]] += 1

        for lab, c in rec["candidates"].items():
            costs[lab] += c.get("cost", 0.0)
            if c.get("text") in ("[parse-fail]", "[error]", ""):
                parse_failures[lab] += 1

    n = len(records)
    print(f"\n{'='*80}\nSHOOTOUT SUMMARY  ({n} words; {judge_failures} judge failures)\n{'='*80}")
    print(f"{'model':<10} {'hook':>6} {'scene':>6} {'cn':>6} {'avg':>6} {'wins':>6} {'fails':>6} {'cost':>10} {'/122k':>10}")
    for lab in labels:
        def avg(xs): return statistics.mean(xs) if xs else 0.0
        a, b, c = (
            avg(scores[lab]["hook_strength"]),
            avg(scores[lab]["scene_vividness"]),
            avg(scores[lab]["chinese_natural"]),
        )
        overall = (a + b + c) / 3 if (a or b or c) else 0.0
        projected = costs[lab] / n * 122_000 if n else 0.0
        print(f"{lab:<10} {a:>6.2f} {b:>6.2f} {c:>6.2f} {overall:>6.2f} {wins[lab]:>6} {parse_failures[lab]:>6} ${costs[lab]:>8.4f} ${projected:>8.0f}")
    print(f"\njudge total: ${judge_cost_total:.4f} ({n} calls of opus-4-7)")


async def main_async(args):
    engine = make_engine()
    try:
        completers: dict = {}
        for reg in (_reg_bedrock, _reg_anth, _reg_gemini, _reg_openai, _reg_qwen):
            completers.update(reg())
        needed = {"gemini", "qwen", "bedrock"}
        missing = needed - set(completers)
        if missing:
            print(f"ERROR: missing LLM provider(s) {sorted(missing)}. "
                  f"Set GEMINI_API_KEY / DASHSCOPE_API_KEY / AWS_BEARER_TOKEN_BEDROCK.", file=sys.stderr)
            return 2
        store = CacheStore(engine)
        llm = LLMClient(store=store, completers=completers)
        http = httpx.Client(
            base_url="https://dict.youdao.com", timeout=httpx.Timeout(10.0),
            headers={"User-Agent": "wordforge-shootout/1.0"},
        )
        youdao = YoudaoClient(store=store, http=http)

        words = SEED_WORDS[: args.limit] if args.limit else SEED_WORDS
        random.seed(42)  # reproducible shuffle positions across runs

        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_fh = open(out_path, "w", encoding="utf-8")  # noqa: SIM115
        out_lock = asyncio.Lock()
        sem = asyncio.Semaphore(args.concurrency)

        try:
            tasks = [
                process_word(llm, youdao, w[0], w[1], w[2], out_fh, out_lock, sem)
                for w in words
            ]
            records = await asyncio.gather(*tasks)
        finally:
            out_fh.close()
            http.close()

        summarize(records)
        print(f"\nwrote per-word jsonl to {out_path}")
        return 0
    finally:
        engine.dispose()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=0, help="limit seed words (0=all)")
    p.add_argument("--concurrency", type=int, default=5, help="parallel words")
    p.add_argument("--output", default="/tmp/wordforge_smoke/mnemonic_shootout.jsonl")
    args = p.parse_args()
    rc = asyncio.run(main_async(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
