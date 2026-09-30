"""Slash commands.

These are handled entirely by the CLI and never reach the model.  Each is a
small function registered in :data:`COMMANDS`, so adding one is a two-line
change.
"""

from __future__ import annotations

import inspect
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from rich.table import Table
from rich.text import Text

from lema.config.loader import save_global_config
from lema.config.schema import PermissionMode
from lema.providers import available_providers
from lema.skills.model import SkillScope
from lema.util import paths
from lema.util.redact import mask

if TYPE_CHECKING:  # pragma: no cover
    from lema.cli.repl import Repl


@dataclass
class CommandResult:
    handled: bool = True
    exit: bool = False
    #: When set, the REPL sends this to the agent instead of returning to prompt.
    forward_to_agent: str | None = None


Handler = Callable[["Repl", list[str]], Awaitable[CommandResult] | CommandResult]


@dataclass
class Command:
    name: str
    summary: str
    handler: Handler
    usage: str = ""
    aliases: tuple[str, ...] = ()


COMMANDS: dict[str, Command] = {}


def command(
    name: str, summary: str, usage: str = "", aliases: tuple[str, ...] = ()
) -> Callable[[Handler], Handler]:
    def decorator(func: Handler) -> Handler:
        COMMANDS[name] = Command(
            name=name, summary=summary, handler=func, usage=usage, aliases=aliases
        )
        return func

    return decorator


async def dispatch(repl: "Repl", line: str) -> CommandResult:
    """Run a slash command. Returns handled=False if it is not one."""
    if not line.startswith("/"):
        return CommandResult(handled=False)
    try:
        parts = shlex.split(line[1:])
    except ValueError:
        parts = line[1:].split()
    if not parts:
        return CommandResult(handled=False)

    name, args = parts[0].lower(), parts[1:]
    cmd = COMMANDS.get(name)
    if cmd is None:
        for candidate in COMMANDS.values():
            if name in candidate.aliases:
                cmd = candidate
                break
    if cmd is None:
        import difflib

        close = difflib.get_close_matches(name, list(COMMANDS), n=3, cutoff=0.5)
        repl.renderer.error(
            f"unknown command /{name}"
            + (f" - did you mean {', '.join('/' + c for c in close)}?" if close else "")
        )
        repl.renderer.info("Type /help to list commands.")
        return CommandResult(handled=True)

    result = cmd.handler(repl, args)
    if inspect.isawaitable(result):
        result = await result
    return result or CommandResult()


# ---------------------------------------------------------------- commands


@command("help", "Show available commands", aliases=("h", "?"))
def cmd_help(repl: "Repl", args: list[str]) -> CommandResult:
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("command", style="cyan", no_wrap=True)
    table.add_column("description")
    for name in sorted(COMMANDS):
        cmd = COMMANDS[name]
        label = f"/{name}" + (f" {cmd.usage}" if cmd.usage else "")
        table.add_row(label, cmd.summary)
    repl.renderer.console.print(table)
    repl.renderer.console.print()
    repl.renderer.info("Anything that is not a /command is sent to the agent.")
    repl.renderer.info("Ctrl-C interrupts the agent · Ctrl-D exits.")
    return CommandResult()


@command("exit", "Exit Lema", aliases=("quit", "q"))
def cmd_exit(repl: "Repl", args: list[str]) -> CommandResult:
    return CommandResult(exit=True)


@command("model", "Show or switch the active model/provider profile", usage="[profile|model-name]")
async def cmd_model(repl: "Repl", args: list[str]) -> CommandResult:
    session = repl.session
    config = session.config

    if not args:
        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("profile", style="cyan")
        table.add_column("kind")
        table.add_column("model")
        table.add_column("base url", style="grey50")
        table.add_column("key")
        # The active provider is always listed, even when no named profiles
        # are configured - otherwise /model renders an empty table.
        profiles = dict(config.providers)
        profiles.setdefault(config.provider.name, config.provider)
        for name, profile in sorted(profiles.items()):
            active = "→ " if profile is config.provider or name == config.provider.name else "  "
            table.add_row(
                active + name,
                profile.kind,
                profile.model,
                profile.effective_base_url(),
                mask(profile.resolve_api_key()),
            )
        repl.renderer.console.print(table)
        repl.renderer.console.print()
        repl.renderer.info(
            "Use /model <profile> to switch profile, or /model <name> to change the model id."
        )
        repl.renderer.info(f"Adapters available: {', '.join(available_providers())}")
        return CommandResult()

    target = args[0]
    if target in config.providers:
        session.switch_provider(target)
        repl.renderer.success(
            f"switched to profile '{target}' ({config.provider.kind}: {config.provider.model})"
        )
        return CommandResult()

    if target == "list":
        try:
            models = await session.provider.list_models()
        except Exception as exc:  # noqa: BLE001
            repl.renderer.error(f"could not list models: {exc}")
            return CommandResult()
        if not models:
            repl.renderer.info("the endpoint returned no model list")
        for model in models:
            repl.renderer.console.print(f"  {model}")
        return CommandResult()

    # Treat it as a model id on the current profile.
    config.provider.model = target
    session.provider.model = target
    session.context.invalidate_system_prompt()
    repl.renderer.success(f"model set to '{target}' (profile {config.provider.name})")
    return CommandResult()


