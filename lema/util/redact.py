"""Redaction of secrets before anything is written to disk or logs."""

from __future__ import annotations

import os
import re
from typing import Any

#: Config/env keys whose *values* must never be logged.
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "authorization",
    "auth",
    "private_key",
    "session_token",
}

REDACTED = "***REDACTED***"

#: Patterns for secrets that appear inline in free text (tool output, prompts).
_INLINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),            # OpenAI style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"),        # Anthropic style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),              # GitHub PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),     # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                  # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),           # Google
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}"),
]

_ASSIGNMENT = re.compile(
    r"(?i)\b(" + "|".join(sorted((re.escape(k) for k in SENSITIVE_KEYS), key=len, reverse=True)) + r")"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}\)]+)"
)


def _env_secret_values() -> list[str]:
    values: list[str] = []
    for key, value in os.environ.items():
        lowered = key.lower()
        if not value or len(value) < 12:
            continue
        if any(marker in lowered for marker in ("key", "token", "secret", "password")):
            values.append(value)
    return values


def redact_text(text: str) -> str:
    """Scrub secrets out of an arbitrary string."""
    if not text:
        return text
    out = text
    for value in _env_secret_values():
        if value in out:
            out = out.replace(value, REDACTED)
    for pattern in _INLINE_PATTERNS:
        out = pattern.sub(REDACTED, out)
    out = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    return out


def redact_value(value: Any, key_hint: str | None = None) -> Any:
    """Recursively redact a JSON-like structure."""
    if key_hint and key_hint.lower().replace("-", "_") in SENSITIVE_KEYS:
        return REDACTED if value else value
    if isinstance(value, dict):
        return {k: redact_value(v, key_hint=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def mask(value: str | None, keep: int = 4) -> str:
    """Show only the tail of a secret, for display purposes (``…abcd``)."""
    if not value:
        return "(unset)"
    if len(value) <= keep:
        return "*" * len(value)
    return "…" + value[-keep:]
