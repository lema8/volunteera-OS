from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

import httpx

from .ai_client import analyze_visual_frames
from .config import get_api_key, load_settings
from .media import detect_scenes, extract_audio, extract_frames, probe_media
from .schemas import now_iso
from .storage import file_signature, resolve_project_file, save_project, write_json


def transcribe_local(audio: Path, model_name: str) -> list[dict[str, Any]]:
    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError("faster-whisper is not installed")
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(audio), vad_filter=True)
    return [
        {"start": round(float(s.start), 3), "end": round(float(s.end), 3), "text": s.text.strip()}
        for s in segments if s.text.strip()
    ]


def transcribe_remote(audio: Path) -> list[dict[str, Any]]:
    settings = load_settings()
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("No AI API key is configured")
    with audio.open("rb") as stream:
        response = httpx.post(
            f"{settings.api_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": settings.stt_model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"},
            files={"file": (audio.name, stream, "audio/wav")},
            timeout=600,
        )
    response.raise_for_status()
    data = response.json()
    return [
        {"start": round(float(s.get("start", 0)), 3), "end": round(float(s.get("end", 0)), 3), "text": s.get("text", "").strip()}
        for s in data.get("segments", []) if s.get("text", "").strip()
    ]


def run_ocr(frame_dir: Path, frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if importlib.util.find_spec("pytesseract") is None or importlib.util.find_spec("PIL") is None:
        raise RuntimeError("pytesseract/Pillow is not installed")
    import pytesseract  # type: ignore
    from PIL import Image

    result = []
    # OCR is relatively expensive; representative frames only, bounded at 24.
    for frame in frames[:24]:
        try:
            text = " ".join(pytesseract.image_to_string(Image.open(frame_dir / frame["file"])).split())
        except Exception:
            continue
        if len(text) >= 2:
            result.append({"time": frame["time"], "text": text[:1000]})
    return result


def derive_events(
    duration: float, scenes: list[dict[str, Any]], speech: list[dict[str, Any]], ocr: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events = []
    for item in speech:
        text = item["text"]
        importance = 0.82 if any(mark in text for mark in ("!", "?")) else 0.58
        if len(text.split()) >= 4:
            events.append({
                "start": item["start"], "end": item["end"],
                "description": f'Spoken moment: "{text[:180]}"', "importance": importance,
            })
    for item in ocr:
        events.append({
            "start": item["time"], "end": min(duration, item["time"] + 2),
            "description": f'On-screen text appears: "{item["text"][:180]}"', "importance": 0.62,
        })
    # Scene changes indicate visual events, even if no speech model is available.
    for scene in scenes[1:25]:
        events.append({
            "start": scene["start"], "end": min(scene["end"], scene["start"] + 4),
            "description": "Significant visual scene change", "importance": 0.52,
        })
    return sorted(events, key=lambda item: (item["start"], -item["importance"]))[:100]


def analyze_project(
    project_path: Path,
    project: dict[str, Any],
    progress: Callable[..., None],
    force: bool = False,
) -> dict[str, Any]:
    settings = load_settings()
    clips = project.get("clips", [])
    if not clips:
        raise ValueError("Upload at least one video before analysis")
    warnings: list[str] = []
    for index, clip in enumerate(clips):
        base = (index / len(clips)) * 94
        span = 94 / len(clips)
        source = resolve_project_file(project_path, clip["file"])
        signature = file_signature(source)
        profile = {
            "frame_interval": settings.frame_interval,
            "local_whisper": settings.use_local_whisper,
            "whisper_model": settings.whisper_model if settings.use_local_whisper else settings.stt_model,
            "ocr": settings.enable_ocr,
            "vision": settings.enable_vision_analysis and bool(get_api_key()),
            "vision_model": settings.vision_model if settings.enable_vision_analysis and get_api_key() else None,
        }
        output = project_path / "analysis" / clip["id"] / "analysis.json"
        if output.exists() and not force:
            try:
                existing = json.loads(output.read_text("utf-8"))
                if existing.get("source_signature") == signature and existing.get("analysis_profile") == profile:
                    clip["analysis_status"] = "complete"
                    clip["analysis_file"] = str(output.relative_to(project_path))
                    progress(stage="Analyzing footage", progress=base + span, message=f"Using cached analysis for {clip['name']}")
                    continue
            except (json.JSONDecodeError, OSError):
                pass

        analysis_dir = output.parent
        frame_dir = analysis_dir / "frames"
        progress(stage="Reading metadata", progress=base + span * 0.08, message=f"Inspecting {clip['name']}")
        metadata = probe_media(source)
        clip["metadata"] = metadata
        progress(stage="Extracting frames", progress=base + span * 0.22, message=f"Sampling {clip['name']} every {settings.frame_interval}s")
        frames = extract_frames(source, frame_dir, metadata["duration"], settings.frame_interval)
        progress(stage="Detecting scenes", progress=base + span * 0.4, message=f"Finding scene changes in {clip['name']}")
        scenes = detect_scenes(source, metadata["duration"])
        speech: list[dict[str, Any]] = []
        audio_file = analysis_dir / "audio.wav"
        if metadata["has_audio"]:
            progress(stage="Extracting audio", progress=base + span * 0.55, message=f"Preparing speech track for {clip['name']}")
            extract_audio(source, audio_file)
            progress(stage="Transcribing speech", progress=base + span * 0.7, message=f"Transcribing {clip['name']}")
            try:
                if settings.use_local_whisper:
                    speech = transcribe_local(audio_file, settings.whisper_model)
                else:
                    speech = transcribe_remote(audio_file)
            except Exception as exc:
                warnings.append(f"{clip['name']}: transcription skipped ({exc})")
        ocr: list[dict[str, Any]] = []
        if settings.enable_ocr:
            progress(stage="Reading on-screen text", progress=base + span * 0.83, message=f"Running OCR on {clip['name']}")
            try:
                ocr = run_ocr(frame_dir, frames)
            except Exception as exc:
                warnings.append(f"{clip['name']}: OCR skipped ({exc})")
        visual = {"visual_summary": "", "events": [], "hook_candidates": [], "ending_candidates": []}
        if settings.enable_vision_analysis and get_api_key():
            progress(stage="AI visual analysis", progress=base + span * 0.9, message=f"Understanding representative frames from {clip['name']}")
            try:
                visual = analyze_visual_frames(frame_dir, frames, metadata["duration"], speech, ocr).model_dump(mode="json")
            except Exception as exc:
                warnings.append(f"{clip['name']}: AI visual analysis skipped ({exc})")
        events = derive_events(metadata["duration"], scenes, speech, ocr)
        events.extend(visual["events"])
        events = sorted(events, key=lambda item: (item["start"], -item["importance"]))[:150]
        analysis = {
            "clip_id": clip["id"], "clip": clip["name"], "source_signature": signature,
            "analysis_profile": profile, "analyzed_at": now_iso(),
            "duration": metadata["duration"], "metadata": metadata,
            "frames": frames, "scenes": scenes, "speech": speech, "ocr": ocr,
            "visual_summary": visual["visual_summary"], "hook_candidates": visual["hook_candidates"],
            "ending_candidates": visual["ending_candidates"], "events": events,
        }
        write_json(output, analysis)
        clip["analysis_status"] = "complete"
        clip["analysis_file"] = str(output.relative_to(project_path))
        progress(stage="Caching analysis", progress=base + span * 0.95, message=f"Saved analysis for {clip['name']}")
    project["workflow"] = "ready"
    project["analysis_warnings"] = warnings
    save_project(project_path, project)
    return {"project_id": project["id"], "clips_analyzed": len(clips), "warnings": warnings}
