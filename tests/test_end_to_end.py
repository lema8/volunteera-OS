"""End-to-end tests through the installed `lema` binary.

These drive the real CLI process against a real HTTP model endpoint speaking
the OpenAI wire format. Nothing here is mocked inside the harness: the binary
parses its own arguments, loads its own config, builds its real tool registry,
executes real tools against real files, and talks to a real socket.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fake_server import FakeAPIServer, openai_message, openai_tool_call

LEMA = Path(sys.executable).parent / "lema"


@pytest.fixture
def api():
    server = FakeAPIServer()
    server.url = server.start()  # type: ignore[attr-defined]
    yield server
    server.stop()


def run_lema(api, workspace, task, *extra, timeout=180):
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [
            str(LEMA),
            "--provider", "openai",
            "--base-url", api.url + "/v1",
            "--api-key", "test-key",
            "--model", "test-model",
            "--permissions", "unrestricted",
            "--no-stream",
            "--print",
            *extra,
            task,
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def last_user_visible(payload):
    """The content of the newest tool result in the request, if any."""
    for message in reversed(payload.get("messages", [])):
        if message.get("role") == "tool":
            return message.get("content") or ""
    return ""


def tool_names_offered(payload):
    return {t["function"]["name"] for t in payload.get("tools", [])}


# --------------------------------------------------------------------------


def test_end_to_end_plain_answer(api, workspace):
    api.reply_json(openai_message("The answer is 42."))
    result = run_lema(api, workspace, "what is the answer?")
    assert result.returncode == 0, result.stderr
    assert "The answer is 42." in result.stdout


def test_end_to_end_file_edit(api, workspace):
    (workspace / "greet.py").write_text("def greet():\n    print('hi')\n")

    def policy(payload, index):
        if index == 0:
            return {
                "json": openai_message(
                    "", [openai_tool_call("c1", "read_file", {"path": "greet.py"})]
                )
            }
        if index == 1:
            assert "def greet" in last_user_visible(payload)
            return {
                "json": openai_message(
                    "",
                    [
                        openai_tool_call(
                            "c2",
                            "edit_file",
                            {
                                "path": "greet.py",
                                "old_string": "print('hi')",
                                "new_string": "print('hello, world')",
                            },
                        )
                    ],
                )
            }
        return {"json": openai_message("Updated greet.py to say hello, world.")}

    api.policy = policy
    result = run_lema(api, workspace, "make greet say hello, world")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (workspace / "greet.py").read_text() == (
        "def greet():\n    print('hello, world')\n"
    )
    assert "hello, world" in result.stdout


def test_end_to_end_shell_and_recovery(api, workspace):
    """The model runs a failing command, sees the error, fixes it, verifies."""
    seen_error = {}

    def policy(payload, index):
        if index == 0:
            return {
                "json": openai_message(
                    "", [openai_tool_call("c1", "run_command", {"command": "python3 calc.py"})]
                )
            }
        if index == 1:
            seen_error["text"] = last_user_visible(payload)
            return {
                "json": openai_message(
                    "",
                    [
                        openai_tool_call(
                            "c2",
                            "write_file",
                            {"path": "calc.py", "content": "print(6 * 7)\n"},
                        )
                    ],
                )
            }
        if index == 2:
            return {
                "json": openai_message(
                    "", [openai_tool_call("c3", "run_command", {"command": "python3 calc.py"})]
                )
            }
        assert "42" in last_user_visible(payload)
        return {"json": openai_message("calc.py now prints 42.")}

    api.policy = policy
    result = run_lema(api, workspace, "make calc.py print 42 and verify it")

    assert result.returncode == 0, result.stderr
    assert (workspace / "calc.py").exists()
    assert "42" in result.stdout
    # The harness told the model exactly why the first attempt failed.
    assert "exit_code" in seen_error["text"] or "No such file" in seen_error["text"]


def test_end_to_end_dynamic_skill_creation(api, workspace):
    """Spec §37, through the installed binary.

    The model discovers it has no relevant skill, writes one with create_skill,
    and the very next request shows the skill is loaded - no restart.
    """
    observations = {}

    procedure = (
        "# Frobnicate A Widget\n"
        "\n"
        "## When to use\n"
        "When the user asks to frobnicate a widget in this project.\n"
        "\n"
        "## Procedure\n"
        "1. Create a file named `widget.frob`.\n"
        "2. Write the single line `FROB-OK-7731` into it.\n"
        "3. Verify by reading the file back and checking the marker.\n"
    )

    def policy(payload, index):
        offered = tool_names_offered(payload)
        system = payload["messages"][0]["content"]

        if index == 0:
            # The harness must advertise the skill tools.
            observations["has_create_skill"] = "create_skill" in offered
            observations["skill_absent_initially"] = "frobnicate-a-widget" not in system
            return {
                "json": openai_message(
                    "", [openai_tool_call("s1", "list_skills", {})]
                )
            }
        if index == 1:
            observations["list_output"] = last_user_visible(payload)
            return {
                "json": openai_message(
                    "",
                    [
                        openai_tool_call(
                            "s2",
                            "create_skill",
                            {
                                "name": "frobnicate-a-widget",
                                "description": "How to frobnicate a widget in this project",
                                "content": procedure,
                            },
                        )
                    ],
                )
            }
        if index == 2:
            observations["create_output"] = last_user_visible(payload)
            # THE critical assertion: the new skill is in this request's
            # system prompt, in the same process, with no restart.
            observations["skill_in_next_prompt"] = "frobnicate-a-widget" in system
            return {
                "json": openai_message(
                    "",
                    [
                        openai_tool_call(
                            "s3", "read_skill", {"name": "frobnicate-a-widget"}
                        )
                    ],
                )
            }
        if index == 3:
            body = last_user_visible(payload)
            observations["read_back"] = body
            # Follow the procedure the skill actually returned.
            marker = re.search(r"`(FROB-OK-\d+)`", body)
            assert marker, f"skill body did not come back: {body[:300]}"
            observations["marker"] = marker.group(1)
            return {
                "json": openai_message(
                    "",
                    [
                        openai_tool_call(
                            "s4",
                            "write_file",
                            {"path": "widget.frob", "content": marker.group(1) + "\n"},
                        )
                    ],
                )
            }
        if index == 4:
            return {
                "json": openai_message(
                    "", [openai_tool_call("s5", "read_file", {"path": "widget.frob"})]
                )
            }
        return {
            "json": openai_message(
                "I learned how to frobnicate a widget, saved the skill, and did it."
            )
        }

    api.policy = policy
    result = run_lema(api, workspace, "frobnicate a widget")

    assert result.returncode == 0, result.stderr + result.stdout

    # 1. The capability gap was real at the start.
    assert observations["has_create_skill"] is True
    assert observations["skill_absent_initially"] is True

    # 2. The skill was created and reported as immediately usable.
    assert "frobnicate-a-widget" in observations["create_output"]

    # 3. It became visible to the very next model call, same process.
    assert observations["skill_in_next_prompt"] is True, (
        "a skill created mid-session was NOT offered to the next request"
    )

    # 4. The skill's own text drove the work.
    assert observations["marker"] == "FROB-OK-7731"
    assert (workspace / "widget.frob").read_text().strip() == "FROB-OK-7731"

    # 5. It was retained on disk for future sessions.
    skill_file = (
        Path(os.environ["LEMA_HOME"])
        / "config"
        / "skills"
        / "frobnicate-a-widget"
        / "SKILL.md"
    )
    project_file = workspace / ".lema" / "skills" / "frobnicate-a-widget" / "SKILL.md"
    assert skill_file.exists() or project_file.exists(), "the skill was not persisted"

    # 6. And a brand-new process can see it.
    listing = subprocess.run(
        [str(LEMA), "skills"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1"},
        timeout=90,
    )
    assert "frobnicate-a-widget" in listing.stdout


def test_end_to_end_readonly_mode_blocks_writes(api, workspace):
    def policy(payload, index):
        if index == 0:
            return {
                "json": openai_message(
                    "",
                    [
                        openai_tool_call(
                            "c1", "write_file", {"path": "should-not-exist.txt", "content": "x"}
                        )
                    ],
                )
            }
        return {"json": openai_message("I cannot write in read-only mode.")}

    api.policy = policy
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [
            str(LEMA),
            "--provider", "openai",
            "--base-url", api.url + "/v1",
            "--api-key", "k",
            "--model", "test-model",
            "--permissions", "readonly",
            "--no-stream",
            "--print",
            "write a file",
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not (workspace / "should-not-exist.txt").exists()


def test_end_to_end_iteration_limit_is_respected(api, workspace):
    """A looping model stops at the configured limit instead of running forever."""
    api.reply_json(
        openai_message("", [openai_tool_call("c", "list_directory", {"path": "."})])
    )
    result = run_lema(
        api, workspace, "loop please", "--set", "agent.max_iterations=3"
    )
    assert result.returncode != 0 or "iteration limit" in result.stdout.lower()
    # Exactly the configured number of model calls were made.
    assert len(api.requests) == 3


def test_end_to_end_session_log_is_written_and_redacted(api, workspace):
    api.reply_json(openai_message("done"))
    result = run_lema(api, workspace, "say done")
    assert result.returncode == 0, result.stderr

    sessions_dir = Path(os.environ["LEMA_HOME"]) / "state" / "sessions"
    logs = sorted(sessions_dir.glob("*.jsonl"))
    assert logs, "no session log written"
    body = logs[-1].read_text()
    assert "say done" in body
    # The API key passed on the command line must never be logged verbatim.
    assert "test-key" not in body
    for line in body.splitlines():
        json.loads(line)  # every line is valid JSON


def test_end_to_end_project_awareness(api, workspace):
    (workspace / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (workspace / "Makefile").write_text("test:\n\tpytest\n")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)

    captured = {}

    def policy(payload, index):
        captured["system"] = payload["messages"][0]["content"]
        return {"json": openai_message("I see a Python project.")}

    api.policy = policy
    result = run_lema(api, workspace, "what kind of project is this?")
    assert result.returncode == 0, result.stderr

    system = captured["system"]
    assert "pyproject.toml" in system
    assert "Makefile" in system
    assert "branch" in system.lower()
