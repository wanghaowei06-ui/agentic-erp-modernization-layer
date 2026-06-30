from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, status

# PRD 17.6: privileged memory write tools (register_trusted_capability,
# record_human_approval, record_validation_result) must not be freely invoked
# by an LLM. They must be triggered by UiPath orchestration, validation
# results, or human approval. Endpoints that perform these writes require an
# API key so only authorised callers (UiPath / validation pipeline) can mutate
# the governed memory layer.
MEMORY_WRITE_API_KEY_ENV = "MEMORY_WRITE_API_KEY"


def _configured_api_key() -> Optional[str]:
    """Return the API key configured via environment variable.

    When unset, the service runs in dev/demo mode and memory writes are
    allowed without a token. When set, every memory-write request must
    present a matching token.
    """
    value = os.getenv(MEMORY_WRITE_API_KEY_ENV)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _extract_provided_token(
    x_api_key: Optional[str],
    authorization: Optional[str],
) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return None


def require_memory_write_api_key(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[str]:
    """FastAPI dependency that enforces an API key on memory-write endpoints.

    Behaviour:
    - If ``MEMORY_WRITE_API_KEY`` is not configured: dev/demo mode, request is
      allowed (returns ``None``).
    - If configured and the request omits the token: HTTP 401 Unauthorized.
    - If configured and the request presents a wrong token: HTTP 403 Forbidden.

    Token comparison uses ``secrets.compare_digest`` to avoid timing attacks.
    """
    configured = _configured_api_key()
    if configured is None:
        return None

    provided = _extract_provided_token(x_api_key, authorization)
    if provided is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_api_key",
                "message": (
                    "An API key is required for memory-write endpoints. "
                    f"Provide it via the X-API-Key header or "
                    f"Authorization: Bearer <token>."
                ),
            },
        )
    if not secrets.compare_digest(provided, configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_api_key",
                "message": "The provided API key is not authorised.",
            },
        )
    return provided
