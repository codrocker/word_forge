"""Provider registry tests: implicit openai entry, credential gating,
config parsing, and unknown-transport guard."""

from __future__ import annotations

import pytest

from wordforge.config import ProviderConfig, WordforgeConfig, load_stage_config
from wordforge.llm.registry import build_completers, provider_env_names


@pytest.fixture()
def cfg_with_providers():
    return WordforgeConfig(
        stages={},
        providers={
            "openai": ProviderConfig(
                completer="openai",
                base_url_env="OPENAI_BASE_URL",
                api_key_env="OPENAI_API_KEY",
            ),
            "relay": ProviderConfig(
                completer="openai",
                base_url_env="TEST_RELAY_BASE_URL",
                api_key_env="TEST_RELAY_API_KEY",
            ),
        },
    )


def test_implicit_openai_entry_synthesized():
    cfg = WordforgeConfig(stages={})
    envs = provider_env_names(cfg)
    assert envs == {"openai": ("OPENAI_BASE_URL", "OPENAI_API_KEY")}


def test_config_file_has_provider_block():
    cfg = load_stage_config()
    assert "openai" in cfg.providers
    assert cfg.providers["openai"].completer == "openai"
    assert cfg.providers["openai"].api_key_env == "OPENAI_API_KEY"


def test_missing_creds_skips_entry(cfg_with_providers, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TEST_RELAY_API_KEY", raising=False)
    assert build_completers(cfg_with_providers) == {}


def test_available_entry_built(cfg_with_providers, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TEST_RELAY_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("TEST_RELAY_BASE_URL", "https://relay.example.test/v1")
    completers = build_completers(cfg_with_providers)
    assert sorted(completers) == ["relay"]
    # The completer is the parameterized openai one; calling it would hit
    # the network, so we only assert it was constructed.


def test_unknown_transport_rejected():
    bad = WordforgeConfig(
        stages={},
        providers={"x": ProviderConfig(completer="bedrock", api_key_env="X_KEY")},
    )
    with pytest.raises(ValueError, match="unknown completer transport"):
        build_completers(bad)


def test_provider_block_validation(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(
        '[stages.paraphrase]\nparser_version = "1"\n\n'
        '[providers.oops]\ncompleter = "openai"\napi_key = "literal"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown field"):
        load_stage_config(path=p)

    p.write_text(
        '[stages.paraphrase]\nparser_version = "1"\n\n[providers.oops]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required 'completer'"):
        load_stage_config(path=p)
