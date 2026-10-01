from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from .config import get_api_key, load_settings
from .schemas import Caption, ClipVisionAnalysis, EditingPlan, Review, TimelineSegment
from .storage import resolve_project_file


class AIError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        candidate = candidate[start:end + 1] if start >= 0 and end > start else candidate
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise AIError(f"The AI returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AIError("The AI response must be a JSON object")
    return value


def call_json(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    settings = load_settings()
    key = get_api_key()
    if not key:
        raise AIError("No AI API key is configured. Add one in AI Settings or use the local smart plan.")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    endpoint = f"{settings.api_base_url.rstrip('/')}/chat/completions"
    try:
        response = httpx.post(endpoint, headers=headers, json=payload, timeout=300)
        # Some compatible providers do not support response_format. Retry without it.
        if response.status_code in {400, 422} and "response_format" in response.text:
            payload.pop("response_format", None)
            response = httpx.post(endpoint, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return _extract_json(content)
    except httpx.TimeoutException as exc:
        raise AIError("The AI provider timed out after 5 minutes") from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise AIError(f"AI provider returned HTTP {exc.response.status_code}: {detail}") from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AIError("The AI provider returned an unsupported response format") from exc


def load_analyses(project_path: Path, project: dict[str, Any]) -> list[dict[str, Any]]:
    analyses = []
    for clip in project.get("clips", []):
        analysis_file = clip.get("analysis_file")
        if not analysis_file:
            continue
        path = resolve_project_file(project_path, analysis_file)
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Exclude storage paths and verbose raw metadata from the LLM context.
        analyses.append({
            "clip_id": clip["id"], "clip_name": clip["name"], "order": clip.get("order"),
            "duration": data.get("duration"), "scenes": data.get("scenes", [])[:80],
            "speech": data.get("speech", [])[:500], "ocr": data.get("ocr", [])[:100],
            "visual_summary": data.get("visual_summary", ""),
            "hook_candidates": data.get("hook_candidates", [])[:10],
            "ending_candidates": data.get("ending_candidates", [])[:10],
            "events": data.get("events", [])[:100],
        })
    return analyses


def _speech_groups(speech: list[dict[str, Any]], duration: float) -> list[tuple[float, float]]:
    if not speech:
        return [(0.0, duration)]
    groups: list[list[float]] = []
    for line in speech:
        start, end = max(0, float(line["start"]) - 0.25), min(duration, float(line["end"]) + 0.35)
        if groups and start - groups[-1][1] < 2.2:
            groups[-1][1] = max(groups[-1][1], end)
        else:
            groups.append([start, end])
    # Preserve context; avoid creating twitchy sub-second cuts.
    merged = [(round(a, 3), round(b, 3)) for a, b in groups if b - a >= 0.65]
    return merged or [(0.0, duration)]


def deterministic_plan(project_path: Path, project: dict[str, Any], include_captions: bool = True) -> EditingPlan:
    analyses = {item["clip_id"]: item for item in load_analyses(project_path, project)}
    timeline: list[TimelineSegment] = []
    captions: list[Caption] = []
    output_cursor = 0.0
    for clip in project.get("clips", []):
        analysis = analyses.get(clip["id"], {})
        duration = float(analysis.get("duration") or clip.get("metadata", {}).get("duration") or 0)
        if duration <= 0:
            continue
        speech = analysis.get("speech", [])
        groups = _speech_groups(speech, duration)
        # With no transcript we make no destructive assumptions and retain the whole clip.
        for group_start, group_end in groups:
            effects = []
            # A restrained opening fade; no random effects.
            if not timeline:
                effects.append({"type": "fade", "start": 0, "end": min(0.35, group_end - group_start)})
            segment = TimelineSegment(
                clip_id=clip["id"], start=group_start, end=group_end,
                effects=effects, transition="cut",
            )
            timeline.append(segment)
            if include_captions:
                for line in speech:
                    overlap_start = max(group_start, float(line["start"]))
                    overlap_end = min(group_end, float(line["end"]))
                    if overlap_end > overlap_start and line.get("text", "").strip():
                        captions.append(Caption(
                            start=round(output_cursor + overlap_start - group_start, 3),
                            end=round(output_cursor + overlap_end - group_start, 3),
                            text=line["text"].strip(), style="default",
                        ))
            output_cursor += group_end - group_start
    if not timeline:
        raise AIError("No analyzed video duration is available. Analyze the clips first.")
    return EditingPlan(
        rationale=(
            "Local smart plan: respected the exact clip order, removed only transcript-confirmed long pauses, "
            "retained full clips when speech data was unavailable, and added transcript captions."
        ),
        timeline=timeline, captions=captions,
    )


def validate_plan_for_project(plan: EditingPlan, project: dict[str, Any]) -> EditingPlan:
    clips = {clip["id"]: clip for clip in project.get("clips", [])}
    order = {clip["id"]: index for index, clip in enumerate(project.get("clips", []))}
    last_order = -1
    last_start_by_clip: dict[str, float] = {}
    for segment in plan.timeline:
        if segment.clip_id not in clips:
            raise AIError(f"Plan references unknown clip: {segment.clip_id}")
        current = order[segment.clip_id]
        if current < last_order:
            raise AIError("AI plan attempted to change the user-defined clip order")
        if segment.start < last_start_by_clip.get(segment.clip_id, -1):
            raise AIError("AI plan attempted to reverse time inside a clip")
        duration = float(clips[segment.clip_id].get("metadata", {}).get("duration") or 0)
        if duration and segment.end > duration + 0.05:
            raise AIError(f"Plan exceeds the duration of {clips[segment.clip_id]['name']}")
        if segment.end - segment.start < 0.1:
            raise AIError("Plan contains a segment shorter than 0.1 seconds")
        last_order = current
        last_start_by_clip[segment.clip_id] = segment.start
    asset_map = {asset["id"]: asset for asset in project.get("assets", [])}
    for cue in [*plan.music, *plan.sound_effects, *plan.visual_overlays]:
        if cue.asset_id not in asset_map:
            raise AIError(f"Plan references unavailable asset: {cue.asset_id}")
    for cue in plan.visual_overlays:
        if asset_map[cue.asset_id].get("category") != "images":
            raise AIError("Visual overlays currently require an image asset")
    output_duration = sum(
        (segment.end - segment.start) /
        next((float(effect.value) for effect in segment.effects if effect.type == "speed" and effect.value), 1.0)
        for segment in plan.timeline
    )
    for timed in [*plan.captions, *plan.visual_overlays]:
        if timed.end > output_duration + 0.1:
            raise AIError("Plan contains an output-timeline cue beyond the rendered duration")
    for cue in plan.sound_effects:
        if cue.time > output_duration + 0.1:
            raise AIError("Plan contains a sound cue beyond the rendered duration")
    return plan


def generate_edit_plan(
    project_path: Path,
    project: dict[str, Any],
    instructions: str,
    feedback: str,
    include_captions: bool,
    use_ai: bool,
) -> tuple[EditingPlan, str | None]:
    fallback = deterministic_plan(project_path, project, include_captions)
    if not use_ai:
        return validate_plan_for_project(fallback, project), None
    settings = load_settings()
    if not get_api_key():
        return validate_plan_for_project(fallback, project), "No API key configured; used the local smart plan."
    analyses = load_analyses(project_path, project)
    prior_feedback = [
        {"version": v.get("id"), "human_rating": v.get("human_rating"), "feedback": v.get("user_feedback", ""),
         "review_problems": v.get("review", {}).get("problems", [])}
        for v in project.get("versions", [])[-5:]
    ]
    prompt = {
        "project": {"name": project["name"], "style": project.get("style"), "instructions": project.get("instructions")},
        "current_instructions": instructions,
        "latest_human_feedback_HIGH_PRIORITY": feedback,
        "previous_human_feedback_HIGH_PRIORITY": prior_feedback,
        "clips_in_locked_order": analyses,
        "available_assets": [
            {k: a.get(k) for k in ("id", "filename", "category", "license", "usage_notes")}
            for a in project.get("assets", [])
        ],
        "required_schema": EditingPlan.model_json_schema(),
    }
    system = (
        "You are a restrained professional YouTube video editor. Return ONLY a valid JSON object matching the schema. "
        "The clips_in_locked_order sequence is inviolable: NEVER place a later clip before an earlier clip. You may "
        "trim or split each clip, but segments within a clip must also remain chronological. Prioritize human feedback "
        "over your own preferences. Preserve context. Improve hook, pacing, payoff and clarity. Avoid excessive effects, "
        "captions, zooms, memes, transitions and sound effects. Use only supplied clip_id and asset_id values. Effects "
        "support zoom, fade, speed and text. visual_overlays may use only supplied image-category assets. Caption, "
        "visual overlay, music, and sound cue times are OUTPUT timeline times, not source times."
    )
    try:
        raw = call_json([
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ], settings.model)
        plan = EditingPlan.model_validate(raw)
        return validate_plan_for_project(plan, project), None
    except (AIError, ValidationError, ValueError) as exc:
        fallback.rationale += f" AI response was safely rejected: {str(exc)[:300]}"
        return validate_plan_for_project(fallback, project), f"AI plan rejected; used safe local plan: {exc}"


def _data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def analyze_visual_frames(
    frame_dir: Path,
    frames: list[dict[str, Any]],
    duration: float,
    speech: list[dict[str, Any]],
    ocr: list[dict[str, Any]],
) -> ClipVisionAnalysis:
    """Ask the configured vision model about a bounded set of low-res representative frames."""
    settings = load_settings()
    if not settings.enable_vision_analysis or not get_api_key():
        return ClipVisionAnalysis()
    if not frames:
        return ClipVisionAnalysis()
    if len(frames) <= 8:
        selected = frames
    else:
        selected = [frames[round(index * (len(frames) - 1) / 7)] for index in range(8)]
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": json.dumps({
            "task": "Analyze this ordered sample of low-resolution video frames before editing.",
            "duration": duration,
            "speech_context": speech[:120],
            "ocr_context": ocr[:50],
            "required_schema": ClipVisionAnalysis.model_json_schema(),
            "rules": [
                "Describe concrete visible events, not imagined events.",
                "Use timestamps from the frame labels.",
                "Importance is 0 to 1 for editing relevance.",
                "Suggest hook/ending candidates only when supported by visible context.",
            ],
        }, ensure_ascii=False),
    }]
    for frame in selected:
        path = frame_dir / frame["file"]
        if not path.exists() or path.stat().st_size > 2_000_000:
            continue
        content.append({"type": "text", "text": f"Representative frame at {float(frame['time']):.3f} seconds"})
        content.append({"type": "image_url", "image_url": {"url": _data_url(path), "detail": "low"}})
    raw = call_json([
        {"role": "system", "content": (
            "You are the visual-analysis stage of a professional video editing system. Return only valid JSON matching "
            "the supplied schema. Infer no identities or off-screen events. Be concise and useful to a later editor."
        )},
        {"role": "user", "content": content},
    ], settings.vision_model)
    analysis = ClipVisionAnalysis.model_validate(raw)
    for event in analysis.events:
        if event.end > duration + 0.05:
            raise AIError("Vision analysis returned an event beyond the clip duration")
    return analysis


