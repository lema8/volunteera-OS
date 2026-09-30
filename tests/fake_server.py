"""A real HTTP server that speaks the OpenAI, Anthropic and Ollama wire formats.

Using a genuine socket server (rather than patching httpx) means the provider
tests exercise the actual request construction, SSE parsing and error handling
that runs in production.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


class FakeAPIServer:
    """Serves scripted responses on a real localhost port."""

    def __init__(self) -> None:
        #: Each entry is a dict describing one response.
        self.script: list[dict[str, Any]] = []
        #: Optional callable(payload, index) -> response spec, consulted
        #: before `script`. Lets a test answer based on the conversation.
        self.policy: Callable[[dict[str, Any], int], dict[str, Any] | None] | None = None
        self.turn = 0
        self.requests: list[dict[str, Any]] = []
        self.headers_seen: list[dict[str, str]] = []
        self._index = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.models: list[str] = ["test-model", "other-model"]

    # ----------------------------------------------------------- lifecycle

    def start(self) -> str:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: ANN002 - silence the server
                return

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_sse(self, chunks: list[dict[str, Any]], raw_lines: list[str] | None = None) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                if raw_lines is not None:
                    for line in raw_lines:
                        self.wfile.write((line + "\n").encode())
                else:
                    for chunk in chunks:
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def do_GET(self):  # noqa: N802
                outer.headers_seen.append(dict(self.headers))
                if self.path.endswith("/models"):
                    body = json.dumps(
                        {"data": [{"id": m} for m in outer.models]}
                    ).encode()
                    self._send(200, body, "application/json")
                    return
                if self.path.endswith("/api/tags"):
                    body = json.dumps(
                        {"models": [{"name": m} for m in outer.models]}
                    ).encode()
                    self._send(200, body, "application/json")
                    return
                self._send(404, b"{}", "application/json")

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
                outer.requests.append({"path": self.path, "body": payload})
                outer.headers_seen.append(dict(self.headers))

                if self.path.endswith("/api/show"):
                    self._send(
                        200,
                        json.dumps({"capabilities": ["completion", "tools"]}).encode(),
                        "application/json",
                    )
                    return

                spec = None
                if outer.policy is not None:
                    spec = outer.policy(payload, outer.turn)
                    outer.turn += 1
                if spec is None:
                    spec = outer._next()
                if spec is None:
                    self._send(500, b'{"error":"no scripted response"}', "application/json")
                    return

                status = spec.get("status", 200)
                if status >= 400:
                    body = json.dumps(spec.get("json", {"error": "failure"})).encode()
                    self._send(status, body, "application/json")
                    return

                if spec.get("sse"):
                    self._send_sse(spec["sse"], spec.get("raw_lines"))
                    return
                if spec.get("ndjson"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in spec["ndjson"]:
                        self.wfile.write((json.dumps(chunk) + "\n").encode())
                    self.wfile.flush()
                    return

                body = json.dumps(spec.get("json", {})).encode()
                self._send(status, body, "application/json")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------ scripting

    def _next(self) -> dict[str, Any] | None:
        if self._index >= len(self.script):
            return self.script[-1] if self.script else None
        spec = self.script[self._index]
        self._index += 1
        return spec

    def reply_json(self, payload: dict[str, Any], status: int = 200) -> "FakeAPIServer":
        self.script.append({"json": payload, "status": status})
        return self

    def reply_sse(self, chunks: list[dict[str, Any]]) -> "FakeAPIServer":
        self.script.append({"sse": chunks})
        return self

    def reply_ndjson(self, chunks: list[dict[str, Any]]) -> "FakeAPIServer":
        self.script.append({"ndjson": chunks})
        return self

    def reply_error(self, status: int, payload: dict[str, Any]) -> "FakeAPIServer":
        self.script.append({"status": status, "json": payload})
        return self


# ------------------------------------------------------------- shorthands


def openai_message(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "model": "test-model",
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def openai_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def anthropic_message(blocks: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "model": "test-model",
        "type": "message",
        "role": "assistant",
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 13, "output_tokens": 5},
    }


def ollama_message(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "model": "test-model",
        "message": message,
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 9,
        "eval_count": 4,
    }
