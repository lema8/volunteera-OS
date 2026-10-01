from __future__ import annotations

import html
import ipaddress
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .config import ROOT
from .schemas import AssetDownload, now_iso
from .storage import safe_filename, unique_destination


class AssetError(RuntimeError):
    pass


def _plain(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _meta(metadata: dict[str, Any], key: str) -> str:
    item = metadata.get(key, {})
    return _plain(item.get("value", "") if isinstance(item, dict) else str(item))


def search_wikimedia(query: str, media_type: str = "image", limit: int = 18) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    search = query.strip()
    if media_type == "audio":
        search += " filetype:audio"
    elif media_type == "video":
        search += " filetype:video"
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": search,
        "gsrnamespace": 6, "gsrlimit": min(limit, 30), "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 640, "origin": "*",
    }
    try:
        response = httpx.get("https://commons.wikimedia.org/w/api.php", params=params, timeout=30)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
    except (httpx.HTTPError, ValueError) as exc:
        raise AssetError(f"Wikimedia Commons search failed: {exc}") from exc
    results = []
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = info.get("mime", "")
        if media_type == "image" and not mime.startswith("image/"):
            continue
        if media_type == "audio" and not mime.startswith("audio/"):
            continue
        if media_type == "video" and not mime.startswith("video/"):
            continue
        metadata = info.get("extmetadata", {})
        license_name = _meta(metadata, "LicenseShortName") or _meta(metadata, "UsageTerms")
        license_url = _meta(metadata, "LicenseUrl")
        if not license_name:
            # Only surface search results that describe reuse terms.
            continue
        results.append({
            "title": page.get("title", "").removeprefix("File:"),
            "download_url": info.get("url"),
            "preview_url": info.get("thumburl") or info.get("url"),
            "source_url": info.get("descriptionurl") or f"https://commons.wikimedia.org/?curid={page.get('pageid')}",
            "source_name": "Wikimedia Commons",
            "license": license_name,
            "license_url": license_url,
            "artist": _meta(metadata, "Artist"),
            "attribution": _meta(metadata, "Credit") or _meta(metadata, "Attribution"),
            "mime": mime,
            "width": info.get("width"), "height": info.get("height"), "size": info.get("size"),
        })
    return results


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise AssetError("Only public HTTPS asset URLs are accepted")
    if parsed.username or parsed.password:
        raise AssetError("Credentials in asset URLs are not allowed")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AssetError("Asset host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise AssetError("Private, loopback, reserved, and link-local asset hosts are blocked")


def download_asset(project_path: Path, request: AssetDownload) -> dict[str, Any]:
    max_bytes = int(float(__import__("os").getenv("ASSET_DOWNLOAD_MAX_MB", "500")) * 1024 * 1024)
    url = request.url
    category_dir = {
        "music": "music", "images": "images", "sound_effects": "sound_effects",
        "video": "assets", "other": "downloaded",
    }[request.category]
    destination = unique_destination(project_path / category_dir, request.filename)
    total = 0
    client = httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False)
    try:
        for _ in range(5):
            _validate_public_url(url)
            # Stream instead of buffering untrusted remote content into memory.
            with client.stream("GET", url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise AssetError("Asset redirect did not include a destination")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                declared = int(response.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise AssetError(f"Asset exceeds the {max_bytes // 1024 // 1024} MB download limit")
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise AssetError(f"Asset exceeds the {max_bytes // 1024 // 1024} MB download limit")
                        output.write(chunk)
                break
        else:
            raise AssetError("Too many redirects while downloading asset")
    except (httpx.HTTPError, AssetError) as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, AssetError):
            raise
        raise AssetError(f"Asset download failed: {exc}") from exc
    finally:
        client.close()
    asset = {
        "id": str(uuid.uuid4()), "filename": destination.name, "category": request.category,
        "file": str(destination.relative_to(project_path)), "size": destination.stat().st_size,
        "source_url": request.source_url, "download_url": request.url,
        "source_name": request.source_name, "license": request.license,
        "download_date": now_iso(), "usage_notes": request.usage_notes,
        "used_in": [], "user_supplied": False,
    }
    return asset
