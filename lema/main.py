"""Command-line entry point.

    lema                          interactive session
    lema "fix the failing test"   one-shot task, then exit
    lema --print "explain this"   one-shot, plain output, scriptable
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from lema import __version__
from lema.config.loader import ConfigError, load_config, write_default_global_config
from lema.config.schema import Config, PermissionMode
from lema.providers import ProviderError, available_providers
from lema.storage.logs import setup_logging
from lema.util import paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lema",
        description="Lema Harness - an extensible AI coding-agent runtime.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  lema                                    start an interactive session
  lema "add tests for the parser"         run one task and exit
  lema --permissions unrestricted         no approval prompts
  lema --provider openai --model gpt-4o   override the model for this run
  lema --print "summarise this repo"      plain output for scripts/pipes
  lema init                               write the default config file
  lema skills                             list available skills
  lema doctor                             check the environment and provider
""",
    )
    parser.add_argument("task", nargs="*", help="Task to run non-interactively.")

    model_group = parser.add_argument_group("model")
    model_group.add_argument("--provider", help="Provider adapter kind (openai/anthropic/ollama).")
    model_group.add_argument(
        "--profile",
        help="Named provider profile from ~/.config/lema/providers/<name>.toml.",
    )
    model_group.add_argument("--model", "-m", help="Model id.")
    model_group.add_argument("--base-url", help="API base URL.")
    model_group.add_argument("--api-key", help="API key (prefer an env var).")
    model_group.add_argument("--temperature", type=float, help="Sampling temperature.")
    model_group.add_argument("--context-window", type=int, help="Context window in tokens.")
    model_group.add_argument(
        "--tool-mode",
        choices=["auto", "native", "text"],
        help="How tool calls are transported (auto probes the model).",
    )

    agent_group = parser.add_argument_group("agent")
    agent_group.add_argument(
        "--permissions",
        "-p",
        choices=[m.value for m in PermissionMode],
        help="Permission mode (default: ask).",
    )
    agent_group.add_argument(
        "--yolo",
        action="store_true",
        help="Shorthand for --permissions unrestricted.",
    )
    agent_group.add_argument(
        "--max-iterations", type=int, help="Maximum agent<->model round trips per turn."
    )
    agent_group.add_argument(
        "--developer-mode",
        action="store_true",
        help="Allow the agent to modify Lema's own runtime source.",
    )
    agent_group.add_argument(
        "--no-subagents", action="store_true", help="Disable the task/subagent tool."
    )

    io_group = parser.add_argument_group("input/output")
    io_group.add_argument(
        "--print",
        "-P",
        dest="print_mode",
        action="store_true",
        help="One-shot mode with plain output (implies non-interactive).",
    )
    io_group.add_argument("--cwd", "-C", help="Working directory for the session.")
    io_group.add_argument("--no-banner", action="store_true", help="Hide the startup banner.")
    io_group.add_argument("--no-stream", action="store_true", help="Disable token streaming.")
    io_group.add_argument("--debug", action="store_true", help="Verbose debug logging.")
    io_group.add_argument("--no-color", action="store_true", help="Disable coloured output.")
    parser.add_argument(
        "--set",
        "-s",
        dest="set_options",
        action="append",
        metavar="SECTION.KEY=VALUE",
        help="Override any config setting, e.g. -s agent.max_iterations=200.",
    )

    config_group = parser.add_argument_group("configuration")
    config_group.add_argument("--config", help="Path to an additional config file.")
    config_group.add_argument(
        "--no-global-config", action="store_true", help="Ignore ~/.config/lema/config.toml."
    )
    config_group.add_argument(
        "--no-project-config", action="store_true", help="Ignore ./.lema/config.toml."
    )
    config_group.add_argument(
        "--no-skills", action="store_true", help="Do not load built-in skills."
    )
    config_group.add_argument(
        "--no-plugins", action="store_true", help="Do not load user tool plugins."
    )
    config_group.add_argument(
        "--no-mcp", action="store_true", help="Do not connect configured MCP servers."
    )
    config_group.add_argument("--version", "-V", action="version", version=f"lema {__version__}")

    return parser


def overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.provider:
        overrides["provider.kind"] = args.provider
    if args.model:
        overrides["provider.model"] = args.model
    if args.base_url:
        overrides["provider.base_url"] = args.base_url
    if args.api_key:
        overrides["provider.api_key"] = args.api_key
    if args.temperature is not None:
        overrides["provider.temperature"] = args.temperature
    if args.context_window:
        overrides["provider.context_window"] = args.context_window
    if args.tool_mode:
        overrides["provider.tool_mode"] = args.tool_mode
    if args.max_iterations:
        overrides["agent.max_iterations"] = args.max_iterations
    if args.developer_mode:
        overrides["agent.developer_mode"] = True
    if args.no_subagents:
        overrides["agent.subagents_enabled"] = False
    if args.yolo:
        overrides["permissions"] = PermissionMode.UNRESTRICTED.value
    elif args.permissions:
        overrides["permissions"] = args.permissions
    if args.no_banner or args.print_mode:
        overrides["ui.banner"] = False
    if args.no_color:
        overrides["ui.color"] = False
    if getattr(args, "profile", None):
        overrides["provider.name"] = args.profile
    for item in getattr(args, "set_options", None) or []:
        key, sep, value = str(item).partition("=")
        if not sep:
            raise ConfigError(
                f"invalid --set {item!r}: expected section.key=value "
                "(for example agent.max_iterations=200)"
            )
        overrides[key.strip()] = value.strip()
    if args.no_stream or args.print_mode:
        overrides["ui.stream_output"] = False
    if args.print_mode:
        overrides["ui.show_timings"] = False
    if args.debug:
        overrides["debug"] = True
        overrides["logging.level"] = "debug"
    if args.no_skills:
        overrides["skills.load_builtin"] = False
    if args.no_plugins:
        overrides["tools.load_plugins"] = False
    return overrides


