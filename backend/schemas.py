from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=10000)
    style: str = Field(default="Clean, engaging YouTube edit", max_length=1000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=10000)
    style: str | None = Field(default=None, max_length=1000)


class ClipRename(BaseModel):
    name: str = Field(min_length=1, max_length=180)


class ClipOrder(BaseModel):
    clip_ids: list[str] = Field(min_length=1)


class SpeechItem(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class OCRItem(BaseModel):
    time: float = Field(ge=0)
    text: str


class AnalysisEvent(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    description: str = Field(min_length=1, max_length=1000)
    importance: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def times_are_valid(self):
        if self.end <= self.start:
            raise ValueError("event end must be after start")
        return self


class ClipVisionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visual_summary: str = Field(default="", max_length=4000)
    events: list[AnalysisEvent] = Field(default_factory=list, max_length=50)
    hook_candidates: list[str] = Field(default_factory=list, max_length=10)
    ending_candidates: list[str] = Field(default_factory=list, max_length=10)


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["zoom", "fade", "speed", "text"]
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, ge=0)
    scale: float | None = Field(default=None, ge=1, le=2)
    value: float | None = Field(default=None, ge=0.25, le=4)
    text: str | None = Field(default=None, max_length=500)


class TimelineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    effects: list[Effect] = Field(default_factory=list, max_length=20)
    transition: Literal["cut", "fade"] = "cut"

    @model_validator(mode="after")
    def times_are_valid(self):
        if self.end <= self.start:
            raise ValueError("segment end must be after start")
        return self


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=500)
    style: Literal["default", "emphasis", "subtle"] = "default"

    @model_validator(mode="after")
    def times_are_valid(self):
        if self.end <= self.start:
            raise ValueError("caption end must be after start")
        return self


class MusicCue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, ge=0)
    volume: float = Field(default=0.12, ge=0, le=1)
    duck_during_dialogue: bool = True


class SoundCue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    time: float = Field(ge=0)
    volume: float = Field(default=0.5, ge=0, le=2)


class VisualOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    mode: Literal["picture_in_picture", "fullscreen"] = "picture_in_picture"
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"] = "bottom_right"
    opacity: float = Field(default=1, ge=0.2, le=1)

    @model_validator(mode="after")
    def times_are_valid(self):
        if self.end <= self.start:
            raise ValueError("visual overlay end must be after start")
        return self


class EditingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    rationale: str = Field(default="", max_length=5000)
    timeline: list[TimelineSegment] = Field(min_length=1, max_length=1000)
    captions: list[Caption] = Field(default_factory=list, max_length=5000)
    visual_overlays: list[VisualOverlay] = Field(default_factory=list, max_length=20)
    music: list[MusicCue] = Field(default_factory=list, max_length=20)
    sound_effects: list[SoundCue] = Field(default_factory=list, max_length=100)


class Review(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: float = Field(ge=1, le=10)
    hook: float = Field(ge=1, le=10)
    pacing: float = Field(ge=1, le=10)
    clarity: float = Field(ge=1, le=10)
    narrative_flow: float = Field(default=7, ge=1, le=10)
    entertainment: float = Field(ge=1, le=10)
    visual_variety: float = Field(ge=1, le=10)
    audio: float = Field(ge=1, le=10)
    caption_quality: float = Field(default=7, ge=1, le=10)
    ending: float = Field(ge=1, le=10)
    problems: list[str] = Field(default_factory=list, max_length=20)
    suggested_changes: list[str] = Field(default_factory=list, max_length=20)
    advisory_note: str = "AI Self-Review is advisory. You have final approval."


class RenderRequest(BaseModel):
    instructions: str = Field(default="", max_length=10000)
    feedback: str = Field(default="", max_length=10000)
    use_ai: bool = True
    include_captions: bool = True
    auto_retry: bool = False


class VersionFeedback(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=10)
    feedback: str = Field(default="", max_length=10000)


class AssetCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    category: Literal["music", "images", "sound_effects", "video", "other"] = "other"
    source_url: str = Field(default="", max_length=4000)
    source_name: str = Field(default="User upload", max_length=500)
    license: str = Field(default="User supplied", max_length=1000)
    usage_notes: str = Field(default="", max_length=2000)


class AssetDownload(BaseModel):
    url: str = Field(max_length=4000)
    filename: str = Field(min_length=1, max_length=255)
    category: Literal["music", "images", "sound_effects", "video", "other"] = "other"
    source_url: str = Field(max_length=4000)
    source_name: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=1000)
    usage_notes: str = Field(default="", max_length=2000)

    @field_validator("url", "source_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Only HTTPS URLs are accepted")
        return value


class SettingsUpdate(BaseModel):
    provider: str
    api_base_url: str
    api_key: str | None = None
    model: str
    vision_model: str
    stt_model: str
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=256, le=100000)
    frame_interval: int = Field(ge=2, le=120)
    output_width: int = Field(ge=320, le=7680)
    output_height: int = Field(ge=240, le=4320)
    output_fps: int = Field(ge=12, le=120)
    max_auto_retries: int = Field(ge=0, le=10)
    use_local_whisper: bool = True
    whisper_model: str = "small"
    enable_ocr: bool = True
    enable_vision_analysis: bool = True
    burn_captions: bool = True
