from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Callable

from .ai_client import generate_edit_plan, self_review
from .config import binary_path, load_settings
from .media import MediaError, extract_frames, probe_media, require_binary, run_checked, run_ffmpeg_progress
from .schemas import EditingPlan, RenderRequest, now_iso
from .storage import (
    find_asset,
    find_clip,
    next_version_id,
    require_project,
    resolve_project_file,
    save_project,
    write_json,
)


def _escape_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", "\\:").replace("'", "\\'").replace("[", "\\[").replace("]", "\\]")


def _atempo_chain(speed: float) -> str:
    values = []
    remaining = speed
    while remaining > 2:
        values.append(2.0)
        remaining /= 2
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    values.append(remaining)
    return ",".join(f"atempo={value:.5f}" for value in values)


def _segment_speed(segment: Any) -> float:
    for effect in segment.effects:
        if effect.type == "speed" and effect.value:
            return float(effect.value)
    return 1.0


def _escape_drawtext(value: str) -> str:
    """Escape user/LLM text for FFmpeg's drawtext expression (no shell involved)."""
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%")


def _render_segment(
    source: Path,
    destination: Path,
    segment: Any,
    has_audio: bool,
    width: int,
    height: int,
    fps: int,
    callback: Callable[[float], None] | None = None,
) -> float:
    ffmpeg = require_binary("ffmpeg")
    source_duration = segment.end - segment.start
    speed = _segment_speed(segment)
    output_duration = source_duration / speed
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{segment.start:.4f}", "-t", f"{source_duration:.4f}", "-i", str(source),
    ]
    if not has_audio:
        command += ["-f", "lavfi", "-t", f"{output_duration:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
    video_filters = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        f"fps={fps}",
    ]
    # Supported zoom effects are applied only in their requested local time window.
    zooms = [effect for effect in segment.effects if effect.type == "zoom"]
    if zooms:
        effect = zooms[0]
        scale = effect.scale or 1.12
        local_start = max(0, effect.start - segment.start)
        local_end = min(source_duration, (effect.end or segment.end) - segment.start)
        if local_end > local_start:
            video_filters += [
                f"scale=w='if(between(t,{local_start:.3f},{local_end:.3f}),ceil(iw*{scale:.4f}/2)*2,iw)'"
                f":h='if(between(t,{local_start:.3f},{local_end:.3f}),ceil(ih*{scale:.4f}/2)*2,ih)':eval=frame",
                f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2",
            ]
    fades = [effect for effect in segment.effects if effect.type == "fade"]
    if fades:
        fade_duration = min(0.8, max(0.1, float(fades[0].end or 0.3) - float(fades[0].start)))
        video_filters.append(f"fade=t=in:st=0:d={fade_duration:.3f}")
    # Text effects are bounded, escaped overlays. The model never supplies filter syntax.
    for effect in [item for item in segment.effects if item.type == "text" and item.text][:4]:
        local_start = max(0, effect.start - segment.start)
        local_end = min(source_duration, (effect.end or segment.end) - segment.start)
        if local_end > local_start:
            text = _escape_drawtext(effect.text or "")
            video_filters.append(
                f"drawtext=text='{text}':x=(w-text_w)/2:y=h*0.14:fontsize=h*0.055:"
                f"fontcolor=white:borderw=3:bordercolor=black@0.8:enable='between(t,{local_start:.3f},{local_end:.3f})'"
            )
    if segment.transition == "fade" and output_duration > 0.5:
        video_filters.append(f"fade=t=out:st={max(0, output_duration - 0.25):.3f}:d=0.25")
    if speed != 1:
        video_filters.append(f"setpts=PTS/{speed:.6f}")
    video_filters += ["format=yuv420p"]
    if has_audio:
        audio_filters = [
            "asetpts=PTS-STARTPTS", "aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"
        ]
        if speed != 1:
            audio_filters.append(_atempo_chain(speed))
        filter_complex = f"[0:v]{','.join(video_filters)}[v];[0:a]{','.join(audio_filters)}[a]"
        command += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
    else:
        filter_complex = f"[0:v]{','.join(video_filters)}[v];[1:a]asetpts=PTS-STARTPTS[a]"
        command += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
    command += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-movflags", "+faststart", "-shortest", str(destination),
    ]
    run_ffmpeg_progress(command, output_duration, callback)
    return output_duration


