"""Text-based tool-calling protocol.

Small local models frequently lack native structured tool calling, or implement
it badly.  Rather than excluding them, providers can fall back to this
protocol: tool schemas are rendered into the system prompt and the model is
asked to emit a fenced JSON block.  The parser is deliberately forgiving
because small models are sloppy about fences and whitespace.

    ```tool_call
    {"name": "read_file", "arguments": {"path": "src/main.py"}}
    ```
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from lema.providers.base import Message, Role, ToolCall, ToolSpec

FENCE_RE = re.compile(
    r"```(?:tool_call|tool|json_tool_call)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
#: Fallback: a bare fenced JSON object that looks like a tool call.
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", re.DOTALL)


def render_tools_prompt(tools: Sequence[ToolSpec]) -> str:
    """Describe the available tools for a model without native tool calling."""
    lines = [
        "# Tool calling protocol",
        "",
        "You can call tools. To call a tool, emit a fenced block exactly like this:",
        "",
        "```tool_call",
        '{"name": "<tool_name>", "arguments": {<json arguments>}}',
        "```",
        "",
        "Rules:",
        "- Emit the block on its own, with no commentary inside the fence.",
        "- You may emit several blocks in one reply to run tools in parallel.",
        "- After each call you will receive a TOOL RESULT message; continue from there.",
        "- When the task is finished, reply with plain text and no tool_call block.",
        "",
        "## Available tools",
        "",
    ]
    for tool in tools:
        lines.append(f"### {tool.name}")
        lines.append(tool.description.strip())
        schema = json.dumps(tool.parameters, indent=2, sort_keys=True)
        lines.append("Input schema:")
        lines.append(f"```json\n{schema}\n```")
        lines.append("")
    return "\n".join(lines)


def _coerce_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    if value is None:
        return {}
    return {"value": value}


def _extract_candidates(text: str) -> list[str]:
    candidates = FENCE_RE.findall(text)
    if candidates:
        return candidates
    # Some models drop the custom fence tag; accept plain JSON fences that have
    # the shape of a tool call.
    out = []
    for block in JSON_FENCE_RE.findall(text):
        if '"name"' in block and ("arguments" in block or "parameters" in block):
            out.append(block)
    return out


def parse_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """Split model output into prose and the tool calls it requested."""
    if not text:
        return "", []

    calls: list[ToolCall] = []
    candidates = _extract_candidates(text)

    for block in candidates:
        block = block.strip()
        if not block:
            continue
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            # Try to recover the first JSON object in the block.
            match = re.search(r"\{.*\}", block, re.DOTALL)
            payload = None
            if match is not None:
                try:
                    payload = json.loads(match.group(0))
                except json.JSONDecodeError:
                    payload = None
            if payload is None:
                # Never drop a malformed block silently: surface it as a failed
                # call so the model is told to re-emit valid JSON.
                name_match = re.search(r'"(?:name|tool)"\s*:\s*"([^"]+)"', block)
                calls.append(
                    ToolCall(
                        name=name_match.group(1) if name_match else "__invalid__",
                        arguments={},
                        parse_error=(
                            "the tool_call block did not contain valid JSON "
                            f"(it may have been truncated): {block[:200]!r}"
                        ),
                    )
                )
                continue
        if not isinstance(payload, dict):
            continue
        name = payload.get("name") or payload.get("tool") or payload.get("function")
        if not name:
            continue
        raw_args = payload.get("arguments", payload.get("parameters", payload.get("input", {})))
        calls.append(ToolCall(name=str(name), arguments=_coerce_arguments(raw_args)))

    # Strip the fences from the visible prose.
    prose = FENCE_RE.sub("", text)
    if candidates and not FENCE_RE.search(text):
        prose = JSON_FENCE_RE.sub("", text)
    return prose.strip(), calls


def flatten_for_text_protocol(messages: Sequence[Message]) -> list[Message]:
    """Rewrite structured tool traffic into plain text turns.

    Providers using the text protocol cannot represent ``role=tool`` messages
    or assistant ``tool_calls``, so we serialise them into the conversation in
    a format the model can read back.
    """
    out: list[Message] = []
    for msg in messages:
        if msg.role is Role.TOOL:
            body = f"TOOL RESULT [{msg.name}]:\n{msg.content}"
            if out and out[-1].role is Role.USER and out[-1].metadata.get("_synthetic_tool"):
                out[-1] = Message(
                    role=Role.USER,
                    content=out[-1].content + "\n\n" + body,
                    metadata={"_synthetic_tool": True},
                )
            else:
                out.append(Message(role=Role.USER, content=body, metadata={"_synthetic_tool": True}))
            continue

        if msg.role is Role.ASSISTANT and msg.tool_calls:
            blocks = [msg.content.strip()] if msg.content.strip() else []
            for call in msg.tool_calls:
                payload = json.dumps({"name": call.name, "arguments": call.arguments})
                blocks.append(f"```tool_call\n{payload}\n```")
            out.append(Message(role=Role.ASSISTANT, content="\n".join(blocks)))
            continue

        out.append(msg)
    return out
