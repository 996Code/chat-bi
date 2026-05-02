import logging
import json
import re
from typing import Optional


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        # Add optional context fields if present
        for field in ("request_id", "tenant_id", "user_id", "duration_ms"):
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value
        return json.dumps(log_entry, ensure_ascii=False)


def mask_sensitive(value: str) -> str:
    """Mask sensitive data in logs."""
    if not value:
        return value
    # Mask passwords
    if "@" in value:
        # Email: show first and last char of local part
        local, domain = value.split("@", 1)
        if len(local) > 2:
            return f"{local[0]}***{local[-1]}@{domain}"
        return f"***@{domain}"
    return "***"


def get_logger(module: str) -> logging.Logger:
    logger = logging.getLogger(module)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
