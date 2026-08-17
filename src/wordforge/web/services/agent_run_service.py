"""Experiment runs BY AGENT (web config center recipe).

The agent path resolves a pinned agent version into a concrete recipe
(provider config version + model + prompt version + params), builds a
single-provider completer directly from it, and records a resolved
snapshot on the run row for audit. Separate from experiment_service so
the TOML-registry path and the config-center path evolve independently;
sampling/rendering/stage specs are imported from experiment_service.

The runs' `provider` column stores the literal "agent" — the concrete
identity lives in agent_version_id + resolved_snapshot (audit).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from wordforge.stages._llm_base import parse_llm_json
from wordforge.web.services.config_center_service import (
    ConfigCenterError,
    resolve_agent_version,
)
from wordforge.web.services.experiment_service import (
    MAX_PROMPT_OVERRIDE_CHARS,
    MAX_WORD_COUNT,
    STAGE_SPECS,
    ExperimentError,
    _render,
    _sample_words,
)

AGENT_PROVIDER = "agent"


def _new_llm_from_recipe(engine: Engine, resolved: dict):
    """Single-provider LLMClient built from the resolved recipe.
    Tests monkeypatch me instead of touching real endpoints."""
    from wordforge.cache import CacheStore
    from wordforge.llm.client import LLMClient

    if resolved["transport"] == "anthropic":
        from wordforge.llm.anthropic_completer import make_anthropic_completer

        completer = make_anthropic_completer(api_key=resolved["api_key"])
    else:
        from wordforge.llm.openai_completer import make_openai_completer

        completer = make_openai_completer(
            api_key=resolved["api_key"], base_url=resolved["base_url"]
        )
    return LLMClient(store=CacheStore(engine), completers={AGENT_PROVIDER: completer})


def start_agent_run(
    engine: Engine,
    *,
    editor_id: int,
    agent_id: int,
    prompt_override: str | None,
    word_count: int,
    seed: int,
) -> int:
    with engine.connect() as conn:
        av = (
            conn.execute(
                sa.text(
                    "SELECT av.id AS av_id, av.version, a.name AS agent_name "
                    "FROM meta.agents a "
                    "JOIN meta.agent_versions av ON av.id = a.current_version_id "
                    "WHERE a.id = :a"
                ),
                {"a": agent_id},
            )
            .mappings()
            .first()
        )
    if av is None:
        raise ExperimentError(f"agent {agent_id} not found")
    try:
        resolved = resolve_agent_version(engine, av["av_id"])
    except ConfigCenterError as e:
        raise ExperimentError(str(e)) from e

    stage = resolved["stage"]
    if stage not in STAGE_SPECS:
        raise ExperimentError(f"stage {stage!r} not experimentable; valid: {sorted(STAGE_SPECS)}")
    if not 1 <= word_count <= MAX_WORD_COUNT:
        raise ExperimentError(f"word_count must be 1..{MAX_WORD_COUNT}")
    if prompt_override is not None and len(prompt_override) > MAX_PROMPT_OVERRIDE_CHARS:
        raise ExperimentError(f"prompt_override over {MAX_PROMPT_OVERRIDE_CHARS} chars")

    rows = _sample_words(engine, word_count=word_count, seed=seed)

    prompt_digest = hashlib.sha256(resolved["prompt_content"].encode()).hexdigest()[:16]
    snapshot = {
        "agent": {"id": agent_id, "name": av["agent_name"], "version": av["version"]},
        "provider_config": {
            "id": resolved["provider_config_id"],
            "name": resolved["provider_config_name"],
            "version": resolved["provider_config_version"],
            "base_url": resolved["base_url"],
            "transport": resolved["transport"],
        },
        "model": resolved["model"],
        "prompt": {
            "id": resolved["prompt_id"],
            "name": resolved["prompt_name"],
            "version": resolved["prompt_version"],
            "sha256": prompt_digest,
        },
        "params": resolved["params"],
    }

    word_ids_json = json.dumps([r["id"] for r in rows])
    snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    model_value = resolved["model"]
    stage_value = stage
    av_value = av["av_id"]
    with engine.begin() as conn:
        run_id = conn.execute(
            sa.text(
                "INSERT INTO meta.experiment_runs "
                "(editor_id, provider, model, stage, prompt_override, seed, "
                "word_ids, agent_version_id, resolved_snapshot) "
                "VALUES (:e, :p, :m, :s, :po, :seed, CAST(:wids AS jsonb), :av, "
                "CAST(:snap AS jsonb)) RETURNING id"
            ),
            {
                "e": editor_id,
                "p": AGENT_PROVIDER,
                "m": model_value,
                "s": stage_value,
                "po": prompt_override,
                "seed": seed,
                "wids": word_ids_json,
                "av": av_value,
                "snap": snapshot_json,
            },
        ).scalar_one()

    threading.Thread(
        target=_execute_agent_run,
        args=(engine, run_id, resolved, prompt_override, rows),
        daemon=True,
        name=f"experiment-agent-run-{run_id}",
    ).start()
    return run_id


def _execute_agent_run(
    engine: Engine,
    run_id: int,
    resolved: dict,
    prompt_override: str | None,
    rows: list[dict[str, Any]],
) -> None:
    """Worker thread body; mirrors experiment_service._execute_run with
    recipe-sourced template and merged params."""
    stage = resolved["stage"]
    spec = STAGE_SPECS[stage]
    template = prompt_override if prompt_override else resolved["prompt_content"]
    agent_params = resolved["params"] if isinstance(resolved["params"], dict) else {}
    request_params = {**spec["request_params"], **agent_params}
    model_value = resolved["model"]

    try:
        from wordforge.stages.paraphrase import summarize_youdao_json

        llm = _new_llm_from_recipe(engine, resolved)
        results: list[dict[str, Any]] = []
        total_cost = 0.0
        ok_count = 0
        valid_count = 0

        for row in rows:
            payload = row["payload"]
            raw_json = payload.get("raw_json", {}) if isinstance(payload, dict) else {}
            dict_summary = summarize_youdao_json(raw_json) or "(no dictionary data)"
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
                    provider=AGENT_PROVIDER,
                    model=model_value,
                    rendered_prompt=rendered,
                    request_params=dict(request_params),
                    input_payload={"script": "experiment-agent", "stage": stage},
                )
                item["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                item["text"] = completion.response.get("text", "")
                item["cost_usd"] = completion.cost_usd
                total_cost += completion.cost_usd
                parsed = parse_llm_json(item["text"])
                item["ok"] = True
                ok_count += 1
                item["valid"] = bool(spec["validate"](parsed))
                if item["valid"]:
                    valid_count += 1
            except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                item["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                item["error"] = str(exc)[:500]
            results.append(item)

        results_json = json.dumps(results, ensure_ascii=False)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE meta.experiment_runs SET status = 'done', "
                    "results = CAST(:results AS jsonb), total_cost_usd = :cost, "
                    "ok_count = :ok, valid_count = :valid, finished_at = now() "
                    "WHERE id = :id"
                ),
                {
                    "results": results_json,
                    "cost": total_cost,
                    "ok": ok_count,
                    "valid": valid_count,
                    "id": run_id,
                },
            )
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        err_value = str(exc)[:1000]
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE meta.experiment_runs SET status = 'error', "
                    "error = :err, finished_at = now() WHERE id = :id"
                ),
                {"err": err_value, "id": run_id},
            )


def get_run_detail(engine: Engine, run_id: int) -> dict[str, Any] | None:
    """Run detail including agent_version_id + resolved_snapshot (the
    registry-path reader omits those columns)."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT id, editor_id, provider, model, stage, prompt_override, "
                    "seed, word_ids, status, error, results, total_cost_usd, "
                    "ok_count, valid_count, agent_version_id, resolved_snapshot, "
                    "created_at, finished_at "
                    "FROM meta.experiment_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
