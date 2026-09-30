"""User-defined tool plugins.

Drop a Python file into ``~/.config/lema/tools/`` or ``./.lema/tools/`` and it
becomes a tool - no changes to Lema's source, which is the point of §24's
"self-extension without core modification".

A plugin exposes tools in any of three ways:

1. ``TOOLS = [MyTool(), ...]``
2. ``def register(registry): registry.register(MyTool())``
3. Any module-level function decorated with :func:`tool`.

Example plugin::

    from lema.tools.plugins import tool

    @tool(description="Count lines in a file", parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    })
    async def count_lines(ctx, path: str):
        return str(len(open(path).read().splitlines()))
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, get_type_hints, Iterable, Sequence

from lema.tools.base import FunctionTool, Tool, ToolCategory
from lema.tools.registry import ToolRegistry

#: Marker attribute set by the @tool decorator.
_TOOL_MARKER = "__lema_tool__"


def tool(
    name: str | None = None,
    *,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    category: str | ToolCategory = ToolCategory.OTHER,
    mutating: bool = False,
    dangerous: bool = False,
    read_only: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that marks a function as a Lema tool."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            func,
            _TOOL_MARKER,
            {
                "name": name or func.__name__,
                "description": description or (func.__doc__ or "").strip(),
                "parameters": parameters or _infer_parameters(func),
                "category": ToolCategory(category) if not isinstance(category, ToolCategory) else category,
                "mutating": mutating,
                "dangerous": dangerous,
                "read_only": read_only and not mutating,
            },
        )
        return func

    return decorator


def _infer_parameters(func: Callable[..., Any]) -> dict[str, Any]:
    """Build a JSON Schema from the function signature's type hints."""
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    #: `from __future__ import annotations` turns hints into strings, so the
    #: bare spellings have to be understood too.
    by_name = {
        "str": "string", "int": "integer", "float": "number", "bool": "boolean",
        "list": "array", "dict": "object",
    }
    properties: dict[str, Any] = {}
    required: list[str] = []
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover
        return {"type": "object", "properties": {}}
    try:
        hints = get_type_hints(func)
    except Exception:  # noqa: BLE001 - unresolvable forward refs
        hints = {}
    for param_name, param in signature.parameters.items():
        if param_name in {"ctx", "self"}:
            continue
        annotation = hints.get(param_name, param.annotation)
        if isinstance(annotation, str):
            json_type = by_name.get(annotation.replace(" ", "").split("|")[0], "string")
        else:
            json_type = mapping.get(annotation, "string")
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        else:
            properties[param_name]["default"] = param.default
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclass
class PluginLoadResult:
    loaded: list[str]
    errors: list[str]


def _load_module(path: Path) -> Any:
    module_name = f"lema_plugin_{path.stem}_{abs(hash(str(path))) % 10_000_000}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _tools_from_module(module: Any) -> list[Tool]:
    found: list[Tool] = []

    declared = getattr(module, "TOOLS", None)
    if isinstance(declared, Iterable):
        for item in declared:
            if isinstance(item, Tool):
                found.append(item)

    for _, obj in inspect.getmembers(module):
        meta = getattr(obj, _TOOL_MARKER, None)
        if meta and callable(obj):
            found.append(
                FunctionTool(
                    name=meta["name"],
                    description=meta["description"],
                    parameters=meta["parameters"],
                    handler=obj,
                    category=meta["category"],
                    mutating=meta["mutating"],
                    dangerous=meta["dangerous"],
                    read_only=meta["read_only"],
                )
            )
        elif inspect.isclass(obj) and issubclass(obj, Tool) and obj.__module__ == module.__name__:
            if obj is Tool or obj is FunctionTool or not getattr(obj, "name", ""):
                continue
            try:
                found.append(obj())
            except Exception:  # noqa: BLE001 - a broken plugin must not break startup
                continue

    return found


def load_plugins(
    registry: ToolRegistry, directories: Sequence[Path], *, replace: bool = True
) -> PluginLoadResult:
    """Load every ``*.py`` plugin from the given directories."""
    loaded: list[str] = []
    errors: list[str] = []

    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module = _load_module(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
                if __debug__ and "LEMA_DEBUG" in __import__("os").environ:
                    errors.append(traceback.format_exc())
                continue

            register_fn = getattr(module, "register", None)
            if callable(register_fn):
                try:
                    register_fn(registry)
                    loaded.append(f"{path.name}:register()")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{path}:register(): {type(exc).__name__}: {exc}")

            for found in _tools_from_module(module):
                try:
                    registry.register(found, replace=replace)
                    loaded.append(f"{path.name}:{found.name}")
                except ValueError as exc:
                    errors.append(f"{path}: {exc}")

    return PluginLoadResult(loaded=loaded, errors=errors)
