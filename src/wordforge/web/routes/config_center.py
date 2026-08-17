"""Config center routes (M9): versioned provider configs, prompts, agents.

Keys are write-only: create/update accept api_key, every response shape
comes from config_center_service which never returns key material (only
has_key + last4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.engine import Engine

from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok
from wordforge.web.schemas.config_center import (
    AgentCreate,
    AgentUpdate,
    PromptCreate,
    PromptUpdate,
    ProviderConfigCreate,
    ProviderConfigUpdate,
    RollbackRequest,
)
from wordforge.web.secrets_box import SecretBoxError
from wordforge.web.services import config_center_service as svc

router = APIRouter(prefix="/api/v1/config-center", dependencies=[Depends(current_editor)])


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/providers")
def list_providers(engine: Engine = Depends(get_engine)):
    return envelope_ok({"items": svc.list_providers(engine)})


@router.post("/providers", status_code=status.HTTP_201_CREATED)
def create_provider(
    req: ProviderConfigCreate,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.create_provider(
            engine,
            name=req.name,
            transport=req.transport,
            base_url=req.base_url,
            api_key=req.api_key,
            notes=req.notes,
            editor_id=editor["id"],
        )
    except (svc.ConfigCenterError, SecretBoxError) as e:
        raise _bad_request(e) from e
    return envelope_ok({"provider": row})


@router.get("/providers/{provider_id}")
def get_provider(provider_id: int, engine: Engine = Depends(get_engine)):
    try:
        return envelope_ok({"provider": svc.get_provider(engine, provider_id)})
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e


@router.patch("/providers/{provider_id}")
def update_provider(
    provider_id: int,
    req: ProviderConfigUpdate,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.update_provider(
            engine,
            provider_id,
            name=req.name,
            transport=req.transport,
            base_url=req.base_url,
            notes=req.notes,
            api_key=req.api_key,
            editor_id=editor["id"],
        )
    except (svc.ConfigCenterError, SecretBoxError) as e:
        raise _bad_request(e) from e
    return envelope_ok({"provider": row})


@router.post("/providers/{provider_id}/rollback")
def rollback_provider(
    provider_id: int,
    req: RollbackRequest,
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.rollback_provider(engine, provider_id, req.version)
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"provider": row})


@router.get("/providers/{provider_id}/models")
def provider_models(provider_id: int, engine: Engine = Depends(get_engine)):
    try:
        models = svc.fetch_provider_models(engine, provider_id)
    except (svc.ConfigCenterError, SecretBoxError) as e:
        raise _bad_request(e) from e
    return envelope_ok({"models": models})


@router.get("/prompts")
def list_prompts(engine: Engine = Depends(get_engine)):
    return envelope_ok({"items": svc.list_prompts(engine)})


@router.post("/prompts", status_code=status.HTTP_201_CREATED)
def create_prompt(
    req: PromptCreate,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.create_prompt(
            engine,
            name=req.name,
            stage=req.stage,
            content=req.content,
            description=req.description,
            notes=req.notes,
            editor_id=editor["id"],
        )
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"prompt": row})


@router.get("/prompts/{prompt_id}")
def get_prompt(prompt_id: int, engine: Engine = Depends(get_engine)):
    try:
        return envelope_ok({"prompt": svc.get_prompt(engine, prompt_id)})
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e


@router.patch("/prompts/{prompt_id}")
def update_prompt(
    prompt_id: int,
    req: PromptUpdate,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.update_prompt(
            engine,
            prompt_id,
            content=req.content,
            description=req.description,
            notes=req.notes,
            editor_id=editor["id"],
        )
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"prompt": row})


@router.post("/prompts/{prompt_id}/rollback")
def rollback_prompt(prompt_id: int, req: RollbackRequest, engine: Engine = Depends(get_engine)):
    try:
        row = svc.rollback_prompt(engine, prompt_id, req.version)
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"prompt": row})


@router.get("/agents")
def list_agents(engine: Engine = Depends(get_engine)):
    return envelope_ok({"items": svc.list_agents(engine)})


@router.post("/agents", status_code=status.HTTP_201_CREATED)
def create_agent(
    req: AgentCreate,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.create_agent(
            engine,
            name=req.name,
            description=req.description,
            provider_config_id=req.provider_config_id,
            provider_config_version=req.provider_config_version,
            model=req.model,
            prompt_id=req.prompt_id,
            prompt_version=req.prompt_version,
            params=req.params,
            notes=req.notes,
            editor_id=editor["id"],
        )
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"agent": row})


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int, engine: Engine = Depends(get_engine)):
    try:
        return envelope_ok({"agent": svc.get_agent(engine, agent_id)})
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: int,
    req: AgentUpdate,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    try:
        row = svc.update_agent(
            engine,
            agent_id,
            description=req.description,
            provider_config_id=req.provider_config_id,
            provider_config_version=req.provider_config_version,
            model=req.model,
            prompt_id=req.prompt_id,
            prompt_version=req.prompt_version,
            params=req.params,
            notes=req.notes,
            editor_id=editor["id"],
        )
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"agent": row})


@router.post("/agents/{agent_id}/rollback")
def rollback_agent(agent_id: int, req: RollbackRequest, engine: Engine = Depends(get_engine)):
    try:
        row = svc.rollback_agent(engine, agent_id, req.version)
    except svc.ConfigCenterError as e:
        raise _bad_request(e) from e
    return envelope_ok({"agent": row})
