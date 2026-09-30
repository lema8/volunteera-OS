"""Web research tools.

Research matters most for skill creation: when the agent meets an unfamiliar
framework it should be able to read the documentation, understand the
procedure, and write it down as a skill.

The search backend is a replaceable provider, not a hard-coded engine.
Register another one with :func:`register_search_backend`.
"""

from __future__ import annotations

import html as html_module
import json
import re
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolError,
    ToolResult,
)
from lema.util.tokens import truncate_middle

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) lema-harness/0.1 (+https://github.com/lema8/volunteera-OS)"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchBackend(ABC):
    """Pluggable web-search provider."""

    name = "base"

    @abstractmethod
    async def search(self, query: str, *, limit: int = 8, timeout: float = 20.0) -> list[SearchResult]:
        ...


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo: no API key required, which keeps the default zero-config."""

    name = "duckduckgo"
    ENDPOINT = "https://html.duckduckgo.com/html/"

    async def search(self, query: str, *, limit: int = 8, timeout: float = 20.0) -> list[SearchResult]:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = await client.post(self.ENDPOINT, data={"q": query})
            response.raise_for_status()
            return self._parse(response.text, limit)

    @staticmethod
    def _parse(markup: str, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|\Z)',
            markup,
            re.DOTALL,
        )
        for href, title_html, tail in blocks:
            url = _unwrap_ddg(href)
            title = _strip_tags(title_html)
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>', tail, re.DOTALL
            )
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            if not url or not title:
                continue
            results.append(SearchResult(title=title, url=url, snippet=snippet))
            if len(results) >= limit:
                break
        return results


class SearxBackend(SearchBackend):
    """Self-hosted SearxNG instance (set tools.web_search_url)."""

    name = "searx"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, *, limit: int = 8, timeout: float = 20.0) -> list[SearchResult]:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json"},
            )
            response.raise_for_status()
            data = response.json()
        out = []
        for item in (data.get("results") or [])[:limit]:
            out.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                )
            )
        return out


_BACKENDS: dict[str, Any] = {
    "duckduckgo": DuckDuckGoBackend,
    "ddg": DuckDuckGoBackend,
    "searx": SearxBackend,
    "searxng": SearxBackend,
}


def register_search_backend(name: str, factory: Any) -> None:
    _BACKENDS[name.lower()] = factory


def get_search_backend(ctx: ToolContext) -> SearchBackend:
    name = (ctx.config.tools.web_provider or "duckduckgo").lower()
    factory = _BACKENDS.get(name)
    if factory is None:
        raise ToolError(
            f"unknown web search provider {name!r}",
            FailureKind.CONFIGURATION,
            hint=f"Known providers: {', '.join(sorted(_BACKENDS))}",
        )
    if factory is SearxBackend:
        base = ctx.config.tools.web_search_url
        if not base:
            raise ToolError(
                "the searx provider needs tools.web_search_url set to your instance URL",
                FailureKind.CONFIGURATION,
            )
        return SearxBackend(base)
    return factory()


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; recover the real URL."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("uddg", [None])[0]
        if target:
            return urllib.parse.unquote(target)
    return href


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK_RE = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article|pre)[^>]*>", re.IGNORECASE)


def _strip_tags(markup: str) -> str:
    text = _TAG_RE.sub("", markup)
    return " ".join(html_module.unescape(text).split())


def html_to_text(markup: str) -> str:
    """Convert HTML into readable plain text without a parser dependency."""
    text = _SCRIPT_RE.sub(" ", markup)
    # Keep the document structure as newlines so code blocks stay readable.
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    out: list[str] = []
    blank = 0
    for line in lines:
        if not line:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(line)
    return "\n".join(out).strip()


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web and return titles, URLs and snippets. "
        "Use this to research unfamiliar tools, APIs or error messages before acting - "
        "especially before writing a new skill about something you do not know well. "
        "Follow up with fetch_url to read the most promising result."
    )
    category = ToolCategory.WEB
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "description": "Maximum results.", "default": 8},
        },
        "required": ["query"],
    }

    async def run(self, ctx: ToolContext, query: str, limit: int = 8) -> ToolResult:
        if not ctx.config.tools.web_enabled:
            raise ToolError(
                "web access is disabled (tools.web_enabled = false)",
                FailureKind.CONFIGURATION,
                hint="Enable it in the config, or work from local sources only.",
            )
        backend = get_search_backend(ctx)
        try:
            results = await backend.search(query, limit=max(1, min(int(limit), 20)))
        except httpx.HTTPError as exc:
            raise ToolError(
                f"web search failed: {exc}",
                FailureKind.TEMPORARY,
                hint="The network may be unavailable. Continue using local information.",
            ) from exc

        if not results:
            return ToolResult.success(
                f"No results for {query!r}.", data={"query": query, "count": 0}
            )

        lines = [f"{len(results)} result(s) for {query!r} (via {backend.name}):", ""]
        for index, item in enumerate(results, 1):
            lines.append(f"{index}. {item.title}")
            lines.append(f"   {item.url}")
            if item.snippet:
                lines.append(f"   {item.snippet[:300]}")
            lines.append("")
        return ToolResult.success(
            "\n".join(lines),
            data={
                "query": query,
                "count": len(results),
                "results": [r.__dict__ for r in results],
            },
        )


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Fetch a URL and return its content as plain text (HTML is stripped). "
        "Use this to read documentation, README files, API references or raw source."
    )
    category = ToolCategory.WEB
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to fetch."},
            "max_chars": {"type": "integer", "description": "Truncate the result to this size.", "default": 20000},
            "raw": {"type": "boolean", "description": "Return the body without HTML stripping.", "default": False},
        },
        "required": ["url"],
    }

    async def run(
        self, ctx: ToolContext, url: str, max_chars: int = 20000, raw: bool = False
    ) -> ToolResult:
        if not ctx.config.tools.web_enabled:
            raise ToolError(
                "web access is disabled (tools.web_enabled = false)",
                FailureKind.CONFIGURATION,
            )
        if not re.match(r"^https?://", url):
            raise ToolError(
                f"invalid URL: {url}",
                FailureKind.INVALID_INPUT,
                hint="The URL must start with http:// or https://",
            )
        try:
            async with httpx.AsyncClient(
                timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            ) as client:
                response = await client.get(url)
        except httpx.ConnectError as exc:
            raise ToolError(
                f"cannot reach {url}: {exc}",
                FailureKind.TEMPORARY,
                hint="Check the URL or network availability.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"fetch failed: {exc}", FailureKind.TEMPORARY) from exc

        if response.status_code >= 400:
            kind = (
                FailureKind.NOT_FOUND
                if response.status_code == 404
                else FailureKind.TEMPORARY
                if response.status_code >= 500
                else FailureKind.UNKNOWN
            )
            return ToolResult.failure(
                f"HTTP {response.status_code} for {url}",
                kind,
                output=response.text[:1000],
            )

        content_type = response.headers.get("content-type", "")
        body = response.text
        if not raw and "html" in content_type.lower():
            body = html_to_text(body)
        elif not raw and "json" in content_type.lower():
            try:
                body = json.dumps(response.json(), indent=2)[: max_chars * 2]
            except (json.JSONDecodeError, ValueError):
                pass

        return ToolResult.success(
            f"{url} ({content_type or 'unknown type'}, {len(response.content)} bytes)\n\n"
            + truncate_middle(body, int(max_chars), "page truncated"),
            data={
                "url": str(response.url),
                "status": response.status_code,
                "content_type": content_type,
                "chars": len(body),
            },
        )


def build_web_tools() -> list[Tool]:
    return [WebSearchTool(), FetchUrlTool()]
