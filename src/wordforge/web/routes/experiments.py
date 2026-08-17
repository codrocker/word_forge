"""Experiments routes (web M8): provider registry listing, live model
listing, and run create/list/detail for side-by-side comparison."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.engine import Engine

from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok
from wordforge.web.schemas.experiments import ExperimentRunRequest
from wordforge.web.services import experiment_service
from wordforge.web.services.experiment_service import ExperimentError

router = APIRouter(prefix="/api/v1/experiments", dependencies=[Depends(current_editor)])


@router.get("/providers")
def providers():
    from wordforge.config import load_stage_config
    from wordforge.llm.registry import provider_env_names

    cfg = load_stage_config()
    envs = provider_env_names(cfg)
    items = []
    for pid, (base_env, key_env) in envs.items():
        items.append(
            {
                "id": pid,
                "completer": cfg.providers[pid].completer if pid in cfg.providers else "openai",
                "base_url_env": base_env,
                "api_key_env": key_env,
                "available": bool(key_env and os.environ.get(key_env)),
            }
        )
    stages = [
        {
            "stage": name,
            "prompt_version": sc.prompt_version,
            "default_provider": sc.provider,
            "default_model": sc.model,
        }
        for name, sc in cfg.stages.items()
        if name in experiment_service.STAGE_SPECS
    ]
    return envelope_ok({"providers": items, "stages": stages})


@router.get("/providers/{provider_id}/models")
def provider_models(provider_id: str):
    try:
        models = experiment_service.fetch_models(provider_id)
    except ExperimentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return envelope_ok({"provider": provider_id, "models": models})


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    req: ExperimentRunRequest,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    from wordforge.web.services import agent_run_service

    try:
        if req.agent_id is not None:
            run_id = agent_run_service.start_agent_run(
                engine,
                editor_id=editor["id"],
                agent_id=req.agent_id,
                prompt_override=req.prompt_override,
                word_count=req.word_count,
                seed=req.seed,
            )
        else:
            if not req.provider or not req.model or not req.stage:
                raise ExperimentError("provider, model and stage are required without agent_id")
            run_id = experiment_service.start_run(
                engine,
                editor_id=editor["id"],
                provider=req.provider,
                model=req.model,
                stage=req.stage,
                prompt_override=req.prompt_override,
                word_count=req.word_count,
                seed=req.seed,
            )
    except ExperimentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return envelope_ok({"run_id": run_id})


@router.get("/runs")
def runs(limit: int = 50, engine: Engine = Depends(get_engine)):
    return envelope_ok({"items": experiment_service.list_runs(engine, limit=limit)})


@router.get("/runs/{run_id}")
def run_detail(run_id: int, engine: Engine = Depends(get_engine)):
    from wordforge.web.services import agent_run_service

    row = agent_run_service.get_run_detail(engine, run_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"experiment run {run_id} not found",
        )
    return envelope_ok({"run": row})
