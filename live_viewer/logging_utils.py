"""Logging helpers with credential redaction.

Nothing in this module ever writes a password to disk or to the console.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote, quote_plus

_USERINFO_RE = re.compile(r"(rtsp://[^:/@\s]+:)([^@\s]+)(@)", re.IGNORECASE)


def make_redactor(secrets: dict[str, str] | list[str]):
    values = secrets.values() if isinstance(secrets, dict) else secrets
    variants: set[str] = set()
    for value in values:
        if not value:
            continue
        variants.add(value)
        variants.add(quote(value, safe=""))
        variants.add(quote_plus(value))
    ordered = sorted(variants, key=len, reverse=True)

    def redact(text: str) -> str:
        if not text:
            return text
        for value in ordered:
            text = text.replace(value, "***")
        return _USERINFO_RE.sub(r"\1***\3", text)

    return redact


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, redact):
        super().__init__(fmt)
        self._redact = redact

    def format(self, record: logging.LogRecord) -> str:
        return self._redact(super().format(record))


def setup_logging(logs_dir: Path, secrets: dict[str, str], level: str = "INFO"):
    logs_dir.mkdir(parents=True, exist_ok=True)
    redact = make_redactor(secrets)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    formatter = RedactingFormatter(fmt, redact)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        logs_dir / "viewer.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    return redact
