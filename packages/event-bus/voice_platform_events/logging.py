"""Worker events exclude payloads, connection URLs, and raw exception messages."""

import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger("voice_platform.events")


def emit(operation: str, **fields: object) -> None:
    logger.info(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "service": "event-worker",
                "operation": operation,
                **fields,
            }
        )
    )
