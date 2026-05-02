"""Google Gemini completer (Vertex AI preferred, AI Studio fallback).

Two auth paths:

1. **Vertex AI + SA private key** — enterprise; works from any IP
   including mainland China. Credentials retrieved from AWS Secrets
   Manager at `onchain-risk/gcp-credentials` using the sibling AWS
   keypair (WORDFORGE_GCP_SA_AWS_ACCESS_KEY_ID /
   _SECRET_ACCESS_KEY), OR from a local JSON file pointed at by
   GOOGLE_APPLICATION_CREDENTIALS.

2. **AI Studio API key** — simpler, but Google blocks data-center
   IPs; fine for local dev on residential internet but does NOT work
   from cloud VMs. Triggered by GEMINI_API_KEY env.

Models (pricing see wordforge.llm.pricing):
  gemini-3.1-pro-preview / gemini-3-pro-preview
                      — top quality (reasoning), forces thinking mode like 2.5 Pro
  gemini-3-flash-preview
                      — ~6x cheaper than Pro, supports disable-thinking
  gemini-3.1-flash-lite-preview
                      — cheapest 3.x tier, bulk pattern-matching
  gemini-2.5-pro      — legacy quality floor for mnemonic / paraphrase
  gemini-2.5-flash    — legacy 10-100x cheaper flash tier
  gemini-2.5-flash-lite — legacy cheapest, ok for bulk morphology lookup

Timeouts mirror bedrock_completer (mainland-China SOCKS5 proxy
reality): the generated HTTP client gets 10s connect / 60s read.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from wordforge.llm.client import LLMCompletion
from wordforge.llm.pricing import compute_cost

_log = logging.getLogger(__name__)


def make_gemini_completer():  # noqa: C901
    """Create a completer backed by google-genai SDK.

    Lazy import so CI without the SDK isn't forced to install it.
    Raises RuntimeError if neither auth path is configured.
    """
    try:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types as genai_types  # type: ignore[import-not-found]
        from google.genai.types import HttpOptions  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "google-genai not installed; `pip install google-genai` or "
            "`pip install wordforge[gemini]`"
        ) from e

    client = _build_client(genai, HttpOptions)

    def _completer(*, model: str, prompt: str, **params: Any) -> LLMCompletion:
        max_tokens = int(params.get("max_tokens", 2048))
        temperature = float(params.get("temperature", 0))

        # Must use the typed GenerateContentConfig, NOT a plain dict —
        # when the SDK serializes a dict it routes keys through the
        # wrong naming convention and the API rejects "thinkingConfig at
        # 'generation_config'". A typed object uses the right path.
        thinking_config = None
        effective_max_tokens = max_tokens
        # Gemini thinking-mode handling (2.5 + 3.x share the same contract):
        # - Flash / Flash-Lite: thinking optional; we disable (budget=0)
        #   because wordforge tasks are narrow (JSON extraction, pun
        #   generation) and don't benefit from CoT.
        # - Pro:   thinking is MANDATORY — the API rejects budget=0 with
        #   "This model only works in thinking mode". Caller's max_tokens
        #   is treated as the output budget; we silently add THINK_BUDGET
        #   on top, since otherwise a 1024-token mnemonic prompt gets
        #   MAX_TOKENS'd after thinking consumes all of it.
        # Thinking tokens are billed as output → no separate cost path.
        _is_pro = any(
            tag in model
            for tag in ("gemini-2.5-pro", "gemini-3-pro", "gemini-3.1-pro")
        )
        _is_flash = any(
            tag in model
            for tag in (
                "gemini-2.5-flash",
                "gemini-3-flash",
                "gemini-3.1-flash",
            )
        )
        if _is_pro:
            _think_budget = int(os.environ.get("WORDFORGE_GEMINI_PRO_THINK_BUDGET", "2048"))
            thinking_config = genai_types.ThinkingConfig(thinking_budget=_think_budget)
            effective_max_tokens = max_tokens + _think_budget
        elif _is_flash:
            thinking_config = genai_types.ThinkingConfig(thinking_budget=0)

        config = genai_types.GenerateContentConfig(
            max_output_tokens=effective_max_tokens,
            temperature=temperature,
            thinking_config=thinking_config,
        )
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

        text = response.text or ""
        if not text:
            # Gemini blocks via prompt_feedback.block_reason or
            # candidates[0].finish_reason ∈ {SAFETY, RECITATION, OTHER}.
            # Mirror Bedrock's content_filtered soft-degrade: return empty
            # text + zero cost so the caller treats it as "model declined".
            finish = _extract_finish_reason(response)
            if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
                _log.warning(
                    "Gemini refused prompt (finish_reason=%s, model=%s)",
                    finish, model,
                )
                return LLMCompletion(
                    response={"text": "", "in_tok": 0, "out_tok": 0},
                    cost_usd=0.0,
                )
            raise RuntimeError(
                f"Gemini returned empty text (finish_reason={finish}, model={model})"
            )

        # usage_metadata fields: prompt_token_count, candidates_token_count.
        # When usage isn't present (rare), fall back to 0 and flag.
        usage = getattr(response, "usage_metadata", None)
        in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
        out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        cost = compute_cost(model, in_tok, out_tok)
        return LLMCompletion(
            response={"text": text, "in_tok": in_tok, "out_tok": out_tok},
            cost_usd=cost,
        )

    return _completer


def _build_client(genai, HttpOptions):
    """Pick auth path: Vertex AI SA first, AI Studio key second.

    Vertex AI resolution order:
      a) GOOGLE_APPLICATION_CREDENTIALS → local JSON file
      b) WORDFORGE_GCP_SA_AWS_ACCESS_KEY_ID / _SECRET_ACCESS_KEY →
         pull SA JSON from Secrets Manager (onchain-risk/gcp-credentials)

    AI Studio:
      c) GEMINI_API_KEY → simple but residential-IP-only

    Timeout applied via HttpOptions(timeout=60000)  (ms). Connect
    timeout is fixed by google-genai at 10s internally — not overridable
    via public API; matches bedrock_completer default anyway.
    """
    # api_version left at SDK default. AI Studio's v1 endpoint rejects
    # thinking_config ("Unknown name thinkingConfig"); default (v1beta)
    # supports it. Vertex AI path is unaffected.
    http_opts = HttpOptions(
        timeout=int(os.environ.get("WORDFORGE_GEMINI_READ_TIMEOUT_MS", "60000")),
    )

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    sa_key = _load_sa_key_if_available()
    if sa_key and project:
        from google.oauth2 import service_account  # type: ignore[import-not-found]
        creds = service_account.Credentials.from_service_account_info(
            sa_key, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _log.info("Gemini: using Vertex AI + SA key (project=%s)", project)
        return genai.Client(
            project=project,
            location=location,
            vertexai=True,
            credentials=creds,
            http_options=http_opts,
        )

    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        _log.info("Gemini: using AI Studio API key (residential IP required)")
        # Explicit vertexai=False: otherwise google-genai auto-switches to
        # Vertex mode when GOOGLE_CLOUD_PROJECT is set in the environment,
        # but without valid ADC this leads to cryptic 400 errors on fields
        # that AI Studio supports but Vertex names differently (the
        # "Unknown name 'thinkingConfig' at 'generation_config'" failure).
        return genai.Client(api_key=api_key, vertexai=False, http_options=http_opts)

    raise RuntimeError(
        "No Gemini credentials: set GOOGLE_APPLICATION_CREDENTIALS or "
        "WORDFORGE_GCP_SA_AWS_ACCESS_KEY_ID+SECRET or GEMINI_API_KEY"
    )


def _load_sa_key_if_available() -> dict | None:
    """Return SA key dict or None. Does NOT raise; absence is valid."""
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("GOOGLE_APPLICATION_CREDENTIALS unreadable: %s", e)

    aws_key = os.environ.get("WORDFORGE_GCP_SA_AWS_ACCESS_KEY_ID")
    aws_secret = os.environ.get("WORDFORGE_GCP_SA_AWS_SECRET_ACCESS_KEY")
    secret_id = os.environ.get(
        "WORDFORGE_GCP_SA_SECRET_ID", "onchain-risk/gcp-credentials"
    )
    if aws_key and aws_secret:
        try:
            import base64

            import boto3  # type: ignore[import-not-found]

            sm = boto3.client(
                "secretsmanager",
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=os.environ.get("AWS_REGION", "us-east-1"),
            )
            raw = sm.get_secret_value(SecretId=secret_id)["SecretString"]
            return json.loads(base64.b64decode(raw).decode())
        except Exception as e:  # noqa: BLE001
            _log.warning("Failed loading SA key from Secrets Manager: %s", e)

    return None


def _extract_finish_reason(response: Any) -> str:
    """Dig finish_reason out of genai response; shape varies by version."""
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = getattr(candidates[0], "finish_reason", None)
            if reason is not None:
                return str(reason).rsplit(".", 1)[-1]  # strip enum prefix
    except (AttributeError, IndexError):
        pass
    try:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None:
            block = getattr(feedback, "block_reason", None)
            if block is not None:
                return str(block).rsplit(".", 1)[-1]
    except AttributeError:
        pass
    return "unknown"


def register_if_env_key() -> dict[str, Any]:
    """Return {'gemini': completer} if Gemini creds present, else {}.

    Matches the bedrock_completer contract so stages/registry.py can
    compose providers the same way.
    """
    _gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    has_vertex_sa = bool(
        (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and _gcp_project)
        or (os.environ.get("WORDFORGE_GCP_SA_AWS_ACCESS_KEY_ID") and _gcp_project)
    )
    has_ai_studio = bool(os.environ.get("GEMINI_API_KEY"))
    if not (has_vertex_sa or has_ai_studio):
        return {}
    try:
        return {"gemini": make_gemini_completer()}
    except RuntimeError:
        return {}
