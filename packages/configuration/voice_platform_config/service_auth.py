"""Small service-to-service authentication primitive.

The shared-secret mode is intentionally simple for local Compose development.
Production deployments must configure a non-empty token; mTLS or JWT workload
identity can replace this helper without changing the gateway client contract.
"""

from __future__ import annotations

import hmac

SERVICE_TOKEN_HEADER = "X-Service-Token"
DEVELOPMENT_SERVICE_TOKEN = "local-lead-service-token"


def validate_service_auth(environment: str, token: str | None) -> str | None:
    """Fail closed on invalid environments or development credentials in production."""
    if environment not in {"local", "test", "production"}:
        raise ValueError("APP_ENV must be local, test, or production")
    token = token.strip() if token else None
    if token and (not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ValueError("LEAD_SERVICE_AUTH_TOKEN must contain printable ASCII without whitespace")
    if environment == "production" and (not token or token == DEVELOPMENT_SERVICE_TOKEN):
        raise ValueError("LEAD_SERVICE_AUTH_TOKEN must be a non-demo secret in production")
    return token or None


def service_token_is_valid(expected: str | None, provided: str | None) -> bool:
    """Compare service tokens without leaking timing information."""

    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())
