"""Loader for wordforge stage pipeline config (resources/default.toml).

Frozen dataclasses + hand validation; no pydantic (1 user, 8 stages — a
closed schema, no runtime validation magic needed).

Spec §6 — `parser_version` lives here, is fingerprint input. Bumping it
invalidates that stage's fingerprint and forces rerun (cache still hits).

Round 1 D1 battle: config file lives INSIDE the package at
src/wordforge/resources/default.toml. hatchling's `packages = ["src/wordforge"]`
ships it inside the wheel, so `uv tool install wordforge` works just as well
as a git checkout. We resolve it with `importlib.resources` — stdlib API for
package data — so the code path is identical dev vs installed.

Round 1 D5 battle: toml (stdlib tomllib) instead of a C-extension yaml
parser since the schema is closed + static.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

_VALID_STAGE_FIELDS = frozenset(
    {"parser_version", "prompt_version", "model", "cost_estimate_usd", "provider"}
)


@dataclass(frozen=True)
class StageConfig:
    parser_version: str
    cost_estimate_usd: float = 0.0
    prompt_version: str | None = None
    model: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class WordforgeConfig:
    stages: dict[str, StageConfig] = field(default_factory=dict)
    default_budget_cap_usd: float | None = None


def _load_default_bytes() -> bytes:
    ref = resources.files("wordforge.resources") / "default.toml"
    return ref.read_bytes()


def load_stage_config(*, path: Path | None = None) -> WordforgeConfig:
    """Parse toml → WordforgeConfig.

    Args:
        path: optional override; reads the packaged default.toml when omitted.

    Raises:
        ValueError: missing required fields, unknown fields, or wrong types.
        FileNotFoundError: explicit path did not exist.
    """
    if path is not None:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    else:
        raw = tomllib.loads(_load_default_bytes().decode("utf-8"))

    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping, got {type(raw).__name__}")
    if "stages" not in raw:
        raise ValueError("config missing required 'stages' key")

    stages_raw = raw["stages"]
    if not isinstance(stages_raw, dict):
        raise ValueError("'stages' must be a table")

    stages: dict[str, StageConfig] = {}
    for name, sub in stages_raw.items():
        if not isinstance(sub, dict):
            raise ValueError(f"stage {name!r} must be a table, got {type(sub).__name__}")
        extra = set(sub.keys()) - _VALID_STAGE_FIELDS
        if extra:
            raise ValueError(
                f"stage {name!r} has unknown field(s): {sorted(extra)}; "
                f"valid: {sorted(_VALID_STAGE_FIELDS)}"
            )
        if "parser_version" not in sub:
            raise ValueError(f"stage {name!r} missing required 'parser_version'")
        stages[name] = StageConfig(
            parser_version=str(sub["parser_version"]),
            cost_estimate_usd=float(sub.get("cost_estimate_usd", 0.0)),
            prompt_version=(str(sub["prompt_version"]) if "prompt_version" in sub else None),
            model=(str(sub["model"]) if "model" in sub else None),
            provider=(str(sub["provider"]) if "provider" in sub else None),
        )

    cap_raw = raw.get("default_budget_cap_usd")
    cap = float(cap_raw) if cap_raw is not None else None
    return WordforgeConfig(stages=stages, default_budget_cap_usd=cap)
