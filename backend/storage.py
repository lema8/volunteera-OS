from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .config import PROJECTS_DIR, ensure_directories
from .schemas import ProjectCreate, now_iso

PROJECT_SUBDIRS = [
    "input", "analysis", "assets", "music", "images", "sound_effects",
    "downloaded", "versions", "final", "trash",
]


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return value[:60] or "project"


def safe_filename(value: str) -> str:
    name = Path(value).name
    stem = re.sub(r"[^a-zA-Z0-9._ -]+", "_", Path(name).stem).strip(" .")[:120] or "file"
    suffix = re.sub(r"[^a-zA-Z0-9.]", "", Path(name).suffix.lower())[:10]
    return f"{stem}{suffix}"


def project_dir(project_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", project_id):
        raise HTTPException(400, "Invalid project ID")
    path = (PROJECTS_DIR / project_id).resolve()
    if PROJECTS_DIR.resolve() not in path.parents:
        raise HTTPException(400, "Invalid project path")
    return path


def require_project(project_id: str) -> tuple[Path, dict[str, Any]]:
    path = project_dir(project_id)
    metadata = path / "project.json"
    if not metadata.exists():
        raise HTTPException(404, "Project not found")
    try:
        return path, json.loads(metadata.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, "Project metadata is damaged") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    temporary.replace(path)


def save_project(path: Path, project: dict[str, Any]) -> None:
    project["updated_at"] = now_iso()
    write_json(path / "project.json", project)


def create_project(request: ProjectCreate) -> dict[str, Any]:
    ensure_directories()
    base = slugify(request.name)
    project_id = base
    n = 2
    while (PROJECTS_DIR / project_id).exists():
        project_id = f"{base}-{n}"
        n += 1
    path = PROJECTS_DIR / project_id
    for subdir in PROJECT_SUBDIRS:
        (path / subdir).mkdir(parents=True, exist_ok=True)
    project = {
        "id": project_id,
        "name": request.name.strip(),
        "description": request.description,
        "instructions": request.instructions,
        "style": request.style,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "clips": [],
        "assets": [],
        "versions": [],
        "final_version": None,
        "workflow": "upload",
    }
    write_json(path / "project.json", project)
    return project


def list_projects() -> list[dict[str, Any]]:
    ensure_directories()
    result = []
    for metadata in PROJECTS_DIR.glob("*/project.json"):
        try:
            project = json.loads(metadata.read_text("utf-8"))
            project["clip_count"] = len(project.get("clips", []))
            project["version_count"] = len(project.get("versions", []))
            result.append(project)
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(result, key=lambda item: item.get("updated_at", ""), reverse=True)


def file_signature(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def unique_destination(directory: Path, filename: str) -> Path:
    filename = safe_filename(filename)
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def find_clip(project: dict[str, Any], clip_id: str) -> dict[str, Any]:
    clip = next((item for item in project.get("clips", []) if item.get("id") == clip_id), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    return clip


def find_asset(project: dict[str, Any], asset_id: str) -> dict[str, Any]:
    asset = next((item for item in project.get("assets", []) if item.get("id") == asset_id), None)
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


def resolve_project_file(path: Path, relative: str) -> Path:
    resolved = (path / relative).resolve()
    if path.resolve() not in resolved.parents:
        raise HTTPException(400, "Unsafe file path")
    return resolved


def next_version_id(project: dict[str, Any], path: Path | None = None) -> str:
    """Return an unused immutable version ID.

    Project metadata only contains completed versions. A failed/interrupted render can
    still leave a version directory behind, so disk state must also participate in ID
    allocation. Existing directories are preserved and the next number is selected.
    """
    values: list[int] = []
    for version in project.get("versions", []):
        match = re.fullmatch(r"v(\d+)", version.get("id", ""))
        if match:
            values.append(int(match.group(1)))
    if path is not None:
        versions_dir = path / "versions"
        if versions_dir.exists():
            for candidate in versions_dir.iterdir():
                if not candidate.is_dir():
                    continue
                match = re.fullmatch(r"v(\d+)", candidate.name)
                if match:
                    values.append(int(match.group(1)))
    return f"v{(max(values, default=0) + 1):03d}"
