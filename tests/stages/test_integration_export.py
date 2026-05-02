"""P5c Task 2: Integration test — seed a word through all 8 stages via runner.

Uses stub completers for LLM stages (paraphrase/derivatives/examples/mnemonic).
Verifies domain.words/meanings/mnemonics rows appear after the full pipeline run.
"""

from __future__ import annotations

import asyncio
import json

import sqlalchemy as sa

from wordforge.cache import CacheStore
from wordforge.config import StageConfig, WordforgeConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.llm.client import LLMClient, LLMCompletion
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.pipeline.budget import BudgetGate
from wordforge.pipeline.runner import StageRunner
from wordforge.pipeline.runs import StageRunStore
from wordforge.stages.registry import build_stages


def _stub_paraphrase(*, model, prompt, **kw):
    return LLMCompletion(
        response={
            "text": json.dumps(
                {
                    "meanings": [{"pos": "n", "cn": "苹果", "en": "a round fruit"}],
                    "structure": {"root": "apple"},
                }
            )
        },
        cost_usd=0.004,
    )


def _stub_derivatives(*, model, prompt, **kw):
    return LLMCompletion(
        response={
            "text": json.dumps(
                {
                    "word_forms": {"plural": "apples", "past_tense": None},
                    "per_meaning": [
                        {
                            "meaning_index": 0,
                            "equivalents": ["fruit"],
                            "synonyms": ["pome"],
                            "antonyms": [],
                        }
                    ],
                }
            )
        },
        cost_usd=0.002,
    )


def _stub_examples(*, model, prompt, **kw):
    return LLMCompletion(
        response={
            "text": json.dumps(
                {
                    "per_meaning": [
                        {
                            "meaning_index": 0,
                            "examples": [{"en": "I ate an apple.", "cn": "我吃了一个苹果。"}],
                        }
                    ],
                }
            )
        },
        cost_usd=0.003,
    )


def _stub_mnemonic(*, model, prompt, **kw):
    return LLMCompletion(
        response={
            "text": json.dumps(
                {
                    "mnemonic": "Apple谐音阿婆，阿婆卖苹果",
                    "kind": "phonetic",
                }
            )
        },
        cost_usd=0.003,
    )


def _stub_completer(*, model, prompt, **kw):
    """Route to the correct stub based on prompt content heuristics."""
    # In production each stage has its own prompt; for test we route by
    # checking which stage prompt was loaded (they contain distinct markers).
    text = prompt if isinstance(prompt, str) else str(prompt)
    # Route by the strongest distinctive token — prompt files have evolved,
    # so check the most specific markers first.
    if "sound_alike" in text or "谐音助记" in text or "mnemonic" in text.lower():
        return _stub_mnemonic(model=model, prompt=prompt, **kw)
    if "per_meaning" in text and "examples" in text:
        return _stub_examples(model=model, prompt=prompt, **kw)
    if "word_forms" in text:
        return _stub_derivatives(model=model, prompt=prompt, **kw)
    return _stub_paraphrase(model=model, prompt=prompt, **kw)


def test_full_pipeline_8_stages_produces_app_rows(at_head):
    """Seed 'apple', run all 8 stages, verify domain.words + meanings + mnemonics."""
    engine = make_engine()
    ingest_words(engine, raw_forms=["apple"], batch_id="B_INT")

    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id = 'B_INT'")
        ).scalar_one()

    # Build full config with all 8 stages
    stage_names = [
        "fetch_dict",
        "paraphrase",
        "phonetic",
        "derivatives",
        "examples",
        "mnemonic",
        "quality_gate",
        "export",
    ]
    stages_cfg = {
        name: StageConfig(
            parser_version="1",
            cost_estimate_usd=0.0,
            prompt_version="v1"
            if name in ("paraphrase", "derivatives", "examples", "mnemonic")
            else None,
            model="claude-opus-4"
            if name in ("paraphrase", "derivatives", "examples", "mnemonic")
            else None,
        )
        for name in stage_names
    }
    config = WordforgeConfig(stages=stages_cfg, default_budget_cap_usd=None)

    # Wire stub LLM
    cache = CacheStore(engine)
    llm = LLMClient(store=cache, completers={"anthropic": _stub_completer})
    artifacts = StageArtifactStore(engine)
    runs = StageRunStore(engine)
    budget = BudgetGate(engine)

    # Need to set WORDFORGE_STUB_YOUDAO_JSON for fetch_dict
    import os

    os.environ["WORDFORGE_STUB_YOUDAO_JSON"] = (
        '{"simple":{"word":[{"usphone":"ˈæp(ə)l","ukphone":"ˈæp(ə)l",'
        '"usspeech":"apple&type=2","ukspeech":"apple&type=1"}]}}'
    )
    try:
        built_stages = build_stages(config, engine=engine, artifacts=artifacts, llm=llm)
        assert len(built_stages) == 8
        assert [s.name for s in built_stages] == stage_names

        runner = StageRunner(artifacts=artifacts, runs=runs, budget=budget)
        result = asyncio.run(
            runner.run(stages=built_stages, word_ids=[wid], batch_id="B_INT", force=False)
        )
    finally:
        os.environ.pop("WORDFORGE_STUB_YOUDAO_JSON", None)

    # All 8 stages should succeed for 1 word
    assert result.ok_events == 8
    assert result.failed_events == 0

    # Verify domain.words
    with engine.begin() as conn:
        app_row = conn.execute(
            sa.text(
                "SELECT word_id, form, type, phonetic_us, plural, source "
                "FROM domain.words WHERE form = 'apple'"
            )
        ).one()
    assert app_row[1] == "apple"
    assert app_row[2] == 1
    assert app_row[4] == "apples"
    assert app_row[5].startswith("pipeline:")

    app_word_id = app_row[0]

    # Verify domain.meanings
    with engine.begin() as conn:
        meanings = conn.execute(
            sa.text("SELECT cn_paraphrase, pos, source FROM domain.meanings WHERE word_id = :w"),
            {"w": app_word_id},
        ).all()
    assert len(meanings) == 1
    assert meanings[0][0] == "苹果"
    assert meanings[0][1] == 1  # n → 1
    assert meanings[0][2].startswith("pipeline:")

    # Verify domain.mnemonics
    with engine.begin() as conn:
        mnemonics = conn.execute(
            sa.text("SELECT type, content, source FROM domain.mnemonics WHERE word_id = :w"),
            {"w": app_word_id},
        ).all()
    assert len(mnemonics) == 1
    assert mnemonics[0][0] == 1
    content = json.loads(mnemonics[0][1]) if isinstance(mnemonics[0][1], str) else mnemonics[0][1]
    assert "阿婆" in content["text"]
    assert mnemonics[0][2].startswith("pipeline:")

    # Verify pipeline.words status updated
    with engine.begin() as conn:
        pw = conn.execute(
            sa.text("SELECT status, app_word_id FROM pipeline.words WHERE id = :w"),
            {"w": wid},
        ).one()
    assert pw[0] == "done"
    assert pw[1] == app_word_id


def test_registry_includes_quality_gate_and_export(at_head):
    """Registry builds quality_gate + export when config includes them."""
    engine = make_engine()
    stages_cfg = {
        name: StageConfig(parser_version="1", cost_estimate_usd=0.0)
        for name in ["quality_gate", "export"]
    }
    config = WordforgeConfig(stages=stages_cfg, default_budget_cap_usd=None)
    artifacts = StageArtifactStore(engine)
    built = build_stages(config, engine=engine, artifacts=artifacts)
    assert [s.name for s in built] == ["quality_gate", "export"]
