from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import binary_path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class MediaError(RuntimeError):
    pass


def require_binary(kind: str) -> str:
    executable = binary_path(kind)
    if not executable:
        raise MediaError(
            f"{kind} was not found. Install FFmpeg and ensure `{kind}` is on PATH, "
            f"or configure {kind.upper()}_PATH in .env."
        )
    return executable


def run_checked(command: list[str], error_label: str = "FFmpeg") -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise MediaError(f"Could not start {error_label}: {exc}") from exc
    if result.returncode != 0:
        detail = "\n".join(result.stderr.strip().splitlines()[-12:])
        raise MediaError(f"{error_label} failed: {detail or 'unknown error'}")
    return result


def parse_fraction(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else 0
    try:
        return float(value)
    except ValueError:
        return 0


def probe_media(path: Path) -> dict[str, Any]:
    ffprobe = require_binary("ffprobe")
    result = run_checked([
        ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ], "FFprobe")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("FFprobe returned invalid metadata") from exc
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        raise MediaError("This file does not contain a readable video stream")
    format_info = info.get("format", {})
    duration = float(format_info.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError("The video duration could not be determined")
    return {
        "duration": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate")), 3),
        "video_codec": video.get("codec_name"),
        "pixel_format": video.get("pix_fmt"),
        "has_audio": bool(audio),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else 0,
        "audio_sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        "format": format_info.get("format_long_name") or format_info.get("format_name"),
        "size": int(format_info.get("size") or path.stat().st_size),
        "bit_rate": int(format_info.get("bit_rate") or 0),
    }


def extract_thumbnail(source: Path, destination: Path, duration: float) -> None:
    ffmpeg = require_binary("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    seek = max(0, min(duration * 0.15, duration - 0.1, 3))
    run_checked([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seek:.3f}", "-i", str(source),
        "-frames:v", "1", "-vf", "scale=640:-2:force_original_aspect_ratio=decrease", "-q:v", "3", str(destination)
    ], "Thumbnail extraction")


def extract_frames(source: Path, directory: Path, duration: float, interval: int) -> list[dict[str, Any]]:
    ffmpeg = require_binary("ffmpeg")
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.jpg"):
        old.unlink()
    # Representative frames: fixed intervals. Keep model payloads and disk usage bounded.
    effective_interval = max(interval, math.ceil(duration / 40))
    pattern = directory / "frame_%04d.jpg"
    run_checked([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", f"fps=1/{effective_interval},scale=960:-2:force_original_aspect_ratio=decrease",
        "-frames:v", "40", "-q:v", "4", str(pattern)
    ], "Frame extraction")
    frames = []
    for index, frame in enumerate(sorted(directory.glob("frame_*.jpg"))):
        frames.append({"time": round(index * effective_interval, 3), "file": str(frame.name), "kind": "interval"})
    if not frames:
        # Very short clips can produce no interval frame depending on timestamps.
        fallback = directory / "frame_0001.jpg"
        extract_thumbnail(source, fallback, duration)
        frames = [{"time": 0, "file": fallback.name, "kind": "interval"}]
    return frames


def detect_scenes(source: Path, duration: float) -> list[dict[str, Any]]:
    ffmpeg = require_binary("ffmpeg")
    command = [
        ffmpeg, "-hide_banner", "-i", str(source), "-filter:v", "select='gt(scene,0.35)',showinfo",
        "-an", "-f", "null", "-"
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    times: list[float] = []
    for line in result.stderr.splitlines():
        match = re.search(r"pts_time:([0-9.]+)", line)
        if match:
            value = float(match.group(1))
            if not times or value - times[-1] > 0.35:
                times.append(round(value, 3))
        if len(times) >= 100:
            break
    boundaries = [0.0, *times, duration]
    return [
        {"start": round(boundaries[i], 3), "end": round(boundaries[i + 1], 3), "change_score": 0.7}
        for i in range(len(boundaries) - 1) if boundaries[i + 1] - boundaries[i] > 0.1
    ]


def extract_audio(source: Path, destination: Path) -> None:
    ffmpeg = require_binary("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_checked([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination)
    ], "Audio extraction")


def run_ffmpeg_progress(
    command: list[str], duration: float, callback: Callable[[float], None] | None = None
) -> None:
    """Execute an app-generated FFmpeg command and parse machine-readable progress."""
    command = [*command[:-1], "-progress", "pipe:1", "-nostats", command[-1]]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        if key in {"out_time_ms", "out_time_us"} and duration > 0:
            # FFmpeg can emit AV_NOPTS_VALUE (-2^63) before the first encoded frame.
            seconds = max(0, int(value or 0) / 1_000_000)
            if callback:
                callback(max(0, min(99, seconds / duration * 100)))
    assert process.stderr is not None
    stderr_lines = process.stderr.read().splitlines()
    return_code = process.wait()
    if return_code != 0:
        raise MediaError("FFmpeg render failed: " + "\n".join(stderr_lines[-15:]))
    if callback:
        callback(100)
