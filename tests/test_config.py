"""P4 Task 0+1: wordforge.config toml loader + in-package default.toml."""

from __future__ import annotations

import tomllib
from importlib import resources

import pytest

from wordforge.config import (
    StageConfig,  # noqa: F401
    WordforgeConfig,  # noqa: F401
    load_stage_config,
)


def test_default_toml_ships_inside_package():
    ref = resources.files("wordforge.resources") / "default.toml"
    assert ref.is_file(), "wordforge/resources/default.toml missing in the installed package"


def test_default_toml_parses():
    ref = resources.files("wordforge.resources") / "default.toml"
    data = tomllib.loads(ref.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "stages" in data, "top-level 'stages' key required"


def test_load_stage_config_returns_all_stages():
    cfg = load_stage_config()
    assert set(cfg.stages.keys()) == {
        "fetch_dict",
        "paraphrase",
        # paraphrase_rerank: not a real stage (registry ignores it), but
        # load_stage_config returns every [stages.X] section because the
        # file doesn't distinguish main-pipeline stages from sub-call
        # configs. See _SPEC_STAGE_ORDER in stages/registry.py.
        "paraphrase_rerank",
        "phonetic",
        "derivatives",
        "examples",
        "mnemonic",
        "quality_gate",
        "export",
    }


def test_stage_config_has_required_fields():
    cfg = load_stage_config()
    paraphrase = cfg.stages["paraphrase"]
    assert paraphrase.parser_version == "1"
    assert paraphrase.prompt_version == "v1"
    # Lock the shape, not the exact model — default.toml upgrades the shipped
    # model over time (claude/bedrock until 2026-08, openai-compatible after
    # the provider migration). Test should verify it's a usable model id,
    # not an empty string.
    assert paraphrase.model is not None
    assert len(paraphrase.model) > 0
    assert paraphrase.provider == "openai"
    assert paraphrase.cost_estimate_usd > 0


def test_fetch_dict_no_prompt_fields_OK():
    cfg = load_stage_config()
    fd = cfg.stages["fetch_dict"]
    assert fd.prompt_version is None
    assert fd.model is None
    assert fd.cost_estimate_usd == 0.0


def test_default_budget_cap_is_null_in_shipped_config():
    cfg = load_stage_config()
    assert cfg.default_budget_cap_usd is None


def test_load_stage_config_accepts_explicit_path(tmp_path):
    p = tmp_path / "custom.toml"
    # IMPORTANT: default_budget_cap_usd MUST come BEFORE the [stages.X] header
    # or TOML scoping will place it inside that table (D11 battle).
    p.write_text(
        """default_budget_cap_usd = 10.0

[stages.fetch_dict]
parser_version = "99"
cost_estimate_usd = 0.0
""",
        encoding="utf-8",
    )
    cfg = load_stage_config(path=p)
    assert cfg.stages["fetch_dict"].parser_version == "99"
    assert cfg.default_budget_cap_usd == pytest.approx(10.0)


def test_missing_stages_key_raises(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("some_other_key = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'stages'"):
        load_stage_config(path=p)


def test_unknown_field_rejected(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(
        """
[stages.fetch_dict]
parser_version = "1"
cost_estimate_usd = 0.0
typo_field = "oops"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown"):
        load_stage_config(path=p)


def test_missing_parser_version_rejected(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(
        """
[stages.fetch_dict]
cost_estimate_usd = 0.0
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parser_version"):
        load_stage_config(path=p)
