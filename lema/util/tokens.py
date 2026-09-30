"""Cheap, dependency-free token estimation.

We deliberately avoid pulling in a tokenizer: exact counts are not required for
context budgeting, and a tokenizer would tie us to one vendor's vocabulary.
The estimator is intentionally conservative (it over-counts slightly) so the
compaction trigger fires before a provider rejects the request.
"""

from __future__ import annotations

import json
from typing import Any

#: Empirically ~3.6 chars/token for source code and English prose mixed.
CHARS_PER_TOKEN = 3.6

#: Per-message protocol overhead (role markers, delimiters).
MESSAGE_OVERHEAD = 4


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN) + 1)


def estimate_obj_tokens(obj: Any) -> int:
    if obj is None:
        return 0
    if isinstance(obj, str):
        return estimate_tokens(obj)
    try:
        return estimate_tokens(json.dumps(obj, default=str))
    except (TypeError, ValueError):
        return estimate_tokens(str(obj))


def truncate_middle(text: str, max_chars: int, note: str = "truncated") -> str:
    """Keep the head and tail of a long string - errors usually live at the tail."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max_chars // 2
    head = text[: keep]
    tail = text[-(max_chars - keep) :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n... [{note}: {omitted} characters omitted] ...\n\n{tail}"
