"""Heuristic failure classification for shell/process output.

The goal is not perfect diagnosis - it is giving the model a strong prior so
that it attempts the *right kind* of recovery instead of blindly retrying.
"""

from __future__ import annotations

import re

from lema.tools.base import FailureKind

#: (pattern, kind, hint). Ordered: first match wins, so put specific first.
_RULES: list[tuple[re.Pattern[str], FailureKind, str]] = [
    (
        re.compile(r"(?i)\b(permission denied|operation not permitted|eacces|eperm)\b"),
        FailureKind.PERMISSION,
        "The OS refused the operation. Check ownership/mode, the path, or whether elevated privileges are required.",
    ),
    (
        re.compile(r"(?i)\bsudo:? .*(password|no tty|not allowed)"),
        FailureKind.PERMISSION,
        "sudo needs an interactive password; avoid sudo or ask the user to run it.",
    ),
    (
        re.compile(r"(?i)\b(command not found|no such file or directory: .*bin|is not recognized as)"),
        FailureKind.DEPENDENCY,
        "The executable is not installed or not on PATH. Install it or use an alternative.",
    ),
    (
        re.compile(
            r"(?i)(modulenotfounderror|importerror: cannot import|cannot find module|"
            r"unresolved import|package .* is not installed|could not find a version that satisfies)"
        ),
        FailureKind.DEPENDENCY,
        "A dependency is missing. Install it (pip/npm/cargo/apt) and retry.",
    ),
    (
        re.compile(r"(?i)\b(npm err!.*(eresolve|peer dep)|version solving failed|conflicting dependencies)"),
        FailureKind.DEPENDENCY,
        "Dependency resolution conflict. Adjust version constraints or use a resolution flag.",
    ),
    (
        re.compile(
            r"(?i)(syntaxerror|indentationerror|parse error|unexpected token|"
            r"expected .* found|error\[E\d+\]|cannot parse|invalid syntax)"
        ),
        FailureKind.SYNTAX,
        "The code does not parse/compile. Read the reported file and line, then fix the source.",
    ),
    (
        re.compile(r"(?i)\b(\d+ (failed|failures)|test.*fail|assertionerror|FAILED |FAIL\b)"),
        FailureKind.TEST_FAILURE,
        "Tests executed but failed. Read the failure output and fix the underlying cause.",
    ),
    (
        re.compile(r"(?i)\b(address already in use|eaddrinuse|port .* (is )?in use)"),
        FailureKind.CONFIGURATION,
        "The port is occupied. Pick another port or stop the process holding it (see list_processes / ss -ltnp).",
    ),
    (
        re.compile(
            r"(?i)(no such file or directory|enoent|not found|does not exist|"
            r"cannot stat|unable to open)"
        ),
        FailureKind.NOT_FOUND,
        "A path or target is missing. Verify it with list_directory or find_files.",
    ),
    (
        re.compile(
            r"(?i)(connection refused|econnrefused|could not resolve host|"
            r"network is unreachable|temporary failure in name resolution|dns)"
        ),
        FailureKind.TEMPORARY,
        "Network problem. The service may be down or offline access may be restricted; retry or work offline.",
    ),
    (
        re.compile(r"(?i)\b(timed? ?out|etimedout|deadline exceeded)\b"),
        FailureKind.TIMEOUT,
        "The operation exceeded its time budget. Increase the timeout or run it as a background process.",
    ),
    (
        re.compile(
            r"(?i)(invalid (option|flag|argument)|unrecognized (option|arguments)|"
            r"unknown (option|flag|command)|usage:)"
        ),
        FailureKind.CONFIGURATION,
        "The command was invoked incorrectly. Check its --help output.",
    ),
    (
        re.compile(r"(?i)(configuration (error|is invalid)|failed to parse config|invalid config)"),
        FailureKind.CONFIGURATION,
        "A configuration file is invalid. Read and correct it.",
    ),
    (
        re.compile(r"(?i)(disk quota exceeded|no space left on device)"),
        FailureKind.CONFIGURATION,
        "The filesystem is full. Free space before retrying.",
    ),
    (
        re.compile(r"(?i)(killed|out of memory|oom|cannot allocate memory)"),
        FailureKind.TEMPORARY,
        "The process was killed, likely OOM. Reduce parallelism or memory usage.",
    ),
    (
        re.compile(r"(?i)(merge conflict|conflict.*\.(py|js|ts|rs|go|java|c|cpp|md))"),
        FailureKind.CONFIGURATION,
        "A VCS conflict needs resolving before continuing.",
    ),
]


def classify_output(
    text: str, exit_code: int | None = None, *, timed_out: bool = False
) -> tuple[FailureKind, str | None]:
    """Classify a failure from command output.

    Returns ``(kind, hint)``.  ``FailureKind.NONE`` means "looks like success".
    """
    if timed_out:
        return (
            FailureKind.TIMEOUT,
            "The command exceeded its timeout. Either raise `timeout` or start it "
            "with start_process if it is a long-running server.",
        )
    if exit_code == 0:
        return FailureKind.NONE, None
    if exit_code is not None and exit_code < 0:
        return (
            FailureKind.INTERRUPTED,
            f"The process was terminated by signal {-exit_code}.",
        )

    haystack = text or ""
    for pattern, kind, hint in _RULES:
        if pattern.search(haystack):
            return kind, hint

    if exit_code == 127:
        return (
            FailureKind.DEPENDENCY,
            "Exit 127 means 'command not found'. Install the tool or correct the name.",
        )
    if exit_code == 126:
        return (
            FailureKind.PERMISSION,
            "Exit 126 means the file is not executable. Check the permission bits.",
        )
    if exit_code == 130:
        return FailureKind.INTERRUPTED, "Interrupted (SIGINT)."
    if exit_code in (1, 2) and not haystack.strip():
        return (
            FailureKind.UNKNOWN,
            "The command failed with no output. Re-run with a verbose flag to learn more.",
        )
    return (
        FailureKind.UNKNOWN,
        "Read the output above carefully, form a hypothesis, then verify it before changing code.",
    )
