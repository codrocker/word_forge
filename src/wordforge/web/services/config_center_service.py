"""Web config center service (M9): versioned provider configs, prompts,
agents — the operator-facing layer above the env-based TOML registry.

Versioning model (uniform across the three entities):
- every edit appends an immutable *_versions row and moves the parent's
  current_version_id pointer forward;
- rollback moves the pointer to an existing version (history stays
  append-only, nothing is rewritten);
- provider API keys live ONLY on the parent row (Fernet-encrypted via
  web.secrets_box); version rows carry non-secret fields exclusively;
- agents pin exact component versions, so an agent version is a fully
  reproducible recipe (provider config version + model + prompt version
  + params).
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from wordforge.web import secrets_box
from wordforge.web.services.experiment_service import (
    ExperimentError,
)

_VALID_TRANSPORTS = ("openai", "anthropic")


class ConfigCenterError(ValueError):
    """User-facing config-center validation error (maps to HTTP 400)."""


def _assert_resolvable_public_url(url: str) -> None:
    """SSRF guard for operator-supplied base URLs: http(s) only, and the
    hostname must RESOLVE (every address) to a public IP — checking only
    the literal host lets a DNS name that resolves into the private
    range bypass the guard. Redirects are not followed at request time."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigCenterError(f"base_url scheme must be http/https, got {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise ConfigCenterError("base_url has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ConfigCenterError(f"cannot resolve base_url host {host!r}") from e
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_reserved
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise ConfigCenterError(
                f"base_url host {host!r} resolves to a non-public address"
            )


def _one(engine: Engine, sql: str, params: dict) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(sa.text(sql), params).mappings().first()
        return dict(row) if row else None


def _rows(engine: Engine, sql: str, params: dict | None = None) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(sa.text(sql), params or {}).mappings().all()]


# ─────────────────────────── provider configs ───────────────────────────


def create_provider(
    engine: Engine,
    *,
    name: str,
    transport: str,
    base_url: str,
    api_key: str,
    notes: str | None,
    editor_id: int,
) -> dict:
    if transport not in _VALID_TRANSPORTS:
        raise ConfigCenterError(f"transport must be one of {list(_VALID_TRANSPORTS)}")
    _assert_resolvable_public_url(base_url)
    if _one(engine, "SELECT id FROM meta.provider_configs WHERE name = :n", {"n": name}):
        raise ConfigCenterError(f"provider config name {name!r} already exists")
    if not api_key:
        raise ConfigCenterError("api_key is required when creating a provider config")

    encrypted = secrets_box.encrypt_key(api_key)
    with engine.begin() as conn:
        cid = conn.execute(
            sa.text(
                "INSERT INTO meta.provider_configs (name, api_key_encrypted, "
                "api_key_last4, created_by) VALUES (:n, :k, :l4, :e) RETURNING id"
            ),
            {"n": name, "k": encrypted, "l4": api_key[-4:], "e": editor_id},
        ).scalar_one()
        vid = conn.execute(
            sa.text(
                "INSERT INTO meta.provider_config_versions "
                "(config_id, version, name, transport, base_url, notes, created_by) "
                "VALUES (:c, 1, :n, :t, :b, :no, :e) RETURNING id"
            ),
            {"c": cid, "n": name, "t": transport, "b": base_url, "no": notes, "e": editor_id},
        ).scalar_one()
        conn.execute(
            sa.text("UPDATE meta.provider_configs SET current_version_id = :v, "
                    "updated_at = now() WHERE id = :c"),
            {"v": vid, "c": cid},
        )
    return get_provider(engine, cid)


def update_provider(
    engine: Engine,
    config_id: int,
    *,
    name: str | None,
    transport: str | None,
    base_url: str | None,
    notes: str | None,
    api_key: str | None,
    editor_id: int,
) -> dict:
    current = _require_provider(engine, config_id)
    if transport is not None and transport not in _VALID_TRANSPORTS:
        raise ConfigCenterError(f"transport must be one of {list(_VALID_TRANSPORTS)}")
    new_url = base_url if base_url is not None else current["base_url"]
    _assert_resolvable_public_url(new_url)
    new_name = name if name is not None else current["name"]
    new_transport = transport if transport is not None else current["transport"]
    new_notes = notes if notes is not None else current["notes"]

    with engine.begin() as conn:
        next_v = conn.execute(
            sa.text("SELECT COALESCE(MAX(version), 0) + 1 FROM "
                    "meta.provider_config_versions WHERE config_id = :c"),
            {"c": config_id},
        ).scalar_one()
        vid = conn.execute(
            sa.text(
                "INSERT INTO meta.provider_config_versions "
                "(config_id, version, name, transport, base_url, notes, created_by) "
                "VALUES (:c, :v, :n, :t, :b, :no, :e) RETURNING id"
            ),
            {"c": config_id, "v": next_v, "n": new_name, "t": new_transport,
             "b": new_url, "no": new_notes, "e": editor_id},
        ).scalar_one()
        # Key (if rotated) updates the parent only — never a version row.
        if api_key:
            encrypted = secrets_box.encrypt_key(api_key)
            conn.execute(
                sa.text(
                    "UPDATE meta.provider_configs SET api_key_encrypted = :k, "
                    "api_key_last4 = :l4, current_version_id = :v, updated_at = now() "
                    "WHERE id = :c"
                ),
                {"k": encrypted, "l4": api_key[-4:], "v": vid, "c": config_id},
            )
        else:
            conn.execute(
                sa.text("UPDATE meta.provider_configs SET current_version_id = :v, "
                        "updated_at = now() WHERE id = :c"),
                {"v": vid, "c": config_id},
            )
    return get_provider(engine, config_id)


def rollback_provider(engine: Engine, config_id: int, version: int) -> dict:
    row = _one(
        engine,
        "SELECT id FROM meta.provider_config_versions "
        "WHERE config_id = :c AND version = :v",
        {"c": config_id, "v": version},
    )
    if row is None:
        raise ConfigCenterError(f"provider config {config_id} has no version {version}")
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE meta.provider_configs SET current_version_id = :v, "
                    "updated_at = now() WHERE id = :c"),
            {"v": row["id"], "c": config_id},
        )
    return get_provider(engine, config_id)


