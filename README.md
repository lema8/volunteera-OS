# Lema Harness

A terminal-first, general-purpose AI coding-agent runtime for Linux.

Lema is not a wrapper around another agent. It is its own runtime: a model
provider abstraction, a generic tool registry, an agent loop with failure
recovery, context compaction, a permission model, and a skill system the agent
can extend **while it is running**.

```
╭────────────────────────────────────────────────────────────╮
│                        LEMA HARNESS                        │
│                  ollama · qwen2.5-coder:7b                 │
│          32 tools · 6 skills · unrestricted mode           │
╰────────────────────────────────────────────────────────────╯
```

## The defining feature: skills the agent writes for itself

When Lema hits a task it does not know how to do, it does not just improvise and
forget. It writes a skill, loads it **in the same session with no restart**,
uses it to finish the job, and keeps it for next time.

```
› cut a 1.2.0 release

I don't have a release skill yet. Let me check.
● list_skills
  └─ ok  6 skill(s)
No release skill exists. I'll write one.
● create_skill  release-checklist
  └─ ok  release-checklist v1
  ◆ skill 'release-checklist' loaded and available now      ← no restart
Skill saved. Now following it.
● write_file  VERSION
● write_file  CHANGELOG.md
● run_command  cat VERSION CHANGELOG.md
  └─ ok  exit 0 in 0.0s

Cut release 1.2.0. I also saved a release-checklist skill so next time
I can do this directly.

  5 iterations · 5 tool calls · 0.3s · 90 tokens
  skills: release-checklist
```

The skill is a plain Markdown file you can read, edit and commit:

```
.lema/skills/release-checklist/SKILL.md
```

## Install

Requires Python 3.11+ on Linux.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then:

```bash
lema                      # interactive
lema "fix the failing test in tests/test_auth.py"
lema --print "what does this project do?"   # non-interactive, clean stdout
```

## Quick start with a local model

Lema treats local models as first-class and defaults to Ollama.

```bash
ollama serve
ollama pull qwen2.5-coder:7b

lema init      # write ~/.config/lema/config.toml
lema doctor    # check tools, skills, and that the model endpoint answers
lema
```

Small models that cannot emit structured tool calls are still supported: set
`tool_mode = "text"` (or leave it on `auto`) and Lema switches to a fenced
`tool_call` protocol it parses itself.

## Providers

| kind | endpoint | notes |
|------|----------|-------|
| `ollama` | `http://localhost:11434` | default; native or text tool calls |
| `openai` | `https://api.openai.com/v1` | also any OpenAI-compatible server |
| `anthropic` | `https://api.anthropic.com` | content blocks, tool_use |
| `openrouter`, `groq`, `together`, `deepseek`, `mistral`, `llamacpp`, `lmstudio`, `vllm` | preset base URLs | all via the OpenAI adapter |

Define profiles in `~/.config/lema/providers/*.toml` and switch with
`lema --profile work` or `/model work` inside a session.

## Permissions

```bash
lema --permissions unrestricted   # or --yolo
lema --permissions ask            # approve per call / per tool / per category / per session
lema --permissions readonly       # no writes, no shell, no process spawn
```

`unrestricted` means exactly that: Lema adds **no** application-level
restrictions on top of your OS account. There is no command blocklist and no
path jail. The single exception is Lema's own runtime source, which requires
`--developer-mode` to modify — skills and config are always writable.

## Tools

32 built-in tools, all registered through one generic interface (name,
description, JSON-Schema input, handler, structured result, typed failure):

- **filesystem** — `read_file` `write_file` `edit_file` `delete_file` `list_directory` `create_directory` `move_file` `copy_file`
- **search** — `search_text` (ripgrep with smart-case) `find_files` `search_code`
- **shell** — `run_command` (exit code, stdout, stderr, duration, cwd) `which`
- **processes** — `start_process` `stop_process` `list_processes` `get_process_output` for servers and other long-running jobs
- **git** — `git_status` `git_diff` `git_log` `git_branch` `git_show` `git_commit` (never automatic)
- **skills** — `list_skills` `read_skill` `create_skill` `update_skill` `delete_skill` `reload_skills`
- **web** — `web_search` `fetch_url` behind a replaceable provider
- **agent** — `task` delegates to real subagents with their own context windows

Add your own in `~/.config/lema/tools/*.py` or `./.lema/tools/*.py`:

```python
from lema.tools.plugins import tool
from lema.tools.base import ToolResult

@tool(description="Count widgets in the current project.")
async def count_widgets(ctx, path: str = "."):
    return ToolResult.success(str(len(list(ctx.resolve_path(path).glob("*.widget")))))
```

MCP servers plug into the same registry via `[tools.mcp_servers]`.

## Slash commands

`/help` `/model` `/skills` `/tools` `/context` `/status` `/diff` `/clear`
`/compact` `/config` `/permissions` `/cd` `/processes` `/log` `/exit`

All handled client-side — they never reach the model.

## Configuration

Layered, lowest to highest:

1. built-in defaults
2. `~/.config/lema/config.toml` and `~/.config/lema/providers/*.toml`
3. `./.lema/config.toml` (project)
4. `LEMA_*` environment variables
5. CLI flags and `--set section.key=value`

```bash
lema --set agent.max_iterations=250 --set provider.temperature=0.1
```

Skills live in `~/.config/lema/skills/` (global) and `./.lema/skills/`
(project, higher precedence). Logs are JSONL under `~/.local/state/lema/` with
secrets redacted; `--debug` adds a verbose file log. Stdout stays clean.

## Writing a skill by hand

```markdown
---
name: deploy-staging
description: Deploy this service to the staging cluster
keywords: deploy, staging, kubernetes
---

# Deploy To Staging

## When to use
When the user asks to deploy, ship or push to staging.

## Procedure
1. Run `make build` and confirm it exits 0.
2. `kubectl --context staging apply -f k8s/`
3. Verify with `kubectl --context staging rollout status deploy/api`.

## Pitfalls
- Never deploy with uncommitted changes; check `git status` first.
```

Drop it at `.lema/skills/deploy-staging/SKILL.md`. Lema indexes every skill but
only injects the full text of the ones relevant to the current task, so a large
library does not flood the context window.

Updating a skill keeps a version history under `.versions/`; a skill is never
silently destroyed.

## Architecture

```
lema/
  providers/   ModelProvider abstraction: openai_compat, anthropic, ollama, text_tools
  tools/       registry, base contract, filesystem, search, shell, processes, git,
               skills, web, plugins, mcp
  skills/      SKILL.md model, scoped registry, pluggable retrieval
  agent/       loop, context, compaction, permissions, prompts, events, subagents
  cli/         renderer, slash commands, REPL
  config/      typed schema + layered loader
  storage/     session logs, history
```

The agent loop never writes to the terminal — it emits events. The CLI renderer
consumes them, and so could a web UI or a test.

## Tests

```bash
.venv/bin/python -m pytest
```

289 tests. Tools run for real against real files, real subprocesses and real git
repositories; providers are tested against a real HTTP server speaking the
OpenAI, Anthropic and Ollama wire formats; and `tests/test_end_to_end.py` drives
the installed `lema` binary as a subprocess.

`tests/test_dynamic_skills.py` and
`test_end_to_end_dynamic_skill_creation` are the acceptance tests for the
defining feature: they assert that a skill created mid-session appears in the
*next* model request, that its stored text is what drives the work, and that a
fresh process still sees it.
