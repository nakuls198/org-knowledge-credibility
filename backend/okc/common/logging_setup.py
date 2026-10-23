"""Logging setup with a redaction filter so secrets never reach log output."""

from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret)(\s*[=:]\s*)\S+"), r"\1\2***"),
    (re.compile(r"(?i)(\w+(?:\+\w+)?://[^:/\s]+:)[^@\s]+@"), r"\1***@"),
]
_NOISY_LOGGERS = ("elastic_transport", "urllib3", "httpx", "sentence_transformers")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in _SECRET_PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg, record.args = msg, ()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