def _ass_time(seconds: float) -> str:
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _write_ass(path: Path, plan: EditingPlan, width: int, height: int, duration: float) -> bool:
    captions = [caption for caption in plan.captions if caption.start < duration and caption.end > caption.start]
    if not captions:
        return False
    font_size = max(28, round(height * 0.055))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H0000FFFF,&H00111111,&H88000000,-1,0,0,0,100,100,0,0,1,3,1,2,90,90,{round(height * .08)},1
Style: Emphasis,Arial,{round(font_size * 1.12)},&H0000E4FF,&H0000FFFF,&H00111111,&H88000000,-1,0,0,0,100,100,0,0,1,4,1,2,90,90,{round(height * .08)},1
Style: Subtle,Arial,{round(font_size * .78)},&H00EEEEEE,&H0000FFFF,&H00111111,&H88000000,0,0,0,0,100,100,0,0,1,2,0,2,90,90,{round(height * .06)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for caption in captions:
        style = {"default": "Default", "emphasis": "Emphasis", "subtle": "Subtle"}[caption.style]
        end = min(duration, caption.end)
        lines.append(f"Dialogue: 0,{_ass_time(caption.start)},{_ass_time(end)},{style},,0,0,0,,{_ass_escape(caption.text)}")
    path.write_text(header + "\n".join(lines) + "\n", "utf-8")
    return True


def _concat_segments(segment_paths: list[Path], destination: Path) -> None:
    ffmpeg = require_binary("ffmpeg")
    concat_file = destination.parent / "concat.txt"
    lines = ["file '" + str(path.resolve()).replace("'", "'\\''") + "'" for path in segment_paths]
    concat_file.write_text("\n".join(lines) + "\n", "utf-8")
    run_checked([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(destination),
    ], "Timeline assembly")


def _finish_render(
    project_path: Path,
    project: dict[str, Any],
    plan: EditingPlan,
    timeline_path: Path,
    destination: Path,
    ass_path: Path,
    duration: float,
    width: int,
    height: int,
    progress: Callable[..., None],
) -> list[str]:
    ffmpeg = require_binary("ffmpeg")
    warnings: list[str] = []
    settings = load_settings()
    burn_captions = settings.burn_captions and _write_ass(ass_path, plan, width, height, duration)
    input_command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(timeline_path)]
    visual_inputs: list[tuple[Any, int]] = []
    audio_inputs: list[tuple[str, Any, int]] = []
    input_index = 1
    for cue in plan.visual_overlays[:8]:
        try:
            asset = find_asset(project, cue.asset_id)
            path = resolve_project_file(project_path, asset["file"])
            if not path.exists():
                raise FileNotFoundError(path.name)
            input_command += ["-loop", "1", "-framerate", str(settings.output_fps), "-i", str(path)]
            visual_inputs.append((cue, input_index))
            input_index += 1
        except Exception as exc:
            warnings.append(f"Skipped missing visual asset {cue.asset_id}: {exc}")
    for cue_type, cues in (("music", plan.music[:1]), ("sfx", plan.sound_effects[:8])):
        for cue in cues:
            try:
                asset = find_asset(project, cue.asset_id)
                path = resolve_project_file(project_path, asset["file"])
                if not path.exists():
                    raise FileNotFoundError(path.name)
                if cue_type == "music":
                    input_command += ["-stream_loop", "-1", "-i", str(path)]
                else:
                    input_command += ["-i", str(path)]
                audio_inputs.append((cue_type, cue, input_index))
                input_index += 1
            except Exception as exc:
                warnings.append(f"Skipped missing audio asset {cue.asset_id}: {exc}")

    filters: list[str] = []
    video_current = "[0:v]"
    margin = max(16, round(min(width, height) * 0.025))
    positions = {
        "top_left": (str(margin), str(margin)),
        "top_right": (f"W-w-{margin}", str(margin)),
        "bottom_left": (str(margin), f"H-h-{margin}"),
        "bottom_right": (f"W-w-{margin}", f"H-h-{margin}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }
    for seq, (cue, idx) in enumerate(visual_inputs):
        image_label = f"img{seq}"
        output_label = f"vov{seq}"
        if cue.mode == "fullscreen":
            image_filter = (
                f"[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,format=rgba,"
                f"colorchannelmixer=aa={cue.opacity:.3f}[{image_label}]"
            )
            x, y = "0", "0"
        else:
            pip_width = max(160, round(width * 0.3 / 2) * 2)
            image_filter = (
                f"[{idx}:v]scale={pip_width}:-2:flags=lanczos,format=rgba,"
                f"colorchannelmixer=aa={cue.opacity:.3f}[{image_label}]"
            )
            x, y = positions[cue.position]
        filters.append(image_filter)
        filters.append(
            f"{video_current}[{image_label}]overlay=x={x}:y={y}:"
            f"enable='between(t,{cue.start:.3f},{min(duration, cue.end):.3f})':eof_action=pass[{output_label}]"
        )
        video_current = f"[{output_label}]"

    video_without_captions = video_current
    caption_filter: str | None = None
    if burn_captions:
        caption_filter = f"{video_current}subtitles='{_escape_filter_path(ass_path)}'[vout]"
        filters.append(caption_filter)
        video_current = "[vout]"
    map_video = video_current if visual_inputs or burn_captions else "0:v"

    map_audio = "0:a"
    if audio_inputs:
        mix_labels = ["[base]"]
        filters.append("[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo[base]")
        for seq, (cue_type, cue, idx) in enumerate(audio_inputs):
            label = f"aud{seq}"
            if cue_type == "music":
                end = min(duration, cue.end or duration)
                length = max(0.1, end - cue.start)
                delay = round(cue.start * 1000)
                filters.append(
                    f"[{idx}:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,volume={cue.volume:.3f},"
                    f"afade=t=in:st=0:d={min(.8, length/3):.3f},afade=t=out:st={max(0, length-.8):.3f}:d={min(.8, length/3):.3f},"
                    f"adelay={delay}|{delay}[{label}]"
                )
            else:
                delay = round(cue.time * 1000)
                filters.append(f"[{idx}:a]volume={cue.volume:.3f},adelay={delay}|{delay}[{label}]")
            mix_labels.append(f"[{label}]")
        filters.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=1[aout]")
        map_audio = "[aout]"

    def build_command(active_filters: list[str], active_video: str, encode_video: bool) -> list[str]:
        command = list(input_command)
        if active_filters:
            command += ["-filter_complex", ";".join(active_filters)]
        command += ["-map", active_video, "-map", map_audio, "-c:v", "libx264" if encode_video else "copy"]
        if encode_video:
            command += ["-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
        command += ["-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.4f}", "-movflags", "+faststart", str(destination)]
        return command

    encode_video = bool(visual_inputs or burn_captions)
    try:
        run_ffmpeg_progress(build_command(filters, map_video, encode_video), duration, lambda value: progress(
            stage="Rendering final video", progress=76 + value * 0.14,
            message=f"Encoding overlays, captions and audio — {value:.0f}%",
        ))
    except MediaError as exc:
        if burn_captions and "subtitles" in str(exc).lower():
            warnings.append("This FFmpeg build lacks the subtitles filter; captions were saved as a sidecar ASS file but not burned in.")
            fallback_filters = [item for item in filters if item != caption_filter]
            fallback_video = video_without_captions if visual_inputs else "0:v"
            run_ffmpeg_progress(
                build_command(fallback_filters, fallback_video, bool(visual_inputs)), duration,
                lambda value: progress(stage="Rendering final video", progress=76 + value * 0.14, message=f"Encoding without burned captions — {value:.0f}%"),
            )
        else:
            raise
    return warnings


def render_one_version(
    project_path: Path,
    project: dict[str, Any],
    request: RenderRequest,
    progress: Callable[..., None],
    retry_feedback: str = "",
) -> dict[str, Any]:
    settings = load_settings()
    progress(stage="AI editing", progress=3, message="Building a validated editing plan")
    combined_feedback = "\n".join(value for value in (request.feedback, retry_feedback) if value)
    plan, plan_warning = generate_edit_plan(
        project_path, project, request.instructions, combined_feedback, request.include_captions, request.use_ai
    )
    version_id = next_version_id(project, project_path)
    version_dir = project_path / "versions" / version_id
    # next_version_id accounts for both completed metadata and interrupted render
    # directories. Keep this guard as a final race-safety check; project jobs are
    # serialized by the API, so it should never be reached in normal operation.
    if version_dir.exists():
        raise RuntimeError(f"Could not allocate an unused version directory after selecting {version_id}")
    work_dir = version_dir / "work"
    work_dir.mkdir(parents=True)
    write_json(version_dir / "edit_plan.json", plan.model_dump(mode="json"))
    progress(stage="Preparing render", progress=10, message=f"Created immutable version {version_id}")

    segment_paths: list[Path] = []
    total_duration = 0.0
    for index, segment in enumerate(plan.timeline):
        clip = find_clip(project, segment.clip_id)
        source = resolve_project_file(project_path, clip["file"])
        metadata = clip.get("metadata") or probe_media(source)
        clip["metadata"] = metadata
        destination = work_dir / f"segment_{index:04d}.mp4"
        phase_start = 12 + index / len(plan.timeline) * 54
        phase_span = 54 / len(plan.timeline)
        progress(stage="Rendering timeline", progress=phase_start, message=f"Rendering cut {index + 1} of {len(plan.timeline)}")
        output_duration = _render_segment(
            source, destination, segment, bool(metadata.get("has_audio")),
            settings.output_width, settings.output_height, settings.output_fps,
            lambda pct, start=phase_start, span=phase_span: progress(
                stage="Rendering timeline", progress=start + span * pct / 100,
                message=f"Rendering cut {index + 1} of {len(plan.timeline)} — {pct:.0f}%",
            ),
        )
        total_duration += output_duration
        segment_paths.append(destination)
    progress(stage="Assembling edit", progress=68, message="Joining normalized clips in your locked order")
    timeline_path = work_dir / "timeline.mp4"
    _concat_segments(segment_paths, timeline_path)
    output_path = version_dir / "video.mp4"
    render_warnings = _finish_render(
        project_path, project, plan, timeline_path, output_path, version_dir / "captions.ass",
        total_duration, settings.output_width, settings.output_height, progress,
    )
    progress(stage="AI Self-Review", progress=92, message="Sampling the finished render for advisory review")
    review_frames_dir = version_dir / "review_frames"
    review_frames = extract_frames(output_path, review_frames_dir, total_duration, max(2, math.ceil(total_duration / 8)))
    frame_paths = [review_frames_dir / frame["file"] for frame in review_frames]
    review, review_warning = self_review(plan, total_duration, frame_paths, project)
    write_json(version_dir / "self_review.json", review.model_dump(mode="json"))

    assets_used = sorted({cue.asset_id for cue in [*plan.visual_overlays, *plan.music, *plan.sound_effects]})
    version = {
        "id": version_id,
        "created_at": now_iso(),
        "video_file": str(output_path.relative_to(project_path)),
        "plan_file": str((version_dir / "edit_plan.json").relative_to(project_path)),
        "review_file": str((version_dir / "self_review.json").relative_to(project_path)),
        "caption_file": str((version_dir / "captions.ass").relative_to(project_path)) if (version_dir / "captions.ass").exists() else None,
        "duration": round(total_duration, 3),
        "review": review.model_dump(mode="json"),
        "human_rating": None,
        "user_feedback": combined_feedback,
        "instructions": request.instructions,
        "ai_model": settings.model if request.use_ai and not plan_warning else "local-smart-plan",
        "vision_model": settings.vision_model if not review_warning else "local-heuristics",
        "assets_used": assets_used,
        "warnings": [item for item in [plan_warning, review_warning, *render_warnings] if item],
        "status": "awaiting_approval",
    }
    write_json(version_dir / "version.json", version)
    project.setdefault("versions", []).append(version)
    for asset in project.get("assets", []):
        if asset.get("id") in assets_used:
            used_in = asset.setdefault("used_in", [])
            if version_id not in used_in:
                used_in.append(version_id)
    project["workflow"] = "review"
    save_project(project_path, project)
    # Intermediates are deterministic and can be large. They are not user assets, but keep by default
    # to honor the no-silent-delete principle; the UI can expose cleanup later.
    return version


def render_project(project_id: str, request: RenderRequest, progress: Callable[..., None]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    max_attempts = 1 + (load_settings().max_auto_retries if request.auto_retry else 0)
    retry_feedback = ""
    for attempt in range(max_attempts):
        project_path, project = require_project(project_id)
        version = render_one_version(project_path, project, request, progress, retry_feedback)
        attempts.append(version)
        if version["review"]["score"] >= 5 or not request.auto_retry:
            break
        retry_feedback = "AI Self-Review requested these changes: " + "; ".join(version["review"].get("suggested_changes", []))
        progress(stage="Regenerating", progress=2, message=f"AI score was below 5; starting bounded retry {attempt + 2} of {max_attempts}")
    return {
        "project_id": project_id,
        "versions": [version["id"] for version in attempts],
        "latest_version": attempts[-1]["id"],
        "score": attempts[-1]["review"]["score"],
        "recommend_retry": attempts[-1]["review"]["score"] < 5,
        "attempts": len(attempts),
    }
