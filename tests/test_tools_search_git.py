"""Search and git tools, executed for real against a real repository."""

from __future__ import annotations

import subprocess

import pytest

from lema.tools.base import FailureKind


def git(workspace, *args):
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(workspace),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


@pytest.fixture
def repo(workspace):
    git(workspace, "init", "-q", "-b", "main")
    git(workspace, "config", "user.email", "test@example.com")
    git(workspace, "config", "user.name", "Test")
    (workspace / "app.py").write_text("def main():\n    print('hello')\n")
    (workspace / "README.md").write_text("# Demo\n")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "initial commit")
    return workspace


@pytest.fixture
def sample_tree(workspace):
    (workspace / "src").mkdir()
    (workspace / "src" / "auth.py").write_text(
        "import os\n\n\ndef login(user, password):\n"
        "    # TODO: rate limit this\n"
        "    return check_password(user, password)\n"
    )
    (workspace / "src" / "db.py").write_text(
        "def connect(url):\n    return url\n\n\ndef login_audit(user):\n    pass\n"
    )
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_auth.py").write_text(
        "def test_login():\n    assert login('a', 'b')\n"
    )
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "junk.py").write_text("def login(): pass\n")
    (workspace / "notes.txt").write_text("nothing relevant here\n")
    return workspace


# -------------------------------------------------------------- search_text


@pytest.mark.asyncio
async def test_search_text_finds_matches_with_locations(tools, tool_context, sample_tree):
    result = await tools.dispatch("search_text", {"pattern": "def login"}, tool_context)
    assert result.ok
    assert "src/auth.py" in result.output
    assert ":4:" in result.output or "4:" in result.output


@pytest.mark.asyncio
async def test_search_text_glob_filter(tools, tool_context, sample_tree):
    result = await tools.dispatch(
        "search_text", {"pattern": "login", "glob": "tests/**"}, tool_context
    )
    assert result.ok
    assert "tests/test_auth.py" in result.output
    assert "src/auth.py" not in result.output


@pytest.mark.asyncio
async def test_search_text_skips_vendor_directories(tools, tool_context, sample_tree):
    result = await tools.dispatch("search_text", {"pattern": "def login"}, tool_context)
    assert "node_modules" not in result.output


@pytest.mark.asyncio
async def test_search_text_no_match_is_success_not_failure(tools, tool_context, sample_tree):
    result = await tools.dispatch("search_text", {"pattern": "zzz-nope"}, tool_context)
    assert result.ok
    assert "no matches" in result.output.lower()


@pytest.mark.asyncio
async def test_search_text_uses_smart_case(tools, tool_context, sample_tree):
    """Lowercase patterns match any case; an uppercase pattern is literal."""
    lower = await tools.dispatch("search_text", {"pattern": "login"}, tool_context)
    assert "auth.py" in lower.output

    upper = await tools.dispatch("search_text", {"pattern": "LOGIN"}, tool_context)
    assert "no matches" in upper.output.lower()

    forced = await tools.dispatch(
        "search_text", {"pattern": "LOGIN", "case_sensitive": False}, tool_context
    )
    assert "auth.py" in forced.output


@pytest.mark.asyncio
async def test_search_text_invalid_regex_is_actionable(tools, tool_context, sample_tree):
    result = await tools.dispatch("search_text", {"pattern": "("}, tool_context)
    assert not result.ok
    assert result.failure_kind is FailureKind.INVALID_INPUT
    assert result.hint


@pytest.mark.asyncio
async def test_search_text_respects_a_result_limit(tools, tool_context, workspace):
    big = workspace / "big.txt"
    big.write_text("\n".join(f"match {i}" for i in range(500)))
    result = await tools.dispatch(
        "search_text", {"pattern": "match", "max_results": 10}, tool_context
    )
    assert result.ok
    assert len([ln for ln in result.output.splitlines() if "match" in ln]) <= 12


# --------------------------------------------------------------- find_files


@pytest.mark.asyncio
async def test_find_files_by_glob(tools, tool_context, sample_tree):
    result = await tools.dispatch("find_files", {"pattern": "**/*.py"}, tool_context)
    assert result.ok
    assert "src/auth.py" in result.output
    assert "tests/test_auth.py" in result.output
    assert "node_modules" not in result.output


@pytest.mark.asyncio
async def test_find_files_bare_name_pattern(tools, tool_context, sample_tree):
    result = await tools.dispatch("find_files", {"pattern": "*.txt"}, tool_context)
    assert result.ok
    assert "notes.txt" in result.output


@pytest.mark.asyncio
async def test_find_files_reports_nothing_found(tools, tool_context, sample_tree):
    result = await tools.dispatch("find_files", {"pattern": "**/*.rs"}, tool_context)
    assert result.ok
    assert "no files" in result.output.lower()


# -------------------------------------------------------------- search_code


@pytest.mark.asyncio
async def test_search_code_finds_a_definition(tools, tool_context, sample_tree):
    result = await tools.dispatch(
        "search_code", {"symbol": "login", "language": "python"}, tool_context
    )
    assert result.ok
    assert "auth.py" in result.output


