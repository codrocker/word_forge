"""P5b Task 3: ExamplesStage using a stub completer."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from wordforge.cache import CacheStore
from wordforge.config import StageConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.llm.client import LLMClient, LLMCompletion
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.examples import ExamplesStage, _extract_reference_examples

_STUB_RESPONSE = (
    '{"per_meaning":[{"meaning_index":0,"examples":['
    '{"en":"I ate an apple.","cn":"我吃了一个苹果。"},'
    '{"en":"Apple pie is great.","cn":"苹果派很好吃。"}]}]}'
)


def _stub_completer(*, model, prompt, **kw):
    return LLMCompletion(
        response={"text": _STUB_RESPONSE},
        cost_usd=0.003,
    )


@pytest.fixture
def stage(at_head):
    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cache = CacheStore(engine)
    cfg = StageConfig(
        parser_version="1", prompt_version="v1", model="claude-opus-4", cost_estimate_usd=0.003
    )
    llm = LLMClient(store=cache, completers={"anthropic": _stub_completer})
    return (
        ExamplesStage(engine=engine, artifacts=artifacts, config=cfg, llm=llm),
        engine,
        artifacts,
    )


def test_examples_fingerprint_deterministic(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_E1")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_E1'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="para_fp",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    fp1 = s.expected_fingerprint(word_id=wid)
    fp2 = s.expected_fingerprint(word_id=wid)
    assert fp1 == fp2


def test_examples_run_one_returns_examples(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_E2")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_E2'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="para_fp",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    payload = asyncio.run(s.run_one(word_id=wid))
    assert payload.payload["per_meaning"][0]["examples"][0]["en"] == "I ate an apple."
    assert payload.source == "pipeline:anthropic:claude-opus-4:examples_v1"
    assert payload.cost_usd == 0.003


def test_examples_missing_upstream_raises(stage):
    s, engine, _ = stage
    ingest_words(engine, raw_forms=["ghost"], batch_id="B_E3")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_E3'")
        ).scalar_one()
    with pytest.raises(LookupError, match="paraphrase missing"):
        asyncio.run(s.run_one(word_id=wid))


def test_extract_reference_examples_groups_by_sense():
    """Each tr/tran_entry becomes its own bucket; blng is one null-sense bucket."""
    raw_json = {
        "ec": {
            "word": [
                {
                    "trs": [
                        {"tr": [{"l": {
                            "i": ["跑：快速移动双腿前进。"],
                            "sentence": [
                                {"en": "I <b>run</b> fast.", "zh": "我跑得很快。"},
                            ],
                        }}]},
                        {"tr": [{"l": {
                            "i": ["经营：负责或运作（企业等）。"],
                            "sentence": [
                                {"en": "We run a shop.", "zh": "我们经营一家店。"},
                            ],
                        }}]},
                    ]
                }
            ]
        },
        "collins": {
            "collins_entries": [{
                "entries": {"entry": [
                    {"tran_entry": [
                        {
                            "tran": "When you <b>run</b>, you move quickly. 跑",
                            "exam_sents": {"sent": [
                                {"eng_sent": "He runs every morning.",
                                 "chn_sent": "他每天早上跑步。"},
                                # casefold-dup of the ec sentence — must drop
                                {"eng_sent": "i run fast.",
                                 "chn_sent": "我跑得很快。"},
                            ]},
                        },
                    ]},
                ]}
            }]
        },
        "blng_sents_part": {
            "sentence-pair": [
                {"sentence": "Engines <b>run</b> on fuel.",
                 "sentence-translation": "发动机靠燃料运转。"},
                {"sentence": "", "sentence-translation": "空的"},
                {"sentence-translation": "无英文"},
            ]
        },
    }
    got = _extract_reference_examples(raw_json)

    # Structure: each bucket has sense + examples.
    assert all(set(b.keys()) == {"sense", "examples"} for b in got)
    # Sense-aligned buckets first (ec → collins), blng (sense=None) last.
    senses = [b["sense"] for b in got]
    assert senses[0] == "跑：快速移动双腿前进。"
    assert senses[1] == "经营：负责或运作（企业等）。"
    assert senses[2] == "When you run, you move quickly. 跑"  # <b> stripped
    assert senses[-1] is None  # blng bucket

    all_ens = [e["en"] for b in got for e in b["examples"]]
    assert "I run fast." in all_ens
    assert "We run a shop." in all_ens
    assert "He runs every morning." in all_ens
    assert "Engines run on fuel." in all_ens
    # dedupe: casefold-dup dropped
    assert len([e for e in all_ens if e.casefold() == "i run fast."]) == 1
    # <b> stripped everywhere
    assert all("<b>" not in e for e in all_ens)
    # Examples inside the "跑" ec bucket must NOT include the collins dup
    # (dedupe attributes the sentence to its first-seen bucket — ec).
    collins_bucket = got[2]
    collins_ens = [e["en"] for e in collins_bucket["examples"]]
    assert "He runs every morning." in collins_ens
    assert "i run fast." not in [e.casefold() for e in collins_ens]


def test_extract_reference_examples_empty_and_malformed():
    assert _extract_reference_examples(None) == []
    assert _extract_reference_examples("not a dict") == []
    assert _extract_reference_examples({}) == []
    assert _extract_reference_examples({"ec": "garbage", "collins": 42}) == []


def test_extract_reference_examples_round_robin_coverage():
    """Every sense gets its 1st example before any sense gets a 2nd."""
    # 6 ec senses × 5 sentences each = 30 candidates, budget is 10.
    # Depth-first would give sense 0 all 5 + sense 1 all 5 → 4 senses empty.
    # Round-robin must give senses 0-5 one each, then senses 0-3 a second.
    ec_trs = []
    for si in range(6):
        ec_trs.append({
            "tr": [{"l": {
                "i": [f"sense{si} 释义"],
                "sentence": [
                    {"en": f"sense{si} example {j}.", "zh": f"义项{si} 例 {j}。"}
                    for j in range(5)
                ],
            }}]
        })
    raw_json = {"ec": {"word": [{"trs": ec_trs}]}}

    got = _extract_reference_examples(raw_json)

    # All 6 sense buckets present.
    assert len(got) == 6
    # Each has at least one example (round 0 hit every bucket).
    assert all(len(b["examples"]) >= 1 for b in got)
    # Total caps at 10.
    total = sum(len(b["examples"]) for b in got)
    assert total == 10
    # First 6 examples taken are each bucket's [0]; next 4 are buckets 0-3's [1].
    counts = [len(b["examples"]) for b in got]
    assert counts == [2, 2, 2, 2, 1, 1]


def test_extract_reference_examples_blng_only_fills_tail():
    """When sense-aligned buckets underfill, blng bucket uses leftover budget."""
    raw_json = {
        "ec": {"word": [{"trs": [{"tr": [{"l": {
            "i": ["只有一个义项的词"],
            "sentence": [{"en": "Only sentence.", "zh": "唯一例句。"}],
        }}]}]}]},
        "blng_sents_part": {
            "sentence-pair": [
                {"sentence": f"corpus {i}.", "sentence-translation": f"语料 {i}。"}
                for i in range(20)
            ]
        },
    }
    got = _extract_reference_examples(raw_json)
    assert got[0]["sense"] == "只有一个义项的词"
    assert len(got[0]["examples"]) == 1
    assert got[-1]["sense"] is None
    assert len(got[-1]["examples"]) == 9  # 10 budget - 1 ec


def test_extract_reference_examples_caps_at_ten():
    raw_json = {
        "blng_sents_part": {
            "sentence-pair": [
                {"sentence": f"example {i} here.",
                 "sentence-translation": f"例句 {i}。"}
                for i in range(30)
            ]
        }
    }
    got = _extract_reference_examples(raw_json)
    total = sum(len(b["examples"]) for b in got)
    assert total == 10