@command("skills", "List, show or reload skills", usage="[reload|show <name>|new <name>]")
async def cmd_skills(repl: "Repl", args: list[str]) -> CommandResult:
    registry = repl.session.skills

    if args and args[0] == "reload":
        count = repl.session.reload_skills()
        repl.renderer.success(f"reloaded {count} skill(s)")
        for err in registry.load_errors:
            repl.renderer.warning(err)
        return CommandResult()

    if args and args[0] in ("show", "read", "cat") and len(args) > 1:
        skill = registry.get(args[1])
        if skill is None:
            repl.renderer.error(f"no skill named '{args[1]}'")
            suggestions = registry.suggest(args[1])
            if suggestions:
                repl.renderer.info("did you mean: " + ", ".join(suggestions))
            return CommandResult()
        repl.renderer.rule(f"{skill.name}  (v{skill.version}, {skill.scope.value})")
        repl.renderer.print_markdown(skill.content)
        if skill.path:
            repl.renderer.info(f"\n{skill.path}")
        return CommandResult()

    if args and args[0] == "new" and len(args) > 1:
        # Let the agent write it - that is the whole point of the skill system.
        return CommandResult(
            forward_to_agent=(
                f"Create a skill named '{args[1]}'. Research the topic if you are not "
                f"already confident about it, then call create_skill with a complete, "
                f"concrete procedure."
            )
        )

    skills = registry.all()
    if not skills:
        repl.renderer.info("No skills yet. The agent creates them as it learns procedures.")
        repl.renderer.info("You can also add Markdown files under .lema/skills/<name>/SKILL.md")
        return CommandResult()

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("skill", style="magenta")
    table.add_column("scope", style="grey50")
    table.add_column("v", justify="right", style="grey50")
    table.add_column("description")
    for skill in skills:
        table.add_row(skill.name, skill.scope.value, str(skill.version), skill.description[:70])
    repl.renderer.console.print(table)
    repl.renderer.console.print()
    for source in registry.sources:
        marker = "" if source.path.exists() else "  (missing)"
        repl.renderer.info(f"  {source.scope.value:<8} {source.path}{marker}")
    return CommandResult()


@command("tools", "List registered tools", usage="[category]")
def cmd_tools(repl: "Repl", args: list[str]) -> CommandResult:
    registry = repl.session.tools
    wanted = args[0].lower() if args else None

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("tool", style="cyan")
    table.add_column("category", style="grey50")
    table.add_column("flags", style="yellow")
    table.add_column("description")
    for category, tools in sorted(registry.by_category().items(), key=lambda kv: kv[0].value):
        if wanted and category.value != wanted:
            continue
        for tool in tools:
            flags = []
            if tool.mutating:
                flags.append("mut")
            if tool.dangerous:
                flags.append("danger")
            if not tool.parallel_safe:
                flags.append("serial")
            summary = (tool.description or "").strip().splitlines()[0][:66]
            table.add_row(tool.name, category.value, ",".join(flags), summary)
    repl.renderer.console.print(table)
    repl.renderer.console.print()
    repl.renderer.info(f"{len(registry)} tools registered.")
    return CommandResult()


@command("context", "Show context window usage", aliases=("ctx",))
def cmd_context(repl: "Repl", args: list[str]) -> CommandResult:
    stats = repl.session.context.stats()
    repl.renderer.console.print(stats.render())
    injected = repl.session.context.injected_skills
    if injected:
        repl.renderer.console.print(f"  injected skills: {', '.join(injected)}")
    touched = repl.session.tool_context.scratch.get("touched_files", {})
    if touched:
        repl.renderer.console.print(f"  files touched:   {len(touched)}")
    return CommandResult()


