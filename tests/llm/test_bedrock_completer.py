"""Tests for the bedrock_completer module."""

from __future__ import annotations

import os
from unittest.mock import patch

from wordforge.llm.bedrock_completer import register_if_env_key


def test_register_if_env_key_returns_empty_when_no_creds():
    """With no AWS creds in env, register_if_env_key returns {}."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID")
    }
    with patch.dict(os.environ, env, clear=True):
        result = register_if_env_key()
    assert result == {}


def test_register_if_env_key_returns_completer_when_bearer_token_set():
    """With AWS_BEARER_TOKEN_BEDROCK set, register_if_env_key returns a bedrock completer."""
    pytest = __import__("pytest")
    try:
        import boto3  # noqa: F401
    except ImportError:
        pytest.skip("boto3 not installed")

    env = dict(os.environ)
    env["AWS_BEARER_TOKEN_BEDROCK"] = "fake-token"
    with patch.dict(os.environ, env, clear=True):
        result = register_if_env_key()
    assert "bedrock" in result
    assert callable(result["bedrock"])