def heuristic_review(plan: EditingPlan, duration: float, warning: str | None = None) -> Review:
    segment_count = len(plan.timeline)
    cuts_per_minute = segment_count / max(duration / 60, 0.25)
    has_caption = bool(plan.captions)
    pacing = 7.6 if 2 <= cuts_per_minute <= 15 else 6.2
    hook = 7.4 if plan.timeline[0].start > 0 or duration < 60 else 6.7
    variety = min(8.2, 6.2 + len([e for s in plan.timeline for e in s.effects]) * 0.15 + min(segment_count, 8) * 0.08)
    caption = 7.8 if has_caption else 6.5
    overall = round((hook + pacing + variety + caption + 7.4 + 7.2) / 6, 1)
    problems = []
    suggestions = []
    if cuts_per_minute < 1.5 and duration > 45:
        problems.append("The cut density may feel slow for YouTube pacing")
        suggestions.append("Review long sections and remove only genuinely unnecessary pauses")
    if not has_caption:
        problems.append("No captions are present")
        suggestions.append("Enable selective captions if the dialogue needs clarity or emphasis")
    if warning:
        problems.append("Visual AI review was unavailable; this score uses timeline heuristics")
    return Review(
        score=overall, hook=hook, pacing=pacing, clarity=7.4, narrative_flow=7.3,
        entertainment=7.1, visual_variety=round(variety, 1), audio=7.2,
        caption_quality=caption, ending=7.0, problems=problems, suggested_changes=suggestions,
    )


