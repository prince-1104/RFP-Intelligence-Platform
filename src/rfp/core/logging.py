import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only application-controlled event names; never serialize exception detail.
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "request_id": request_id.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            # Callers pass identifiers, counts, timings and codes only, never document text,
            # URLs or credentials. Keys cannot override the envelope.
            payload.update({key: value for key, value in fields.items() if key not in payload})
        return json.dumps(payload, default=str)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any):
    logger.log(level, event, extra={"fields": fields})


def configure_logging(level: str) -> None:
    logger = logging.getLogger("rfp")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