def resolve_provider_profile(config: Config, requested: str | None) -> None:
    """``--provider`` may name a profile or an adapter kind; support both."""
    if not requested:
        return
    if requested in config.providers:
        config.provider = config.providers[requested]
    elif requested in available_providers():
        config.provider.kind = requested
        config.provider.name = requested
    # Unknown values fall through and fail later with a clear provider error.


# --------------------------------------------------------------- subcommands


def cmd_init(args: argparse.Namespace) -> int:
    console = Console()
    path = write_default_global_config(force=False)
    console.print(f"[green]✓[/green] config: {path}")
    console.print(f"  skills:   {paths.global_skills_dir()}")
    console.print(f"  plugins:  {paths.global_tools_dir()}")
    console.print(f"  state:    {paths.state_dir()}")
    console.print()
    console.print("Edit the config to point at your model, then run [cyan]lema[/cyan].")
    return 0


async def cmd_doctor(config: Config) -> int:
    from lema.providers import create_provider
    from lema.skills.registry import build_registry
    from lema.tools import build_default_registry

    console = Console()
    console.print("[bold]Lema Harness doctor[/bold]\n")
    ok = True

    console.print(f"  version        {__version__}")
    console.print(f"  python         {sys.version.split()[0]}")
    console.print(f"  platform       {sys.platform}")
    console.print(f"  workspace      {config.workspace}")
    console.print(f"  project root   {config.project_root}")
    console.print(f"  config sources {', '.join(config.sources)}")
    console.print()

    registry = build_default_registry(
        include_web=config.tools.web_enabled,
        disabled=config.tools.disabled,
        enabled=config.tools.enabled,
    )
    console.print(f"  [green]✓[/green] tools          {len(registry)} registered")

    skills = build_registry(
        config.project_root,
        include_builtin=config.skills.load_builtin,
        extra_dirs=config.skills.extra_dirs,
    )
    console.print(f"  [green]✓[/green] skills         {len(skills)} loaded")
    for error in skills.load_errors:
        ok = False
        console.print(f"    [red]✗[/red] {error}")

    import shutil as _shutil

    for binary in ("git", "rg", "bash"):
        path = _shutil.which(binary)
        marker = "[green]✓[/green]" if path else "[yellow]○[/yellow]"
        note = path or "not found (optional)" if binary != "bash" else path or "not found"
        console.print(f"  {marker} {binary:<14} {note}")

    console.print()
    console.print(
        f"  provider       {config.provider.kind} · {config.provider.model}\n"
        f"  base url       {config.provider.effective_base_url()}"
    )
    provider = create_provider(config.provider)
    try:
        health = await provider.health()
        if health.ok:
            console.print(f"  [green]✓[/green] connection     reachable ({len(health.models)} models)")
            if health.models and config.provider.model not in health.models:
                console.print(
                    f"    [yellow]![/yellow] model '{config.provider.model}' is not in the list"
                )
                sample = ", ".join(health.models[:6])
                console.print(f"      available: {sample}")
        else:
            ok = False
            console.print(f"  [red]✗[/red] connection     {health.detail}")
    except ProviderError as exc:
        ok = False
        console.print(f"  [red]✗[/red] connection     {exc}")
    finally:
        await provider.aclose()

    console.print()
    console.print("[green]All good.[/green]" if ok else "[yellow]Some checks failed.[/yellow]")
    return 0 if ok else 1


def cmd_skills_list(config: Config) -> int:
    from lema.skills.registry import build_registry

    console = Console()
    registry = build_registry(
        config.project_root,
        include_builtin=config.skills.load_builtin,
        extra_dirs=config.skills.extra_dirs,
    )
    if not registry.all():
        console.print("No skills found.")
    for skill in registry.all():
        console.print(
            f"  [magenta]{skill.name:<28}[/magenta] "
            f"[grey50]{skill.scope.value:<8} v{skill.version}[/grey50]  {skill.description[:60]}"
        )
    console.print()
    for source in registry.sources:
        console.print(f"  [grey50]{source.scope.value:<8} {source.path}[/grey50]")
    for error in registry.load_errors:
        console.print(f"  [red]{error}[/red]")
    return 0


