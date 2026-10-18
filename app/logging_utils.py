"""Logging helpers.

Everything goes to stdout so it shows up in ``uvicorn`` locally and in the
hosting provider's log viewer in the cloud.  Conversation events (tool calls,
the final collected payload, end-of-call reports) are logged as single-line
JSON so they are easy to grep.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Quieten noisy libraries but keep our own modules verbose.
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")
    _CONFIGURED = True


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one JSON line: ``{"event": "...", ...fields}``."""
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
