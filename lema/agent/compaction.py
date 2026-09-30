"""Context compaction.

When a session outgrows the model's context window we replace the older part
of the transcript with a structured summary produced by the model itself, and
keep the most recent turns verbatim.

Two invariants make this safe:

* We never split an assistant message from the tool results that answer its
  tool calls - providers reject that, and the model gets confused by dangling
  tool references.
* If the model cannot produce a summary (provider down, etc.) we fall back to
  a deterministic, locally-built summary rather than losing the session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from lema.agent.prompts import COMPACTION_PROMPT
from lema.providers.base import Message, ModelProvider, ProviderError, Role
from lema.util.tokens import estimate_tokens, truncate_middle


@dataclass
class CompactionResult:
    summary: str
    removed_messages: int
    kept_messages: int
    tokens_before: int
    tokens_after: int
    fallback: bool = False

    @property
    def saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    def render(self) -> str:
        mode = " (local fallback)" if self.fallback else ""
        return (
            f"Compacted{mode}: {self.removed_messages} messages summarised, "
            f"{self.kept_messages} kept. "
            f"~{self.tokens_before:,} -> ~{self.tokens_after:,} tokens "
            f"(saved ~{self.saved:,})."
        )


def _safe_split_index(messages: Sequence[Message], keep_recent: int) -> int:
    """Find a cut point that does not orphan tool results.

    Returns the index where the "kept" tail begins.
    """
    if len(messages) <= keep_recent:
        return 0
    index = len(messages) - keep_recent

    # Walk backwards until the tail starts at a clean boundary: a user message,
    # or an assistant message whose tool results are all inside the tail.
    while index > 0:
        message = messages[index]
        if message.role is Role.TOOL:
            index -= 1
            continue
        if message.role is Role.ASSISTANT and message.tool_calls:
            # Keeping an assistant message means keeping its tool results too;
            # they follow it, so this boundary is fine.
            break
        if message.role in (Role.USER, Role.ASSISTANT):
            break
        index -= 1

    # Never leave a leading tool message at the head of the tail.
    while index < len(messages) and messages[index].role is Role.TOOL:
        index += 1
    return index


def _transcript(messages: Sequence[Message], max_chars: int = 60000) -> str:
    parts: list[str] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            continue
        if message.role is Role.TOOL:
            body = truncate_middle(message.content, 2000, "tool output trimmed")
            parts.append(f"[tool:{message.name}] {body}")
            continue
        prefix = {Role.USER: "USER", Role.ASSISTANT: "ASSISTANT"}[message.role]
        text = message.content.strip()
        if message.tool_calls:
            calls = ", ".join(
                f"{c.name}({', '.join(f'{k}={str(v)[:60]!r}' for k, v in list(c.arguments.items())[:3])})"
                for c in message.tool_calls
            )
            text = (text + "\n" if text else "") + f"[called: {calls}]"
        if text:
            parts.append(f"{prefix}: {truncate_middle(text, 4000, 'trimmed')}")
    return truncate_middle("\n\n".join(parts), max_chars, "older history trimmed")


def _local_summary(messages: Sequence[Message]) -> str:
    """Deterministic summary used when the model is unavailable."""
    tasks: list[str] = []
    tools_used: dict[str, int] = {}
    files: list[str] = []
    commands: list[str] = []
    errors: list[str] = []

    for message in messages:
        if message.role is Role.USER and not message.metadata.get("_synthetic_tool"):
            tasks.append(message.content.strip()[:400])
        for call in message.tool_calls:
            tools_used[call.name] = tools_used.get(call.name, 0) + 1
            path = call.arguments.get("path") or call.arguments.get("file")
            if isinstance(path, str) and path not in files:
                files.append(path)
            command = call.arguments.get("command")
            if isinstance(command, str):
                commands.append(command[:160])
        if message.role is Role.TOOL and message.content.startswith("ERROR"):
            errors.append(message.content.splitlines()[0][:200])

    lines = ["## Task"]
    lines += [f"- {t}" for t in tasks[:8]] or ["- (none recorded)"]
    lines.append("\n## Files")
    lines += [f"- {f}" for f in files[:30]] or ["- (none)"]
    lines.append("\n## Commands and results")
    lines += [f"- {c}" for c in commands[-20:]] or ["- (none)"]
    lines.append("\n## Discoveries")
    lines.append(
        "- Tools used: "
        + (", ".join(f"{k}×{v}" for k, v in sorted(tools_used.items(), key=lambda kv: -kv[1])) or "none")
    )
    lines.append("\n## Outstanding")
    lines += [f"- error seen: {e}" for e in errors[-10:]] or [
        "- No errors recorded before compaction."
    ]
    lines.append(
        "\n(Automatically generated locally because the model summary was unavailable.)"
    )
    return "\n".join(lines)


async def compact(
    messages: list[Message],
    provider: ModelProvider,
    *,
    keep_recent: int = 12,
    system_prompt_tokens: int = 0,
    max_summary_tokens: int = 2000,
) -> tuple[list[Message], CompactionResult]:
    """Summarise older history and return the new message list."""
    tokens_before = system_prompt_tokens + sum(
        estimate_tokens(m.content) + 8 for m in messages
    )

    split = _safe_split_index(messages, keep_recent)
    if split <= 0:
        # Nothing safely removable; keep everything.
        return messages, CompactionResult(
            summary="",
            removed_messages=0,
            kept_messages=len(messages),
            tokens_before=tokens_before,
            tokens_after=tokens_before,
        )

    older = messages[:split]
    recent = messages[split:]

    fallback = False
    try:
        response = await provider.chat(
            [
                Message.system(COMPACTION_PROMPT),
                Message.user(
                    "Here is the session transcript to compact:\n\n" + _transcript(older)
                ),
            ],
            stream=False,
            temperature=0.0,
            max_tokens=max_summary_tokens,
        )
        summary = response.content.strip()
        if not summary:
            raise ProviderError("empty summary")
    except (ProviderError, Exception):  # noqa: BLE001 - compaction must never fail hard
        summary = _local_summary(older)
        fallback = True

    summary_message = Message.user(
        "# Session summary (earlier history was compacted)\n\n"
        + summary
        + "\n\nContinue the work from this state.",
        compaction=True,
    )

    new_messages = [summary_message, *recent]
    tokens_after = system_prompt_tokens + sum(
        estimate_tokens(m.content) + 8 for m in new_messages
    )

    return new_messages, CompactionResult(
        summary=summary,
        removed_messages=len(older),
        kept_messages=len(recent),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        fallback=fallback,
    )