@command("status", "Show session status")
async def cmd_status(repl: "Repl", args: list[str]) -> CommandResult:
    session = repl.session
    config = session.config
    stats = session.context.stats()

    table = Table(show_header=False, box=None)
    table.add_column("k", style="grey50")
    table.add_column("v")
    table.add_row("provider", f"{config.provider.kind} ({config.provider.name})")
    table.add_row("model", config.provider.model)
    table.add_row("base url", config.provider.effective_base_url())
    table.add_row("api key", mask(config.provider.resolve_api_key()))
    table.add_row("permissions", config.permissions.value)
    table.add_row("developer mode", "on" if config.agent.developer_mode else "off")
    table.add_row("cwd", str(session.tool_context.cwd))
    table.add_row("project root", str(config.project_root))
    table.add_row("tools", str(len(session.tools)))
    table.add_row("skills", str(len(session.skills)))
    table.add_row("messages", str(len(session.context.messages)))
    table.add_row("context", f"{stats.estimated_tokens:,} / {stats.context_window:,} tokens ({stats.utilization*100:.0f}%)")
    table.add_row("compactions", str(session.context.compactions))
    table.add_row("session log", str(session.logger.path or "(disabled)"))
    processes = session.processes.all()
    if processes:
        table.add_row(
            "processes",
            ", ".join(f"{p.id}({p.status()})" for p in processes),
        )
    repl.renderer.console.print(table)

    health = await session.provider.health()
    if health.ok:
        repl.renderer.success(f"provider reachable ({len(health.models)} models listed)")
    else:
        repl.renderer.warning(f"provider unreachable: {health.detail}")
    return CommandResult()


@command("diff", "Show what changed in the working tree", usage="[--staged] [path]")
async def cmd_diff(repl: "Repl", args: list[str]) -> CommandResult:
    from lema.tools.git import git, is_git_repo

    cwd = repl.session.tool_context.cwd
    if not await is_git_repo(cwd, repl.session.config.tools.shell):
        touched = repl.session.tool_context.scratch.get("touched_files", {})
        if not touched:
            repl.renderer.info("not a git repository, and no files were touched this session")
            return CommandResult()
        repl.renderer.info("not a git repository; files touched this session:")
        for path, action in sorted(touched.items()):
            repl.renderer.console.print(f"  {action:<9} {path}")
        return CommandResult()

    git_args = ["diff", "--color=always"]
    if "--staged" in args or "--cached" in args:
        git_args.append("--cached")
        args = [a for a in args if a not in ("--staged", "--cached")]
    if args:
        git_args += ["--", *args]

    result = await git(git_args, cwd, shell=repl.session.config.tools.shell)
    body = result["stdout"].strip()
    if not body:
        repl.renderer.info("no changes")
        # Untracked files are invisible to `git diff`; list them explicitly.
        status = await git(
            ["ls-files", "--others", "--exclude-standard"],
            cwd,
            shell=repl.session.config.tools.shell,
        )
        untracked = [f for f in status["stdout"].splitlines() if f.strip()]
        if untracked:
            repl.renderer.info(f"{len(untracked)} untracked file(s):")
            for path in untracked[:40]:
                repl.renderer.console.print(f"  + {path}")
        return CommandResult()
    repl.renderer.console.print(body, markup=False, highlight=False)
    return CommandResult()


@command("clear", "Clear the conversation history", aliases=("reset",))
def cmd_clear(repl: "Repl", args: list[str]) -> CommandResult:
    repl.session.context.clear()
    repl.session.tool_context.scratch.clear()
    repl.renderer.console.clear()
    repl.renderer.success("conversation cleared")
    return CommandResult()


@command("compact", "Summarise the conversation to free context")
async def cmd_compact(repl: "Repl", args: list[str]) -> CommandResult:
    before = repl.session.context.stats()
    if not repl.session.context.messages:
        repl.renderer.info("nothing to compact")
        return CommandResult()
    repl.renderer.info("compacting...")
    result = await repl.session.agent._compact(force=True)
    if result is None or result.removed_messages == 0:
        repl.renderer.info("nothing could be safely compacted yet")
        return CommandResult()
    after = repl.session.context.stats()
    repl.renderer.success(result.render())
    repl.renderer.info(
        f"context: {before.utilization*100:.0f}% → {after.utilization*100:.0f}%"
    )
    return CommandResult()


