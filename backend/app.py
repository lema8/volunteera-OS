from __future__ import annotations

import importlib.util
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .analyzer import analyze_project
from .assets import AssetError, download_asset, search_wikimedia
from .config import LIBRARY_DIR, PROJECTS_DIR, ROOT, binary_path, ensure_directories, load_settings, save_settings
from .jobs import jobs
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, MediaError, extract_thumbnail, probe_media
from .renderer import render_project
from .schemas import (
    AssetDownload,
    ClipOrder,
    ClipRename,
    ProjectCreate,
    ProjectUpdate,
    RenderRequest,
    SettingsUpdate,
    VersionFeedback,
    now_iso,
)
from .storage import (
    create_project,
    find_asset,
    find_clip,
    list_projects,
    require_project,
    resolve_project_file,
    safe_filename,
    save_project,
    unique_destination,
    write_json,
)

ensure_directories()
app = FastAPI(
    title="AI Video Editor Agent",
    version="0.1.0",
    description="Local-first, human-approved AI video editing with real FFmpeg rendering.",
)


@app.exception_handler(MediaError)
async def media_error_handler(request: Request, exc: MediaError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(AssetError)
async def asset_error_handler(request: Request, exc: AssetError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _public_project(project: dict[str, Any]) -> dict[str, Any]:
    # No server filesystem paths or secrets are included.
    return project


def _load_library() -> list[dict[str, Any]]:
    manifest = LIBRARY_DIR / "library.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_library(items: list[dict[str, Any]]) -> None:
    write_json(LIBRARY_DIR / "library.json", items)


def _job_conflict(project_id: str, kinds: set[str]) -> None:
    active = [
        job for job in jobs.list_for_project(project_id)
        if job["status"] in {"queued", "running"} and job["kind"] in kinds
    ]
    if active:
        raise HTTPException(409, f"A {active[0]['kind']} job is already running for this project")


@app.get("/api/health")
def health() -> dict[str, Any]:
    usage = shutil.disk_usage(ROOT)
    return {
        "status": "ok",
        "app": "AI Video Editor Agent",
        "ffmpeg": bool(binary_path("ffmpeg")),
        "ffprobe": bool(binary_path("ffprobe")),
        "local_whisper": importlib.util.find_spec("faster_whisper") is not None,
        "ocr": importlib.util.find_spec("pytesseract") is not None,
        "free_disk_bytes": usage.free,
        "projects_dir": str(PROJECTS_DIR.relative_to(ROOT)) if ROOT in PROJECTS_DIR.parents else str(PROJECTS_DIR),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return load_settings().model_dump()


@app.put("/api/settings")
def update_settings(request: SettingsUpdate) -> dict[str, Any]:
    payload = request.model_dump(exclude={"api_key"})
    settings = save_settings(payload, request.api_key)
    return settings.model_dump()


@app.get("/api/projects")
def projects_list() -> list[dict[str, Any]]:
    return list_projects()


@app.post("/api/projects", status_code=201)
def project_create(request: ProjectCreate) -> dict[str, Any]:
    return create_project(request)


@app.get("/api/projects/{project_id}")
def project_get(project_id: str) -> dict[str, Any]:
    _, project = require_project(project_id)
    return _public_project(project)


@app.patch("/api/projects/{project_id}")
def project_update(project_id: str, request: ProjectUpdate) -> dict[str, Any]:
    path, project = require_project(project_id)
    changes = request.model_dump(exclude_none=True)
    for key, value in changes.items():
        project[key] = value.strip() if key == "name" else value
    save_project(path, project)
    return project


@app.post("/api/projects/{project_id}/clips", status_code=201)
async def clip_upload(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    path, project = require_project(project_id)
    filename = safe_filename(file.filename or "video.mp4")
    if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(415, f"Unsupported video extension. Supported: {', '.join(sorted(VIDEO_EXTENSIONS))}")
    max_bytes = int(float(os.getenv("MAX_UPLOAD_GB", "20")) * 1024 * 1024 * 1024)
    destination = unique_destination(path / "input", filename)
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"Upload exceeds the {os.getenv('MAX_UPLOAD_GB', '20')} GB limit")
                output.write(chunk)
        metadata = probe_media(destination)
        clip_id = str(uuid.uuid4())
        thumbnail = path / "analysis" / clip_id / "thumbnail.jpg"
        extract_thumbnail(destination, thumbnail, metadata["duration"])
    except Exception:
        # A failed upload was never accepted as a user asset. Quarantine bytes for inspection instead of silently deleting.
        if destination.exists():
            quarantine = unique_destination(path / "trash", f"invalid-{destination.name}")
            destination.replace(quarantine)
        raise
    clip = {
        "id": clip_id,
        "name": destination.stem,
        "original_filename": file.filename,
        "file": str(destination.relative_to(path)),
        "thumbnail": str(thumbnail.relative_to(path)),
        "size": total,
        "order": len(project.get("clips", [])),
        "metadata": metadata,
        "analysis_status": "pending",
        "uploaded_at": now_iso(),
    }
    project.setdefault("clips", []).append(clip)
    project["workflow"] = "upload"
    save_project(path, project)
    return clip


@app.patch("/api/projects/{project_id}/clips/{clip_id}")
def clip_rename(project_id: str, clip_id: str, request: ClipRename) -> dict[str, Any]:
    path, project = require_project(project_id)
    clip = find_clip(project, clip_id)
    clip["name"] = request.name.strip()
    save_project(path, project)
    return clip


@app.put("/api/projects/{project_id}/clips/order")
def clip_reorder(project_id: str, request: ClipOrder) -> dict[str, Any]:
    path, project = require_project(project_id)
    existing = [clip["id"] for clip in project.get("clips", [])]
    if len(request.clip_ids) != len(set(request.clip_ids)):
        raise HTTPException(400, "Clip order contains duplicate IDs")
    if set(existing) != set(request.clip_ids):
        raise HTTPException(400, "Clip order must contain every clip exactly once")
    by_id = {clip["id"]: clip for clip in project["clips"]}
    project["clips"] = [by_id[clip_id] for clip_id in request.clip_ids]
    for index, clip in enumerate(project["clips"]):
        clip["order"] = index
    save_project(path, project)
    return {"clips": project["clips"], "message": "Locked narrative order saved"}


@app.delete("/api/projects/{project_id}/clips/{clip_id}")
def clip_remove(project_id: str, clip_id: str) -> dict[str, Any]:
    path, project = require_project(project_id)
    clip = find_clip(project, clip_id)
    source = resolve_project_file(path, clip["file"])
    moved_to = None
    if source.exists():
        trash = unique_destination(path / "trash", f"removed-{source.name}")
        source.replace(trash)
        moved_to = str(trash.relative_to(path))
    project["clips"] = [item for item in project["clips"] if item["id"] != clip_id]
    for index, item in enumerate(project["clips"]):
        item["order"] = index
    project.setdefault("removed_items", []).append({**clip, "removed_at": now_iso(), "trash_file": moved_to})
    save_project(path, project)
    return {"removed": clip_id, "preserved_in_trash": moved_to}


@app.get("/api/projects/{project_id}/clips/{clip_id}/media")
def clip_media(project_id: str, clip_id: str):
    path, project = require_project(project_id)
    clip = find_clip(project, clip_id)
    media = resolve_project_file(path, clip["file"])
    if not media.exists():
        raise HTTPException(404, "Clip file is missing")
    return FileResponse(media)


@app.get("/api/projects/{project_id}/clips/{clip_id}/thumbnail")
def clip_thumbnail(project_id: str, clip_id: str):
    path, project = require_project(project_id)
    clip = find_clip(project, clip_id)
    thumbnail = resolve_project_file(path, clip["thumbnail"])
    if not thumbnail.exists():
        raise HTTPException(404, "Thumbnail is missing")
    return FileResponse(thumbnail, media_type="image/jpeg")


@app.post("/api/projects/{project_id}/analyze", status_code=202)
def project_analyze(project_id: str, force: bool = False) -> dict[str, Any]:
    _job_conflict(project_id, {"analysis", "render"})
    path, project = require_project(project_id)
    if not project.get("clips"):
        raise HTTPException(400, "Upload at least one clip first")
    job = jobs.create("analysis", project_id, lambda progress: analyze_project(path, project, progress, force=force))
    return job.public()


@app.post("/api/projects/{project_id}/render", status_code=202)
def project_render(project_id: str, request: RenderRequest) -> dict[str, Any]:
    _job_conflict(project_id, {"analysis", "render"})
    _, project = require_project(project_id)
    if not project.get("clips"):
        raise HTTPException(400, "Upload at least one clip first")
    incomplete = [clip["name"] for clip in project["clips"] if clip.get("analysis_status") != "complete"]
    if incomplete:
        raise HTTPException(409, f"Analyze all clips before rendering. Pending: {', '.join(incomplete)}")
    job = jobs.create("render", project_id, lambda progress: render_project(project_id, request, progress))
    return job.public()


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found (jobs are kept only for this server session)")
    return job.public()


@app.get("/api/projects/{project_id}/jobs")
def project_jobs(project_id: str) -> list[dict[str, Any]]:
    require_project(project_id)
    return jobs.list_for_project(project_id)


def _find_version(project: dict[str, Any], version_id: str) -> dict[str, Any]:
    version = next((item for item in project.get("versions", []) if item.get("id") == version_id), None)
    if not version:
        raise HTTPException(404, "Version not found")
    return version


@app.get("/api/projects/{project_id}/versions/{version_id}/media")
def version_media(project_id: str, version_id: str, download: bool = False):
    path, project = require_project(project_id)
    version = _find_version(project, version_id)
    media = resolve_project_file(path, version["video_file"])
    if not media.exists():
        raise HTTPException(404, "Rendered file is missing")
    return FileResponse(media, media_type="video/mp4", filename=f"{project_id}-{version_id}.mp4" if download else None)


@app.get("/api/projects/{project_id}/versions/{version_id}/plan")
def version_plan(project_id: str, version_id: str):
    path, project = require_project(project_id)
    version = _find_version(project, version_id)
    plan = resolve_project_file(path, version["plan_file"])
    return json.loads(plan.read_text("utf-8"))


@app.put("/api/projects/{project_id}/versions/{version_id}/feedback")
def version_feedback(project_id: str, version_id: str, request: VersionFeedback) -> dict[str, Any]:
    path, project = require_project(project_id)
    version = _find_version(project, version_id)
    version["human_rating"] = request.rating
    version["user_feedback"] = request.feedback
    version["feedback_updated_at"] = now_iso()
    version_file = path / "versions" / version_id / "version.json"
    write_json(version_file, version)
    save_project(path, project)
    return version


@app.post("/api/projects/{project_id}/versions/{version_id}/accept")
def version_accept(project_id: str, version_id: str) -> dict[str, Any]:
    path, project = require_project(project_id)
    version = _find_version(project, version_id)
    source = resolve_project_file(path, version["video_file"])
    final = path / "final" / f"{version_id}.mp4"
    if not final.exists():
        shutil.copy2(source, final)
    for item in project.get("versions", []):
        if item.get("status") == "accepted":
            item["status"] = "superseded"
    version["status"] = "accepted"
    version["accepted_at"] = now_iso()
    project["final_version"] = version_id
    project["final_file"] = str(final.relative_to(path))
    project["workflow"] = "complete"
    write_json(path / "versions" / version_id / "version.json", version)
    save_project(path, project)
    return {"final_version": version_id, "final_file": project["final_file"], "message": "Human-approved final saved"}


@app.post("/api/projects/{project_id}/versions/{version_id}/reject")
def version_reject(project_id: str, version_id: str, request: VersionFeedback) -> dict[str, Any]:
    path, project = require_project(project_id)
    version = _find_version(project, version_id)
    version["status"] = "rejected"
    version["human_rating"] = request.rating
    version["user_feedback"] = request.feedback
    version["rejected_at"] = now_iso()
    write_json(path / "versions" / version_id / "version.json", version)
    save_project(path, project)
    return version


@app.get("/api/projects/{project_id}/final")
def final_media(project_id: str):
    path, project = require_project(project_id)
    if not project.get("final_file"):
        raise HTTPException(404, "No version has been accepted yet")
    media = resolve_project_file(path, project["final_file"])
    return FileResponse(media, media_type="video/mp4", filename=f"{project_id}-final.mp4")


@app.post("/api/projects/{project_id}/assets", status_code=201)
async def asset_upload(
    project_id: str,
    file: UploadFile = File(...),
    category: Annotated[str, Form()] = "other",
    source_name: Annotated[str, Form()] = "User upload",
    license: Annotated[str, Form()] = "User supplied",
    source_url: Annotated[str, Form()] = "",
    usage_notes: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    path, project = require_project(project_id)
    if category not in {"music", "images", "sound_effects", "video", "other"}:
        raise HTTPException(400, "Invalid asset category")
    category_dir = {"music": "music", "images": "images", "sound_effects": "sound_effects", "video": "assets", "other": "downloaded"}[category]
    destination = unique_destination(path / category_dir, safe_filename(file.filename or "asset"))
    max_bytes = int(float(os.getenv("ASSET_DOWNLOAD_MAX_MB", "500")) * 1024 * 1024)
    total = 0
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "Asset exceeds configured size limit")
            output.write(chunk)
    asset = {
        "id": str(uuid.uuid4()), "filename": destination.name, "category": category,
        "file": str(destination.relative_to(path)), "size": total, "source_url": source_url,
        "source_name": source_name, "license": license, "download_date": now_iso(),
        "usage_notes": usage_notes, "used_in": [], "user_supplied": True,
    }
    project.setdefault("assets", []).append(asset)
    save_project(path, project)
    return asset


@app.get("/api/assets/search")
def asset_search(
    q: str = Query(min_length=2, max_length=120),
    media_type: str = Query("image", pattern="^(image|audio|video)$"),
) -> list[dict[str, Any]]:
    return search_wikimedia(q, media_type)


@app.get("/api/library")
def reusable_library() -> list[dict[str, Any]]:
    return _load_library()


@app.get("/api/library/{library_id}/media")
def reusable_library_media(library_id: str):
    item = next((entry for entry in _load_library() if entry.get("id") == library_id), None)
    if not item:
        raise HTTPException(404, "Reusable library asset not found")
    media = (LIBRARY_DIR / item["file"]).resolve()
    if LIBRARY_DIR.resolve() not in media.parents or not media.exists():
        raise HTTPException(404, "Reusable library file is missing")
    return FileResponse(media)


@app.post("/api/projects/{project_id}/assets/{asset_id}/library", status_code=201)
def keep_asset_in_library(project_id: str, asset_id: str) -> dict[str, Any]:
    path, project = require_project(project_id)
    asset = find_asset(project, asset_id)
    library = _load_library()
    existing = next((item for item in library if item.get("source_project") == project_id and item.get("source_asset_id") == asset_id), None)
    if existing:
        return existing
    source = resolve_project_file(path, asset["file"])
    if not source.exists():
        raise HTTPException(404, "Asset file is missing")
    destination = unique_destination(LIBRARY_DIR, asset["filename"])
    shutil.copy2(source, destination)
    item = {
        **{key: asset.get(key) for key in ("filename", "category", "size", "source_url", "source_name", "license", "usage_notes")},
        "id": str(uuid.uuid4()), "filename": destination.name, "file": destination.name,
        "source_project": project_id, "source_asset_id": asset_id, "added_at": now_iso(),
    }
    library.append(item)
    _save_library(library)
    asset["library_id"] = item["id"]
    save_project(path, project)
    return item


@app.post("/api/projects/{project_id}/assets/from-library/{library_id}", status_code=201)
def add_library_asset_to_project(project_id: str, library_id: str) -> dict[str, Any]:
    path, project = require_project(project_id)
    item = next((entry for entry in _load_library() if entry.get("id") == library_id), None)
    if not item:
        raise HTTPException(404, "Reusable library asset not found")
    source = (LIBRARY_DIR / item["file"]).resolve()
    if LIBRARY_DIR.resolve() not in source.parents or not source.exists():
        raise HTTPException(404, "Reusable library file is missing")
    category = item.get("category", "other")
    category_dir = {"music": "music", "images": "images", "sound_effects": "sound_effects", "video": "assets", "other": "downloaded"}.get(category, "downloaded")
    destination = unique_destination(path / category_dir, item["filename"])
    shutil.copy2(source, destination)
    asset = {
        "id": str(uuid.uuid4()), "filename": destination.name, "category": category,
        "file": str(destination.relative_to(path)), "size": destination.stat().st_size,
        "source_url": item.get("source_url", ""), "source_name": item.get("source_name", "Reusable library"),
        "license": item.get("license", "Review source license"), "download_date": now_iso(),
        "usage_notes": item.get("usage_notes", ""), "used_in": [], "user_supplied": item.get("source_name") == "User upload",
        "library_id": library_id,
    }
    project.setdefault("assets", []).append(asset)
    save_project(path, project)
    return asset


@app.post("/api/projects/{project_id}/assets/download", status_code=201)
def asset_download(project_id: str, request: AssetDownload) -> dict[str, Any]:
    path, project = require_project(project_id)
    asset = download_asset(path, request)
    project.setdefault("assets", []).append(asset)
    save_project(path, project)
    return asset


@app.get("/api/projects/{project_id}/assets/{asset_id}/media")
def asset_media(project_id: str, asset_id: str):
    path, project = require_project(project_id)
    asset = find_asset(project, asset_id)
    media = resolve_project_file(path, asset["file"])
    if not media.exists():
        raise HTTPException(404, "Asset file is missing")
    return FileResponse(media, filename=asset["filename"])


@app.delete("/api/projects/{project_id}/assets/{asset_id}")
def asset_remove(project_id: str, asset_id: str) -> dict[str, Any]:
    path, project = require_project(project_id)
    asset = find_asset(project, asset_id)
    used = [version["id"] for version in project.get("versions", []) if asset_id in version.get("assets_used", [])]
    if used:
        raise HTTPException(409, f"Asset is used by versions {', '.join(used)} and was not removed")
    source = resolve_project_file(path, asset["file"])
    moved_to = None
    if source.exists():
        destination = unique_destination(path / "trash", f"asset-{source.name}")
        source.replace(destination)
        moved_to = str(destination.relative_to(path))
    project["assets"] = [item for item in project["assets"] if item["id"] != asset_id]
    project.setdefault("removed_items", []).append({**asset, "removed_at": now_iso(), "trash_file": moved_to})
    save_project(path, project)
    return {"removed": asset_id, "preserved_in_trash": moved_to}


# The SPA is mounted last so /api routes always win.
FRONTEND = ROOT / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
