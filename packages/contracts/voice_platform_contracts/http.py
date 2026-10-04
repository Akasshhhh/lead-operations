"""Correlation header contract shared by HTTP service boundaries."""

import re
from uuid import uuid4


def normalize_request_id(value: str | None) -> str:
    """Keep bounded, printable IDs compatible with the outbox storage column."""
    if value and re.fullmatch(r"[A-Za-z0-9._-]{1,120}", value):
        return value
    return str(uuid4())
