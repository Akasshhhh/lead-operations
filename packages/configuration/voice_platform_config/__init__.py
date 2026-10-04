"""Shared configuration primitives for platform services."""

from .service_auth import SERVICE_TOKEN_HEADER, service_token_is_valid
from .settings import ConfigurationError, Settings

__all__ = ["ConfigurationError", "SERVICE_TOKEN_HEADER", "Settings", "service_token_is_valid"]
