"""Structured JSON logging that cannot leak a token.

Tokens never reach a log record: call sites pass :func:`token_fingerprint`
instead, which is a short salted-free SHA-256 prefix — enough to correlate two
requests from the same credential, useless for replaying it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from typing import Any, Dict

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}

# Anything whose key looks like a credential is dropped rather than printed,
# so a careless ``extra={"token": ...}`` at some future call site is inert.
_FORBIDDEN_SUBSTRINGS = ("token", "authorization", "api_key", "apikey", "secret", "password")
_ALLOWED_TOKENISH_KEYS = {"token_id", "token_use", "token_fp", "token_kind", "token_present"}


def _safe_extra(record: logging.LogRecord) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _RESERVED or key.startswith("_"):
            continue
        lowered = key.lower()
        if key not in _ALLOWED_TOKENISH_KEYS and any(s in lowered for s in _FORBIDDEN_SUBSTRINGS):
            out[key] = "[redacted]"
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            value = repr(value)
        out[key] = value
    return out


class JsonFormatter(logging.Formatter):
    """One JSON object per line, stable key order."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_safe_extra(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def token_fingerprint(token: str | None) -> str | None:
    """Stable, non-reversible 12-hex-char handle for a credential."""
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # uvicorn installs its own colourful handlers; make them go through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