@pytest.mark.asyncio
async def test_search_code_without_language_still_works(tools, tool_context, sample_tree):
    result = await tools.dispatch("search_code", {"symbol": "connect"}, tool_context)
    assert result.ok
    assert "db.py" in result.output


# ---------------------------------------------------------------------- git


@pytest.mark.asyncio
async def test_git_status_on_a_clean_repo(tools, tool_context, repo):
    result = await tools.dispatch("git_status", {}, tool_context)
    assert result.ok
    assert "main" in result.output
    assert "clean" in result.output.lower()


@pytest.mark.asyncio
async def test_git_status_shows_changes(tools, tool_context, repo):
    (repo / "app.py").write_text("def main():\n    print('changed')\n")
    (repo / "new.py").write_text("x = 1\n")
    result = await tools.dispatch("git_status", {}, tool_context)
    assert result.ok
    assert "app.py" in result.output
    assert "new.py" in result.output


@pytest.mark.asyncio
async def test_git_diff_shows_a_unified_patch(tools, tool_context, repo):
    (repo / "app.py").write_text("def main():\n    print('changed')\n")
    result = await tools.dispatch("git_diff", {}, tool_context)
    assert result.ok
    assert "-    print('hello')" in result.output
    assert "+    print('changed')" in result.output


@pytest.mark.asyncio
async def test_git_diff_for_one_path(tools, tool_context, repo):
    (repo / "app.py").write_text("changed\n")
    (repo / "README.md").write_text("# Changed\n")
    result = await tools.dispatch("git_diff", {"path": "README.md"}, tool_context)
    assert result.ok
    assert "README.md" in result.output
    assert "app.py" not in result.output


@pytest.mark.asyncio
async def test_git_diff_staged(tools, tool_context, repo):
    (repo / "app.py").write_text("staged change\n")
    git(repo, "add", "app.py")
    unstaged = await tools.dispatch("git_diff", {}, tool_context)
    assert "staged change" not in unstaged.output
    staged = await tools.dispatch("git_diff", {"staged": True}, tool_context)
    assert "staged change" in staged.output


@pytest.mark.asyncio
async def test_git_log(tools, tool_context, repo):
    result = await tools.dispatch("git_log", {"limit": 5}, tool_context)
    assert result.ok
    assert "initial commit" in result.output


@pytest.mark.asyncio
async def test_git_branch_lists_and_reports_current(tools, tool_context, repo):
    result = await tools.dispatch("git_branch", {}, tool_context)
    assert result.ok
    assert "main" in result.output


@pytest.mark.asyncio
async def test_git_commit_requires_staged_changes(tools, tool_context, repo):
    result = await tools.dispatch("git_commit", {"message": "nothing to do"}, tool_context)
    assert not result.ok
    assert "nothing staged" in (result.error or "").lower() or "nothing to commit" in (
        result.error or ""
    ).lower()


@pytest.mark.asyncio
async def test_git_commit_commits_staged_work(tools, tool_context, repo):
    (repo / "feature.py").write_text("print('feature')\n")
    result = await tools.dispatch(
        "git_commit", {"message": "add feature", "add_all": True}, tool_context
    )
    assert result.ok, result.error
    log = git(repo, "log", "--oneline").stdout
    assert "add feature" in log


@pytest.mark.asyncio
async def test_git_show(tools, tool_context, repo):
    result = await tools.dispatch("git_show", {"ref": "HEAD"}, tool_context)
    assert result.ok
    assert "initial commit" in result.output


@pytest.mark.asyncio
async def test_git_status_outside_a_repo_states_the_fact(tools, tool_context, tmp_path):
    """Not being a repo is information, not a failure the agent must recover from."""
    tool_context.cwd = tmp_path
    tool_context.project_root = tmp_path
    result = await tools.dispatch("git_status", {}, tool_context)
    assert result.ok
    assert "not a git repository" in result.output.lower()


@pytest.mark.asyncio
async def test_git_mutation_outside_a_repo_fails(tools, tool_context, tmp_path):
    tool_context.cwd = tmp_path
    tool_context.project_root = tmp_path
    result = await tools.dispatch("git_commit", {"message": "x"}, tool_context)
    assert not result.ok
    assert "git" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_agent_never_commits_without_being_asked(tools, tool_context, repo):
    """Spec §26: committing is an explicit tool call, never a side effect."""
    (repo / "app.py").write_text("def main():\n    print('edited by agent')\n")
    await tools.dispatch(
        "edit_file",
        {"path": "README.md", "old_string": "# Demo", "new_string": "# Demo App"},
        tool_context,
    )
    await tools.dispatch("write_file", {"path": "extra.py", "content": "x=1\n"}, tool_context)
    # Nothing was committed by those tools.
    log = git(repo, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 1
    status = git(repo, "status", "--porcelain").stdout
    assert "app.py" in status and "extra.py" in status
