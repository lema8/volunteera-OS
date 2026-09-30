"""Session logging.

Every session writes a JSONL transcript under ``~/.local/state/lema/sessions``
containing tool calls, model requests/responses, errors and skill events.
All payloads pass through the redactor first, so API keys never land on disk.

JSONL is chosen deliberately: it is append-only (a crash cannot corrupt the
earlier records), greppable, and trivially loadable with ``jq``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lema.providers.base import ChatResponse, Message
from lema.tools.base import ToolResult
from lema.util import paths
from lema.util.redact import redact_text, redact_value
from lema.util.tokens import truncate_middle

LOGGER_NAME = "lema"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def setup_logging(level: str = "info", *, debug: bool = False) -> logging.Logger:
    """Configure the stderr logger. Stdout stays clean for the UI."""
    logger = logging.getLogger(LOGGER_NAME)
    resolved = logging.DEBUG if debug else _LEVELS.get(level.lower(), logging.INFO)
    logger.setLevel(resolved)
    logger.handlers.clear()

    # The console only ever sees warnings and errors: routine INFO chatter
    # interleaving with the agent UI is noise. Everything goes to the file.
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
        )
    )
    logger.addHandler(handler)

    try:
        file_path = paths.ensure_dir(paths.state_dir()) / ("debug.log" if debug else "lema.log")
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG if debug else resolved)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)
        if debug:
            logger.debug("debug logging to %s", file_path)
    except OSError:  # pragma: no cover - read-only state dir
        pass

    logger.propagate = False
    return logger


@dataclass
class SessionMeta:
    id: str
    started_at: float
    cwd: str
    provider: str
    model: str


class SessionLogger:
    """Append-only JSONL logger for one session."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        log_model_io: bool = True,
        directory: Path | None = None,
        session_id: str | None = None,
        max_files: int = 200,
    ):
        self.enabled = enabled
        self.log_model_io = log_model_io
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.directory = Path(directory or (paths.state_dir() / "sessions"))
        self.path: Path | None = None
        self._logger = logging.getLogger(LOGGER_NAME)
        self._counter = 0

        if self.enabled:
            try:
                paths.ensure_dir(self.directory)
                self.path = self.directory / f"{self.session_id}.jsonl"
                self._prune(max_files)
            except OSError as exc:  # pragma: no cover - read-only FS etc.
                self._logger.warning("session logging disabled: %s", exc)
                self.enabled = False

    def _prune(self, max_files: int) -> None:
        try:
            files = sorted(
                self.directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except OSError:
            return
        for stale in files[max_files:]:
            try:
                stale.unlink()
            except OSError:
                continue

    # ------------------------------------------------------------- writing

    def _write(self, kind: str, payload: dict[str, Any]) -> None:
        if not self.enabled or self.path is None:
            return
        self._counter += 1
        record = {
            "seq": self._counter,
            "ts": time.time(),
            "kind": kind,
            **payload,
        }
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover
            self._logger.debug("failed to write session log: %s", exc)

    # ------------------------------------------------------------- events

    def log_session_start(self, meta: SessionMeta, config_summary: dict[str, Any]) -> None:
        self._write(
            "session_start",
            {
                "session_id": meta.id,
                "cwd": meta.cwd,
                "provider": meta.provider,
                "model": meta.model,
                "pid": os.getpid(),
                "config": redact_value(config_summary),
            },
        )

    def log_event(self, kind: str, payload: dict[str, Any]) -> None:
        self._write(kind, redact_value(payload))

    def log_tool_call(self, name: str, arguments: dict[str, Any], *, depth: int = 0) -> None:
        self._logger.debug("tool call %s depth=%s", name, depth)
        self._write(
            "tool_call",
            {"tool": name, "depth": depth, "arguments": redact_value(arguments)},
        )

    def log_tool_result(self, name: str, result: ToolResult, *, depth: int = 0) -> None:
        self._write(
            "tool_result",
            {
                "tool": name,
                "depth": depth,
                "ok": result.ok,
                "failure_kind": result.failure_kind.value,
                "duration_ms": round(result.duration_ms, 1),
                "error": redact_text(result.error or "") or None,
                "output": redact_text(truncate_middle(result.output, 4000, "log truncated")),
            },
        )

    def log_model_request(
        self, provider: str, messages: Sequence[Message], tool_count: int
    ) -> None:
        if not self.log_model_io:
            self._write("model_request", {"provider": provider, "messages": len(messages)})
            return
        self._write(
            "model_request",
            {
                "provider": provider,
                "tool_count": tool_count,
                "messages": [
                    {
                        "role": m.role.value,
                        "content": redact_text(truncate_middle(m.content, 3000, "log truncated")),
                        "tool_calls": [c.to_dict() for c in m.tool_calls] or None,
                    }
                    for m in messages
                ],
            },
        )

    def log_model_response(self, response: ChatResponse) -> None:
        payload: dict[str, Any] = {
            "model": response.model,
            "finish_reason": response.finish_reason,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "tool_calls": [c.name for c in response.tool_calls],
        }
        if self.log_model_io:
            payload["content"] = redact_text(
                truncate_middle(response.content, 4000, "log truncated")
            )
            payload["tool_call_args"] = [redact_value(c.arguments) for c in response.tool_calls]
        self._write("model_response", payload)

    def log_skill_event(self, action: str, name: str, version: int, path: str) -> None:
        self._logger.info("skill %s: %s v%s", action, name, version)
        self._write(
            "skill_event",
            {"action": action, "skill": name, "version": version, "path": path},
        )

    def log_error(self, message: str, **extra: Any) -> None:
        self._logger.error("%s", message)
        self._write("error", {"message": redact_text(message), **redact_value(extra)})

    def close(self, summary: dict[str, Any] | None = None) -> None:
        if summary is not None:
            self._write("session_end", redact_value(summary))
        else:
            self._write("session_end", {})


def list_sessions(limit: int = 20) -> list[Path]:
    directory = paths.state_dir() / "sessions"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