def self_review(
    plan: EditingPlan, duration: float, frame_paths: list[Path], project: dict[str, Any]
) -> tuple[Review, str | None]:
    settings = load_settings()
    if not get_api_key():
        return heuristic_review(plan, duration, "No API key configured"), "Used local timeline heuristics for self-review."
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": json.dumps({
            "task": "AI Self-Review. Evaluate the rendered edit using these ordered frames and plan. Be critical but calibrated.",
            "human_final_authority": True,
            "duration": duration,
            "plan_summary": plan.model_dump(mode="json"),
            "required_schema": Review.model_json_schema(),
        }, ensure_ascii=False),
    }]
    for path in frame_paths[:8]:
        if path.exists() and path.stat().st_size < 2_000_000:
            content.append({"type": "image_url", "image_url": {"url": _data_url(path), "detail": "low"}})
    system = (
        "You are performing an advisory AI Self-Review of a YouTube edit. Return only JSON matching the schema. "
        "Score 1-10: hook, pacing, clarity, narrative flow, entertainment, visual variety, audio, caption quality, "
        "ending, and overall. Do not claim to predict human enjoyment. Give concrete problems and changes."
    )
    try:
        raw = call_json([{"role": "system", "content": system}, {"role": "user", "content": content}], settings.vision_model)
        return Review.model_validate(raw), None
    except (AIError, ValidationError, ValueError) as exc:
        return heuristic_review(plan, duration, str(exc)), f"AI self-review failed; used local heuristics: {exc}"
