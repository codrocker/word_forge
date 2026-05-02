"""P5a Task 0: registry.build_stages assembles Stage instances from config."""

from __future__ import annotations

from wordforge.config import StageConfig, WordforgeConfig
from wordforge.db.engine import make_engine
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.registry import build_stages


def _cfg_with(stage_names: list[str]) -> WordforgeConfig:
    stages = {name: StageConfig(parser_version="1", cost_estimate_usd=0.0) for name in stage_names}
    return WordforgeConfig(stages=stages, default_budget_cap_usd=None)


def test_build_stages_returns_fetch_dict_and_phonetic(at_head):
    engine = make_engine()
    cfg = _cfg_with(["fetch_dict", "phonetic"])
    artifacts = StageArtifactStore(engine)
    stages = build_stages(cfg, engine=engine, artifacts=artifacts)
    names = [s.name for s in stages]
    assert names == ["fetch_dict", "phonetic"]  # spec §5 order


def test_build_stages_preserves_spec_order(at_head):
    engine = make_engine()
    # Give config in a "wrong" order; registry must reorder to spec §5 sequence.
    cfg = _cfg_with(["phonetic", "fetch_dict"])
    artifacts = StageArtifactStore(engine)
    stages = build_stages(cfg, engine=engine, artifacts=artifacts)
    assert [s.name for s in stages] == ["fetch_dict", "phonetic"]


def test_build_stages_ignores_unimplemented_stages_gracefully(at_head):
    # P5a only implements fetch_dict + phonetic. If config lists paraphrase
    # (P5b), registry must skip it without crashing — executor sees a warning
    # via typer, but pipeline still runs what CAN run.
    engine = make_engine()
    cfg = _cfg_with(["fetch_dict", "paraphrase", "phonetic"])
    artifacts = StageArtifactStore(engine)
    stages = build_stages(cfg, engine=engine, artifacts=artifacts)
    names = [s.name for s in stages]
    assert names == ["fetch_dict", "phonetic"]


def test_build_stages_empty_config_returns_empty_list(at_head):
    engine = make_engine()
    cfg = WordforgeConfig(stages={}, default_budget_cap_usd=None)
    artifacts = StageArtifactStore(engine)
    assert build_stages(cfg, engine=engine, artifacts=artifacts) == []