def _require_provider(engine: Engine, config_id: int) -> dict:
    row = _one(
        engine,
        "SELECT pc.id, pcv.name, pc.api_key_encrypted IS NOT NULL AS has_key, "
        "pcv.transport, pcv.base_url, pcv.notes, pcv.version, "
        "pc.current_version_id, pc.created_at, pc.updated_at "
        "FROM meta.provider_configs pc "
        "JOIN meta.provider_config_versions pcv ON pcv.id = pc.current_version_id "
        "WHERE pc.id = :c",
        {"c": config_id},
    )
    if row is None:
        raise ConfigCenterError(f"provider config {config_id} not found")
    return row


def get_provider(engine: Engine, config_id: int) -> dict:
    current = _require_provider(engine, config_id)
    last4 = _one(
        engine, "SELECT api_key_last4 FROM meta.provider_configs WHERE id = :c",
        {"c": config_id},
    )["api_key_last4"]
    versions = _rows(
        engine,
        "SELECT version, name, transport, base_url, notes, created_at "
        "FROM meta.provider_config_versions WHERE config_id = :c ORDER BY version DESC",
        {"c": config_id},
    )
    return {
        "id": current["id"],
        "name": current["name"],
        "transport": current["transport"],
        "base_url": current["base_url"],
        "notes": current["notes"],
        "current_version": current["version"],
        "has_key": current["has_key"],
        "api_key_last4": last4,
        "versions": versions,
        "created_at": current["created_at"],
        "updated_at": current["updated_at"],
    }