@command(
    "config",
    "Show or change configuration",
    usage="[show|path|init|save|permissions <mode>|set <key> <value>]",
)
def cmd_config(repl: "Repl", args: list[str]) -> CommandResult:
    config = repl.session.config

    if not args or args[0] == "show":
        from lema.util.redact import redact_value
        import json

        repl.renderer.console.print_json(
            json.dumps(redact_value(config.to_dict()), default=str, indent=2)
        )
        return CommandResult()

    if args[0] == "path":
        repl.renderer.console.print(f"  global config  {paths.global_config_file()}")
        repl.renderer.console.print(f"  project config {paths.project_config_file(config.project_root)}")
        repl.renderer.console.print(f"  global skills  {paths.global_skills_dir()}")
        repl.renderer.console.print(f"  project skills {paths.project_skills_dir(config.project_root)}")
        repl.renderer.console.print(f"  tool plugins   {paths.global_tools_dir()}")
        repl.renderer.console.print(f"  state / logs   {paths.state_dir()}")
        repl.renderer.console.print(f"  loaded from    {', '.join(config.sources)}")
        return CommandResult()

    if args[0] == "init":
        from lema.config.loader import write_default_global_config

        path = write_default_global_config(force="--force" in args)
        repl.renderer.success(f"wrote {path}")
        return CommandResult()

    if args[0] == "save":
        path = save_global_config(config)
        repl.renderer.success(f"saved current configuration to {path}")
        return CommandResult()

    if args[0] in ("permissions", "perm", "mode"):
        if len(args) < 2:
            repl.renderer.console.print(repl.session.permissions.describe())
            repl.renderer.info("modes: " + ", ".join(m.value for m in PermissionMode))
            return CommandResult()
        try:
            mode = PermissionMode.parse(args[1])
        except ValueError as exc:
            repl.renderer.error(str(exc))
            return CommandResult()
        config.permissions = mode
        repl.session.permissions.set_mode(mode)
        repl.session.context.invalidate_system_prompt()
        repl.renderer.success(f"permission mode: {mode.value}")
        if mode is PermissionMode.UNRESTRICTED:
            repl.renderer.warning("the agent will now act without asking for approval")
        return CommandResult()

    if args[0] == "set" and len(args) >= 3:
        from lema.config.loader import _apply_overrides  # internal but purpose-built

        key, value = args[1], " ".join(args[2:])
        try:
            _apply_overrides(config, {key: value})
        except Exception as exc:  # noqa: BLE001
            repl.renderer.error(f"could not set {key}: {exc}")
            return CommandResult()
        repl.session.context.invalidate_system_prompt()
        repl.renderer.success(f"{key} = {value}")
        repl.renderer.info("use /config save to persist this globally")
        return CommandResult()

    repl.renderer.error(f"unknown /config subcommand: {args[0]}")
    return CommandResult()


@command("cd", "Change the working directory", usage="<path>")
def cmd_cd(repl: "Repl", args: list[str]) -> CommandResult:
    if not args:
        repl.renderer.console.print(str(repl.session.tool_context.cwd))
        return CommandResult()
    target = Path(args[0]).expanduser()
    if not target.is_absolute():
        target = repl.session.tool_context.cwd / target
    target = target.resolve()
    if not target.is_dir():
        repl.renderer.error(f"not a directory: {target}")
        return CommandResult()
    repl.session.tool_context.cwd = target
    repl.session.context.set_cwd(target)
    repl.renderer.success(f"cwd: {target}")
    return CommandResult()


@command("permissions", "Shortcut for /config permissions", usage="<mode>")
def cmd_permissions(repl: "Repl", args: list[str]) -> CommandResult:
    return cmd_config(repl, ["permissions", *args])


@command("processes", "List background processes", aliases=("ps",))
def cmd_processes(repl: "Repl", args: list[str]) -> CommandResult:
    processes = repl.session.processes.all()
    if not processes:
        repl.renderer.info("no background processes")
        return CommandResult()
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("id", style="cyan")
    table.add_column("status")
    table.add_column("uptime", justify="right")
    table.add_column("command", style="grey50")
    for process in processes:
        table.add_row(
            process.id, process.status(), f"{process.uptime:.0f}s", process.command[:60]
        )
    repl.renderer.console.print(table)
    return CommandResult()


@command("log", "Show the path of this session's log")
def cmd_log(repl: "Repl", args: list[str]) -> CommandResult:
    path = repl.session.logger.path
    if path is None:
        repl.renderer.info("session logging is disabled")
        return CommandResult()
    repl.renderer.console.print(str(path))
    if args and args[0] == "tail":
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-20:]
        except OSError as exc:
            repl.renderer.error(str(exc))
            return CommandResult()
        for line in lines:
            repl.renderer.console.print(line[:300], markup=False, highlight=False)
    return CommandResult()