def cmd_tools_list(config: Config) -> int:
    from lema.tools import build_default_registry

    console = Console()
    registry = build_default_registry(
        include_web=config.tools.web_enabled,
        disabled=config.tools.disabled,
        enabled=config.tools.enabled,
    )
    console.print(registry.describe())
    return 0


# ---------------------------------------------------------------- run modes


async def run_interactive(config: Config, initial_task: str | None) -> int:
    from lema.cli.renderer import Renderer
    from lema.cli.repl import Repl
    from lema.session import build_session

    console = Console(
        highlight=False,
        no_color=not config.ui.color or bool(os.environ.get("NO_COLOR")),
    )
    renderer_holder: dict[str, Renderer] = {}

    async def on_event(event: Any) -> None:
        renderer = renderer_holder.get("r")
        if renderer is not None:
            await renderer.handle(event)

    repl_holder: dict[str, Repl] = {}

    async def approval(request: Any) -> Any:
        repl = repl_holder.get("r")
        if repl is None:  # pragma: no cover
            from lema.agent.permissions import ApprovalScope

            return ApprovalScope.DENY
        return await repl.approval_callback(request)

    session = await build_session(config, on_event=on_event, approval_callback=approval)
    repl = Repl(session, console)
    repl_holder["r"] = repl
    renderer_holder["r"] = repl.renderer

    try:
        return await repl.run(initial_task)
    finally:
        await session.aclose()


async def run_one_shot(config: Config, task: str, *, plain: bool) -> int:
    """Non-interactive execution, suitable for scripts and CI."""
    from lema.cli.renderer import Renderer
    from lema.session import build_session

    console = Console(highlight=False)
    renderer = Renderer(config, console)

    async def on_event(event: Any) -> None:
        if plain:
            # In --print mode only surface progress on stderr so stdout stays
            # clean for piping.
            from lema.agent.events import EventType

            if event.type is EventType.TOOL_START:
                print(f"● {event.tool_name} {renderer._summarize(event.tool_name, event.arguments)}",
                      file=sys.stderr)
            elif event.type is EventType.ERROR:
                print(f"✗ {event.text}", file=sys.stderr)
            elif event.type is EventType.SKILL_EVENT:
                print(f"◆ {event.text}", file=sys.stderr)
            return
        await renderer.handle(event)

    session = await build_session(config, on_event=on_event, connect_mcp=True)

    if not plain:
        renderer.banner(
            model=config.provider.model,
            provider=config.provider.kind,
            cwd=str(config.workspace),
            skills=len(session.skills),
            tools=len(session.tools),
        )
        for warning in session.startup_warnings:
            renderer.warning(warning)

    try:
        result = await session.agent.run(task)
    finally:
        await session.aclose()

    if plain:
        print(result.content)
    return 0 if result.ok else 1


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Bare subcommands, handled before the main parser so they do not collide
    # with free-form task text.
    subcommand = argv[0] if argv else None
    if subcommand in ("init", "doctor", "skills", "tools", "config"):
        argv = argv[1:]
    else:
        subcommand = None

    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(level="debug" if args.debug else "info", debug=args.debug)

    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    if os.environ.get("NO_COLOR"):
        args.no_color = True

    workspace = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    if not workspace.is_dir():
        print(f"lema: not a directory: {workspace}", file=sys.stderr)
        return 2

    try:
        config = load_config(
            workspace=workspace,
            overrides=overrides_from_args(args),
            use_global=not args.no_global_config,
            use_project=not args.no_project_config,
        )
    except ConfigError as exc:
        print(f"lema: configuration error: {exc}", file=sys.stderr)
        return 2

    resolve_provider_profile(config, args.provider)

    if args.config:
        from lema.config.loader import _merge_file

        extra_path = Path(args.config).expanduser()
        if not extra_path.is_file():
            print(f"lema: config file not found: {extra_path}", file=sys.stderr)
            return 2
        try:
            _merge_file(config, extra_path)
        except ConfigError as exc:
            print(f"lema: {exc}", file=sys.stderr)
            return 2
        # CLI flags still win over an explicitly supplied file.
        from lema.config.loader import _apply_overrides

        _apply_overrides(config, overrides_from_args(args))

    # Subcommands.
    if subcommand == "init":
        return cmd_init(args)
    if subcommand == "doctor":
        return asyncio.run(cmd_doctor(config))
    if subcommand == "skills":
        return cmd_skills_list(config)
    if subcommand == "tools":
        return cmd_tools_list(config)
    if subcommand == "config":
        import json

        from lema.util.redact import redact_value

        print(json.dumps(redact_value(config.to_dict()), indent=2, default=str))
        return 0

    task = " ".join(args.task).strip()

    # Accept a task piped on stdin: `echo "fix the build" | lema --print`
    if not task and not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            task = piped

    try:
        if task and (args.print_mode or not sys.stdin.isatty()):
            return asyncio.run(run_one_shot(config, task, plain=args.print_mode))
        if not sys.stdin.isatty() and not task:
            print("lema: no task provided on stdin", file=sys.stderr)
            return 2
        return asyncio.run(run_interactive(config, task or None))
    except ProviderError as exc:
        print(f"lema: provider error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
