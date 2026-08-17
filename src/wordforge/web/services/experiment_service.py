"""LLM experiment runs (web M8) — compare providers/models/prompts on a
word batch.

One run = (provider, model, stage, optional prompt override) applied to a
seed-deterministic sample of words that have fetch_dict upstream data.
Per-word outputs are parsed and schema-validated the same way the real
stage would, so a run answers "is this combo usable", not just "what did
it say". Results + costs land in meta.experiment_runs for side-by-side
comparison.

Adding a stage to experiment on = register a spec in STAGE_SPECS below
(template vars, request params, output validator). Everything else is
stage-agnostic.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from wordforge.stages._llm_base import load_prompt, parse_llm_json
from wordforge.stages.paraphrase import summarize_youdao_json


class ExperimentError(ValueError):
    """User-facing experiment validation error (maps to HTTP 400)."""


def _validate_paraphrase(parsed: Any) -> bool:
    return isinstance(parsed, dict) and isinstance(parsed.get("meanings"), list)


# Stage-specific experiment specs. Values mirror the real stage's prompt
# contract so experiment results are representative of pipeline behavior.
# max_tokens is deliberately generous (8k vs the pipeline's 2k): reasoning-
# tier models (deepseek-v4 etc.) spend the budget on hidden thinking and
# return finish_reason=length with EMPTY content when starved — observed
# live on 2026-08-17 E2E: 2048 tokens failed 3/5 words. The experiment's
# job is to measure output quality, not reproduce starvation.
STAGE_SPECS: dict[str, dict[str, Any]] = {
    "paraphrase": {
        "template_vars": ("word", "dict_summary"),
        "request_params": {"temperature": 0, "max_tokens": 8192},
        "validate": _validate_paraphrase,
    },
}

MAX_WORD_COUNT = 200
MAX_PROMPT_OVERRIDE_CHARS = 20_000


def _new_llm(engine: Engine):
    """Build the LLMClient from the provider registry. Tests monkeypatch me."""
    from wordforge.cache import CacheStore
    from wordforge.config import load_stage_config
    from wordforge.llm.client import LLMClient
    from wordforge.llm.registry import build_completers

    completers = build_completers(load_stage_config())
    return LLMClient(store=CacheStore(engine), completers=completers)


def available_providers() -> dict[str, bool]:
    """{provider_id: creds-present} from the registry env names."""
    from wordforge.config import load_stage_config
    from wordforge.llm.registry import provider_env_names

    cfg = load_stage_config()
    out: dict[str, bool] = {}
    for pid, (_base_env, key_env) in provider_env_names(cfg).items():
        out[pid] = bool(key_env and os.environ.get(key_env))
    return out


def _sample_words(engine: Engine, *, word_count: int, seed: int) -> list[dict[str, Any]]:
    """Deterministic sample: order by sha256(seed:word_id), take first N.

    Hash-ordering (instead of the random module) keeps runs reproducible
    with the same seed — the same 50 words across every combo being
    compared — without any RNG state to manage.
    """
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sa.text(
                    "SELECT w.id, w.normalized_form AS word, a.payload "
                    "FROM pipeline.words w "
                    "JOIN pipeline.stage_artifacts a "
                    "  ON a.word_id = w.id AND a.stage_name = 'fetch_dict'"
                ),
            )
            .mappings()
            .all()
        )
    candidates = [dict(r) for r in rows]
    if not candidates:
        raise ExperimentError("no words with fetch_dict artifacts to sample")
    candidates.sort(
        key=lambda r: hashlib.sha256(f"{seed}:{r['id']}".encode()).digest()
    )
    return candidates[:word_count]


def _render(template: str, *, word: str, dict_summary: str) -> str:
    return template.replace("{word}", word).replace("{dict_summary}", dict_summary)


def start_run(
    engine: Engine,
    *,
    editor_id: int,
    provider: str,
    model: str,
    stage: str,
    prompt_override: str | None,
    word_count: int,
    seed: int,
) -> int:
    if stage not in STAGE_SPECS:
        raise ExperimentError(f"stage {stage!r} not experimentable; valid: {sorted(STAGE_SPECS)}")
    if not 1 <= word_count <= MAX_WORD_COUNT:
        raise ExperimentError(f"word_count must be 1..{MAX_WORD_COUNT}")
    if prompt_override is not None and len(prompt_override) > MAX_PROMPT_OVERRIDE_CHARS:
        raise ExperimentError(f"prompt_override over {MAX_PROMPT_OVERRIDE_CHARS} chars")
    if provider not in available_providers():
        raise ExperimentError(
            f"provider {provider!r} unavailable — set the env pair from "
            "resources/default.toml [providers.*]"
        )

    rows = _sample_words(engine, word_count=word_count, seed=seed)

    with engine.begin() as conn:
        run_id = conn.execute(
            sa.text(
                "INSERT INTO meta.experiment_runs "
                "(editor_id, provider, model, stage, prompt_override, seed, word_ids) "
                "VALUES (:e, :p, :m, :s, :po, :seed, CAST(:wids AS jsonb)) RETURNING id"
            ),
            {
                "e": editor_id,
                "p": provider,
                "m": model,
                "s": stage,
                "po": prompt_override,
                "seed": seed,
                "wids": json.dumps([r["id"] for r in rows]),
            },
        ).scalar_one()

    thread = threading.Thread(
        target=_execute_run,
        args=(engine, run_id, provider, model, stage, prompt_override, rows),
        daemon=True,
        name=f"experiment-run-{run_id}",
    )
    thread.start()
    return run_id


def _execute_run(
    engine: Engine,
    run_id: int,
    provider: str,
    model: str,
    stage: str,
    prompt_override: str | None,
    rows: list[dict[str, Any]],
) -> None:
    """Worker thread body. Unhandled DB failures leave the row in 'running';
    LLM/parse failures are recorded per-word or as status='error'."""
    from wordforge.config import load_stage_config

    try:
        cfg = load_stage_config()
        spec = STAGE_SPECS[stage]
        stage_cfg = cfg.stages.get(stage)
        prompt_version = (stage_cfg.prompt_version if stage_cfg else None) or "v1"
        template = prompt_override if prompt_override else load_prompt(stage, prompt_version)

        llm = _new_llm(engine)
        results: list[dict[str, Any]] = []
        total_cost = 0.0
        ok_count = 0
        valid_count = 0

        for row in rows:
            raw_payload = row["payload"].get("raw_json", {}) if isinstance(row["payload"], dict) else {}
            dict_summary = summarize_youdao_json(raw_payload) or "(no dictionary data)"
            rendered = _render(template, word=row["word"], dict_summary=dict_summary)
            item: dict[str, Any] = {
                "word_id": row["id"],
                "word": row["word"],
                "ok": False,
                "valid": False,
                "cost_usd": 0.0,
                "latency_ms": None,
                "text": None,
                "error": None,
            }
            t0 = time.perf_counter()
            try:
                completion = llm.complete(
                    provider=provider,
                    model=model,
                    rendered_prompt=rendered,
                    request_params=dict(spec["request_params"]),
                    input_payload={"script": "experiment", "stage": stage},
                )
                item["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                item["text"] = completion.response.get("text", "")
                item["cost_usd"] = completion.cost_usd
                total_cost += completion.cost_usd
                parsed = parse_llm_json(item["text"])
                item["ok"] = True
                ok_count += 1
                item["valid"] = bool(spec["validate"](parsed))
                valid_count += 1 if item["valid"] else 0
            except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                item["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                item["error"] = str(exc)[:500]
            results.append(item)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE meta.experiment_runs SET status = 'done', "
                    "results = CAST(:results AS jsonb), total_cost_usd = :cost, "
                    "ok_count = :ok, valid_count = :valid, finished_at = now() "
                    "WHERE id = :id"
                ),
                {
                    "results": json.dumps(results, ensure_ascii=False),
                    "cost": total_cost,
                    "ok": ok_count,
                    "valid": valid_count,
                    "id": run_id,
                },
            )
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE meta.experiment_runs SET status = 'error', "
                    "error = :err, finished_at = now() WHERE id = :id"
                ),
                {"err": str(exc)[:1000], "id": run_id},
            )


def get_run(engine: Engine, run_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT id, editor_id, provider, model, stage, prompt_override, "
                    "seed, word_ids, status, error, results, total_cost_usd, "
                    "ok_count, valid_count, created_at, finished_at "
                    "FROM meta.experiment_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def list_runs(engine: Engine, *, limit: int = 50) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sa.text(
                    "SELECT id, editor_id, provider, model, stage, prompt_override, "
                    "seed, word_ids, status, error, results, total_cost_usd, "
                    "ok_count, valid_count, created_at, finished_at "
                    "FROM meta.experiment_runs ORDER BY created_at DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


def _assert_public_http_url(url: str) -> None:
    """Model-listing fetch guard: http(s) only, no loopback/private/reserved."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ExperimentError(f"base_url scheme must be http/https, got {parsed.scheme!r}")
    host = parsed.hostname or ""
    if host == "localhost":
        raise ExperimentError("base_url host must not be localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a DNS name, fine
    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
        raise ExperimentError("base_url must not point at a private/loopback address")


def fetch_models(provider_id: str) -> list[str]:
    """Live /v1/models listing for an available OpenAI-compatible provider."""
    from wordforge.config import load_stage_config
    from wordforge.llm.registry import provider_env_names

    cfg = load_stage_config()
    envs = provider_env_names(cfg)
    if provider_id not in envs:
        raise ExperimentError(f"unknown provider {provider_id!r}")
    base_env, key_env = envs[provider_id]
    api_key = os.environ.get(key_env, "") if key_env else ""
    if not api_key:
        raise ExperimentError(f"provider {provider_id!r} unavailable — set {key_env}")
    default_base = "https://api.openai.com/v1"
    env_base = os.environ.get(base_env, "") if base_env else ""
    base_url = env_base or default_base
    url = base_url.rstrip("/") + "/models"
    _assert_public_http_url(url)

    try:
        import httpx  # lazy: only the model-listing path needs it
    except ImportError as e:
        raise RuntimeError("httpx not installed; `pip install wordforge[web]`") from e

    resp = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=8.0)
    if resp.status_code != 200:
        raise ExperimentError(f"upstream {resp.status_code} listing models at {url}")
    data = resp.json().get("data", [])
    return sorted(str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id"))
