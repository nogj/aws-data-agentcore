import json
import logging
from typing import Any


logger = logging.getLogger("data_agent.audit")


def emit(event: str, **fields: Any) -> None:
    """Write a structured audit event while callers control sensitive fields."""

    logger.info(json.dumps({"event": event, **fields}, default=str, sort_keys=True))
