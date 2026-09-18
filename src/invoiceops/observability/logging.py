from __future__ import annotations

import json
import logging
import re


_SENSITIVE = re.compile(r"(?i)(password|token|authorization|secret|api[_ -]?key)\s*[:=]\s*[^\s,}]+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|\b(?:IBAN\s*)?[A-Z]{2}\d{2}[A-Z0-9]{10,34}\b")


class JsonFormatter(logging.Formatter):
    component = "invoiceops.api"

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "component": self.component,
            "message": _SENSITIVE.sub("[REDACTED]", record.getMessage()),
        }
        for key in ("trace_id", "stage", "duration_ms", "result_code", "version"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(component: str = "invoiceops.api") -> logging.Logger:
    logger = logging.getLogger("invoiceops")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = JsonFormatter()
        formatter.component = component
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