def list_providers(engine: Engine) -> list[dict]:
    ids = _rows(engine, "SELECT id FROM meta.provider_configs ORDER BY id")
    return [get_provider(engine, r["id"]) for r in ids]


def _decrypt_provider_key(engine: Engine, config_id: int) -> tuple[dict, str]:
    current = _require_provider(engine, config_id)
    row = _one(
        engine,
        "SELECT api_key_encrypted FROM meta.provider_configs WHERE id = :c",
        {"c": config_id},
    )
    if not row or not row["api_key_encrypted"]:
        raise ConfigCenterError(
            f"provider config {current['name']!r} has no API key stored"
        )
    return current, secrets_box.decrypt_key(row["api_key_encrypted"])


def stored_secret(engine: Engine, config_id: int) -> str | None:
    """Diagnostic/test hook: the raw encrypted column value (never a
    plaintext key). Used to assert encryption-at-rest from tests."""
    row = _one(
        engine,
        "SELECT api_key_encrypted FROM meta.provider_configs WHERE id = :c",
        {"c": config_id},
    )
    return row["api_key_encrypted"] if row else None


def fetch_provider_models(engine: Engine, config_id: int) -> list[str]:
    """Live /v1/models listing using the stored (encrypted) key."""
    current, api_key = _decrypt_provider_key(engine, config_id)
    if current["transport"] != "openai":
        raise ConfigCenterError("model listing only supports openai-compatible transport")
    url = current["base_url"].rstrip("/") + "/models"
    _assert_resolvable_public_url(url)
    try:
        import httpx
    except ImportError as e:
        raise RuntimeError("httpx not installed; `pip install wordforge[web]`") from e
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=8.0,
        follow_redirects=False,
    )
    if resp.status_code != 200:
        raise ConfigCenterError(
            f"upstream {resp.status_code} listing models at {url}"
        )
    data = resp.json().get("data", [])
    return sorted(str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id"))


# ─────────────────────────────── prompts ────────────────────────────────


def create_prompt(
    engine: Engine,
    *,
    name: str,
    stage: str,
    content: str,
    description: str | None,
    notes: str | None,
    editor_id: int,
) -> dict:
    if _one(engine, "SELECT id FROM meta.prompts WHERE name = :n", {"n": name}):
        raise ConfigCenterError(f"prompt name {name!r} already exists")
    with engine.begin() as conn:
        pid = conn.execute(
            sa.text("INSERT INTO meta.prompts (name, stage, description, created_by) "
                    "VALUES (:n, :s, :d, :e) RETURNING id"),
            {"n": name, "s": stage, "d": description, "e": editor_id},
        ).scalar_one()
        vid = conn.execute(
            sa.text("INSERT INTO meta.prompt_versions (prompt_id, version, content, "
                    "notes, created_by) VALUES (:p, 1, :c, :no, :e) RETURNING id"),
            {"p": pid, "c": content, "no": notes, "e": editor_id},
        ).scalar_one()
        conn.execute(
            sa.text("UPDATE meta.prompts SET current_version_id = :v WHERE id = :p"),
            {"v": vid, "p": pid},
        )
    return get_prompt(engine, pid)


def update_prompt(
    engine: Engine,
    prompt_id: int,
    *,
    content: str,
    description: str | None,
    notes: str | None,
    editor_id: int,
) -> dict:
    if not _one(engine, "SELECT id FROM meta.prompts WHERE id = :p", {"p": prompt_id}):
        raise ConfigCenterError(f"prompt {prompt_id} not found")
    with engine.begin() as conn:
        next_v = conn.execute(
            sa.text("SELECT COALESCE(MAX(version), 0) + 1 FROM meta.prompt_versions "
                    "WHERE prompt_id = :p"),
            {"p": prompt_id},
        ).scalar_one()
        vid = conn.execute(
            sa.text("INSERT INTO meta.prompt_versions (prompt_id, version, content, "
                    "notes, created_by) VALUES (:p, :v, :c, :no, :e) RETURNING id"),
            {"p": prompt_id, "v": next_v, "c": content, "no": notes, "e": editor_id},
        ).scalar_one()
        conn.execute(
            sa.text("UPDATE meta.prompts SET current_version_id = :v, "
                    "description = COALESCE(:d, description), updated_at = now() "
                    "WHERE id = :p"),
            {"v": vid, "p": prompt_id, "d": description},
        )
    return get_prompt(engine, prompt_id)


def rollback_prompt(engine: Engine, prompt_id: int, version: int) -> dict:
    row = _one(
        engine,
        "SELECT id FROM meta.prompt_versions WHERE prompt_id = :p AND version = :v",
        {"p": prompt_id, "v": version},
    )
    if row is None:
        raise ConfigCenterError(f"prompt {prompt_id} has no version {version}")
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE meta.prompts SET current_version_id = :v, updated_at = now() "
                    "WHERE id = :p"),
            {"v": row["id"], "p": prompt_id},
        )
    return get_prompt(engine, prompt_id)


