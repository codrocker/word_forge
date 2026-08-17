"""Build the completers dict from the [providers.*] registry in default.toml.

Design (2026-08 provider migration):
- One registry entry = one named LLM access point (endpoint + key via env
  var names, transport via `completer`). Adding a provider is a TOML block
  plus two env vars — no code.
- Transports map to completer factories. "openai" covers every
  OpenAI-compatible endpoint (DeepSeek / Kimi / GLM / SiliconFlow /
  self-hosted relays); "anthropic" covers Anthropic-compatible ones.
- An entry whose api_key env is unset is skipped, mirroring the legacy
  register_if_env_key() lazy semantics: machines without a given
  provider's creds simply don't get that provider.
- The implicit `openai` entry (OPENAI_API_KEY / OPENAI_BASE_URL) is
  synthesized when config doesn't define one, so the pre-registry global
  env-pair semantics stay intact for existing stages and scripts.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wordforge.config import WordforgeConfig

_IMPLICIT_OPENAI_ENVS = ("OPENAI_BASE_URL", "OPENAI_API_KEY")


def _make_transport_factory(completer: str):
    """Return a (base_url, api_key) -> completer factory for a transport."""
    if completer == "openai":
        from wordforge.llm.openai_completer import make_openai_completer

        return lambda *, base_url, api_key: make_openai_completer(
            api_key=api_key, base_url=base_url
        )
    if completer == "anthropic":
        from wordforge.llm.anthropic_completer import make_anthropic_completer

        return lambda *, base_url, api_key: make_anthropic_completer(api_key=api_key)
    raise ValueError(
        f"unknown completer transport: {completer!r}; valid: 'openai', 'anthropic'"
    )


def build_completers(config: WordforgeConfig) -> dict[str, Any]:
    """Assemble {provider_id: completer} from config providers.

    Raises ValueError on an unknown transport (config typo guard). Entries
    whose api_key env var is unset are silently skipped.
    """
    from wordforge.config import ProviderConfig

    entries: dict[str, ProviderConfig] = dict(config.providers)
    if "openai" not in entries:
        entries["openai"] = ProviderConfig(
            completer="openai",
            base_url_env="OPENAI_BASE_URL",
            api_key_env="OPENAI_API_KEY",
        )

    completers: dict[str, Any] = {}
    for pid, entry in entries.items():
        factory = _make_transport_factory(entry.completer)
        api_key = os.environ.get(entry.api_key_env or "", "") if entry.api_key_env else ""
        if entry.api_key_env and not api_key:
            continue  # creds absent on this machine — provider not available
        completers[pid] = factory(
            base_url=os.environ.get(entry.base_url_env or "", "") or None
            if entry.base_url_env
            else None,
            api_key=api_key or None,
        )
    return completers


def provider_env_names(config: WordforgeConfig) -> dict[str, tuple[str | None, str | None]]:
    """Return {provider_id: (base_url_env, api_key_env)} incl. the implicit entry.

    Used by the web experiments UI to show which env vars each named
    provider expects and whether they are currently set.
    """
    from wordforge.config import ProviderConfig

    entries: dict[str, ProviderConfig] = dict(config.providers)
    if "openai" not in entries:
        entries["openai"] = ProviderConfig(
            completer="openai",
            base_url_env="OPENAI_BASE_URL",
            api_key_env="OPENAI_API_KEY",
        )
    return {pid: (e.base_url_env, e.api_key_env) for pid, e in entries.items()}
