from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SECRETS_FILE = CONFIG_DIR / "secrets.json"


def _resolved_dir(env_name: str, default: str) -> Path:
    value = Path(os.getenv(env_name, default)).expanduser()
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


PROJECTS_DIR = _resolved_dir("PROJECTS_DIR", "projects")
TEMP_DIR = ROOT / "temp"
OUTPUT_DIR = ROOT / "output"
LIBRARY_DIR = ROOT / "assets" / "library"


class AppSettings(BaseModel):
    provider: str = "openai-compatible"
    api_base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    vision_model: str = "gpt-4.1-mini"
    stt_model: str = "whisper-1"
    temperature: float = Field(0.35, ge=0, le=2)
    max_tokens: int = Field(6000, ge=256, le=100000)
    frame_interval: int = Field(12, ge=2, le=120)
    output_width: int = Field(1920, ge=320, le=7680)
    output_height: int = Field(1080, ge=240, le=4320)
    output_fps: int = Field(30, ge=12, le=120)
    max_auto_retries: int = Field(3, ge=0, le=10)
    use_local_whisper: bool = True
    whisper_model: str = "small"
    enable_ocr: bool = True
    enable_vision_analysis: bool = True
    burn_captions: bool = True
    api_key_configured: bool = False

    @field_validator("api_base_url")
    @classmethod
    def validate_base(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("API base URL must start with http:// or https://")
        return value


ENV_MAP = {
    "provider": "AI_PROVIDER",
    "api_base_url": "AI_API_BASE",
    "model": "AI_MODEL",
    "vision_model": "AI_VISION_MODEL",
    "stt_model": "AI_STT_MODEL",
    "temperature": "AI_TEMPERATURE",
    "max_tokens": "AI_MAX_TOKENS",
}


def ensure_directories() -> None:
    for path in (CONFIG_DIR, PROJECTS_DIR, TEMP_DIR, OUTPUT_DIR, LIBRARY_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_settings() -> AppSettings:
    ensure_directories()
    data: dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            data.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    for field, env in ENV_MAP.items():
        if os.getenv(env) not in (None, ""):
            data[field] = os.environ[env]
    settings = AppSettings.model_validate(data)
    settings.api_key_configured = bool(get_api_key())
    return settings


def save_settings(data: dict[str, Any], api_key: str | None = None) -> AppSettings:
    ensure_directories()
    data.pop("api_key_configured", None)
    validated = AppSettings.model_validate(data)
    public = validated.model_dump(exclude={"api_key_configured"})
    SETTINGS_FILE.write_text(json.dumps(public, indent=2), "utf-8")
    if api_key is not None:
        if api_key.strip():
            SECRETS_FILE.write_text(json.dumps({"api_key": api_key.strip()}), "utf-8")
            try:
                SECRETS_FILE.chmod(0o600)
            except OSError:
                pass
        elif SECRETS_FILE.exists():
            SECRETS_FILE.unlink()
    return load_settings()


def get_api_key() -> str | None:
    env_key = os.getenv("AI_API_KEY", "").strip()
    if env_key:
        return env_key
    if SECRETS_FILE.exists():
        try:
            return str(json.loads(SECRETS_FILE.read_text("utf-8")).get("api_key", "")).strip() or None
        except (json.JSONDecodeError, OSError):
            return None
    return None


def binary_path(kind: str) -> str | None:
    env_name = "FFMPEG_PATH" if kind == "ffmpeg" else "FFPROBE_PATH"
    configured = os.getenv(env_name, kind)
    if Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser().resolve())
    return shutil.which(configured)