def get_prompt(engine: Engine, prompt_id: int) -> dict:
    row = _one(
        engine,
        "SELECT p.id, p.name, p.stage, p.description, pv.version AS current_version, "
        "p.created_at, p.updated_at FROM meta.prompts p "
        "JOIN meta.prompt_versions pv ON pv.id = p.current_version_id "
        "WHERE p.id = :p",
        {"p": prompt_id},
    )
    if row is None:
        raise ConfigCenterError(f"prompt {prompt_id} not found")
    versions = _rows(
        engine,
        "SELECT version, content, notes, created_at FROM meta.prompt_versions "
        "WHERE prompt_id = :p ORDER BY version DESC",
        {"p": prompt_id},
    )
    row["versions"] = versions
    return row


def list_prompts(engine: Engine) -> list[dict]:
    ids = _rows(
        engine,
        "SELECT p.id FROM meta.prompts p "
        "JOIN meta.prompt_versions pv ON pv.id = p.current_version_id ORDER BY p.id",
    )
    return [get_prompt(engine, r["id"]) for r in ids]


# ─────────────────────────────── agents ─────────────────────────────────


def create_agent(
    engine: Engine,
    *,
    name: str,
    description: str | None,
    provider_config_id: int,
    provider_config_version: int | None,
    model: str,
    prompt_id: int,
    prompt_version: int | None,
    params: dict[str, Any] | None,
    notes: str | None,
    editor_id: int,
) -> dict:
    if _one(engine, "SELECT id FROM meta.agents WHERE name = :n", {"n": name}):
        raise ConfigCenterError(f"agent name {name!r} already exists")
    pc_ver = provider_config_version or _require_provider(engine, provider_config_id)["version"]
    if not _one(
        engine,
        "SELECT id FROM meta.provider_config_versions "
        "WHERE config_id = :c AND version = :v",
        {"c": provider_config_id, "v": pc_ver},
    ):
        raise ConfigCenterError(
            f"provider config {provider_config_id} has no version {pc_ver}"
        )
    pr = _one(engine, "SELECT pv.version AS current_version FROM meta.prompts p JOIN "
                      "meta.prompt_versions pv ON pv.id = p.current_version_id "
                      "WHERE p.id = :p", {"p": prompt_id})
    if pr is None:
        raise ConfigCenterError(f"prompt {prompt_id} not found")
    p_ver = prompt_version or pr["current_version"]

    with engine.begin() as conn:
        aid = conn.execute(
            sa.text("INSERT INTO meta.agents (name, description, created_by) "
                    "VALUES (:n, :d, :e) RETURNING id"),
            {"n": name, "d": description, "e": editor_id},
        ).scalar_one()
        vid = conn.execute(
            sa.text(
                "INSERT INTO meta.agent_versions (agent_id, version, "
                "provider_config_id, provider_config_version, model, prompt_id, "
                "prompt_version, params, notes, created_by) "
                "VALUES (:a, 1, :pc, :pcv, :m, :p, :pv, CAST(:pa AS jsonb), :no, :e) "
                "RETURNING id"
            ),
            {"a": aid, "pc": provider_config_id, "pcv": pc_ver, "m": model,
             "p": prompt_id, "pv": p_ver,
             "pa": json.dumps(params or {}), "no": notes, "e": editor_id},
        ).scalar_one()
        conn.execute(
            sa.text("UPDATE meta.agents SET current_version_id = :v WHERE id = :a"),
            {"v": vid, "a": aid},
        )
    return get_agent(engine, aid)


def update_agent(
    engine: Engine,
    agent_id: int,
    *,
    description: str | None,
    provider_config_id: int,
    provider_config_version: int | None,
    model: str,
    prompt_id: int,
    prompt_version: int | None,
    params: dict[str, Any] | None,
    notes: str | None,
    editor_id: int,
) -> dict:
    if not _one(engine, "SELECT id FROM meta.agents WHERE id = :a", {"a": agent_id}):
        raise ConfigCenterError(f"agent {agent_id} not found")
    pc_ver = provider_config_version or _require_provider(engine, provider_config_id)["version"]
    pr = _one(engine, "SELECT pv.version AS current_version FROM meta.prompts p JOIN "
                      "meta.prompt_versions pv ON pv.id = p.current_version_id "
                      "WHERE p.id = :p", {"p": prompt_id})
    if pr is None:
        raise ConfigCenterError(f"prompt {prompt_id} not found")
    p_ver = prompt_version or pr["current_version"]

    with engine.begin() as conn:
        next_v = conn.execute(
            sa.text("SELECT COALESCE(MAX(version), 0) + 1 FROM meta.agent_versions "
                    "WHERE agent_id = :a"),
            {"a": agent_id},
        ).scalar_one()
        vid = conn.execute(
            sa.text(
                "INSERT INTO meta.agent_versions (agent_id, version, "
                "provider_config_id, provider_config_version, model, prompt_id, "
                "prompt_version, params, notes, created_by) "
                "VALUES (:a, :v, :pc, :pcv, :m, :p, :pv, CAST(:pa AS jsonb), :no, :e) "
                "RETURNING id"
            ),
            {"a": agent_id, "v": next_v, "pc": provider_config_id, "pcv": pc_ver,
             "m": model, "p": prompt_id, "pv": p_ver,
             "pa": json.dumps(params or {}), "no": notes, "e": editor_id},
        ).scalar_one()
        conn.execute(
            sa.text("UPDATE meta.agents SET current_version_id = :v, "
                    "description = COALESCE(:d, description), updated_at = now() "
                    "WHERE id = :a"),
            {"v": vid, "a": agent_id, "d": description},
        )
    return get_agent(engine, agent_id)


def rollback_agent(engine: Engine, agent_id: int, version: int) -> dict:
    row = _one(
        engine,
        "SELECT id FROM meta.agent_versions WHERE agent_id = :a AND version = :v",
        {"a": agent_id, "v": version},
    )
    if row is None:
        raise ConfigCenterError(f"agent {agent_id} has no version {version}")
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE meta.agents SET current_version_id = :v, updated_at = now() "
                    "WHERE id = :a"),
            {"v": row["id"], "a": agent_id},
        )
    return get_agent(engine, agent_id)


def get_agent(engine: Engine, agent_id: int) -> dict:
    row = _one(
        engine,
        "SELECT a.id, a.name, a.description, av.version AS current_version, "
        "a.created_at, a.updated_at FROM meta.agents a "
        "JOIN meta.agent_versions av ON av.id = a.current_version_id "
        "WHERE a.id = :a",
        {"a": agent_id},
    )
    if row is None:
        raise ConfigCenterError(f"agent {agent_id} not found")
    versions = _rows(
        engine,
        "SELECT av.version, av.provider_config_id, av.provider_config_version, "
        "av.model, av.prompt_id, av.prompt_version, av.params, av.notes, av.created_at, "
        "pc.name AS provider_config_name, p.name AS prompt_name "
        "FROM meta.agent_versions av "
        "JOIN meta.provider_configs pc ON pc.id = av.provider_config_id "
        "JOIN meta.prompts p ON p.id = av.prompt_id "
        "WHERE av.agent_id = :a ORDER BY av.version DESC",
        {"a": agent_id},
    )
    row["versions"] = versions
    return row


def list_agents(engine: Engine) -> list[dict]:
    ids = _rows(
        engine,
        "SELECT a.id FROM meta.agents a "
        "JOIN meta.agent_versions av ON av.id = a.current_version_id ORDER BY a.id",
    )
    return [get_agent(engine, r["id"]) for r in ids]


# ─────────────────────── experiment integration ─────────────────────────


def resolve_agent_version(engine: Engine, agent_version_id: int) -> dict:
    """Materialize a runnable recipe from one agent version.

    Returns base_url/api_key/transport/model/prompt content/params/stage.
    Used by experiment_service to run BY AGENT.
    """
    av = _one(
        engine,
        "SELECT av.*, pc.id AS pc_id, p.stage AS prompt_stage, "
        "pcv.name AS provider_config_name, p.name AS prompt_name "
        "FROM meta.agent_versions av "
        "JOIN meta.provider_configs pc ON pc.id = av.provider_config_id "
        "JOIN meta.prompts p ON p.id = av.prompt_id "
        "JOIN meta.provider_config_versions pcv "
        "  ON pcv.config_id = av.provider_config_id "
        " AND pcv.version = av.provider_config_version "
        "WHERE av.id = :v",
        {"v": agent_version_id},
    )
    if av is None:
        raise ConfigCenterError(f"agent version {agent_version_id} not found")
    pcv = _one(
        engine,
        "SELECT base_url, transport FROM meta.provider_config_versions "
        "WHERE config_id = :c AND version = :v",
        {"c": av["provider_config_id"], "v": av["provider_config_version"]},
    )
    if pcv is None:
        raise ConfigCenterError(
            f"agent pins provider config {av['provider_config_id']} "
            f"v{av['provider_config_version']} which no longer exists"
        )
    pv = _one(
        engine,
        "SELECT content FROM meta.prompt_versions WHERE prompt_id = :p "
        "AND version = :v",
        {"p": av["prompt_id"], "v": av["prompt_version"]},
    )
    if pv is None:
        raise ConfigCenterError(
            f"agent pins prompt {av['prompt_id']} v{av['prompt_version']} "
            "which no longer exists"
        )
    _current, api_key = _decrypt_provider_key(engine, av["provider_config_id"])
    return {
        "agent_version_id": agent_version_id,
        "transport": pcv["transport"],
        "base_url": pcv["base_url"],
        "api_key": api_key,
        "model": av["model"],
        "prompt_content": pv["content"],
        "params": av["params"] or {},
        "stage": av["prompt_stage"],
        "provider_config_id": av["provider_config_id"],
        "provider_config_version": av["provider_config_version"],
        "provider_config_name": av["provider_config_name"],
        "prompt_id": av["prompt_id"],
        "prompt_version": av["prompt_version"],
        "prompt_name": av["prompt_name"],
    }


# re-export so routes can map both error types uniformly
__all__ = [
    "ConfigCenterError",
    "ExperimentError",
    "create_provider",
    "update_provider",
    "rollback_provider",
    "get_provider",
    "list_providers",
    "fetch_provider_models",
    "create_prompt",
    "update_prompt",
    "rollback_prompt",
    "get_prompt",
    "list_prompts",
    "create_agent",
    "update_agent",
    "rollback_agent",
    "get_agent",
    "list_agents",
    "resolve_agent_version",
]
