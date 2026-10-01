# AI Video Editor Agent

A complete local-first web application for turning ordered source clips into reviewable YouTube-style edits. The interface is branded **Cutwise**, but the project and API are the **AI Video Editor Agent**.

The application does real work: uploads are stored on disk, FFmpeg probes and renders media, analysis is cached by source hash, a configurable OpenAI-compatible model can produce strictly validated edit plans, rendered versions are immutable, and no version becomes final without human approval.

> **Core rule:** the user owns the narrative order. The AI may trim or split a clip, but backend validation rejects any plan that puts a later clip before an earlier clip.

## What works

- Local FastAPI web app with a responsive HTML/CSS/JavaScript interface
- Multiple video upload, thumbnails, previews, rename/remove, and drag-to-reorder
- Explicit locked-order validation in both the API and editing-plan validator
- FFprobe metadata: duration, size, resolution, FPS, codecs, audio presence, bitrate
- Representative frame extraction and scene-change detection
- Audio extraction to 16 kHz mono WAV
- Optional local `faster-whisper` or remote OpenAI-compatible transcription
- Optional Tesseract OCR on bounded representative frames
- Vision-model footage analysis using at most eight low-resolution representative frames per clip, producing visual summaries, concrete events, and hook/ending candidates
- Source-hash and analysis-profile caching; unchanged footage is not re-analyzed
- Configurable OpenAI-compatible editing and vision models
- Strict Pydantic schemas and safe fallback when AI JSON is invalid
- Local smart plan when no API key is configured
- Real FFmpeg rendering with source trims, order-preserving cuts, normalization, portrait/landscape padding, frame-rate normalization, missing-audio generation, speed changes, timed zooms, fades, text overlays, ASS captions, music mixing, sound effects, and H.264/AAC MP4 output
- Advisory AI Self-Review, with a calibrated local heuristic fallback
- Human rating, feedback, rejection/regeneration, accept-anyway, and final selection
- Bounded automatic retries for scores below 5/10 (off by default; default maximum 3)
- Immutable `v001`, `v002`, ... directories; old versions are never overwritten
- Project asset manager with upload, preview, license/source metadata, usage tracking, protected removal, and a reusable cross-project library
- Reuse-friendly Wikimedia Commons search and controlled HTTPS downloads
- Progress jobs for analysis and rendering; the browser remains responsive
- Useful handling for missing binaries, unavailable optional models, invalid media, AI/network errors, malformed plans, and render failures

## Honest first-release boundaries

The core end-to-end workflow is implemented. A few advanced expansion points are deliberately not presented as finished:

- The visual timeline is a plan inspector, not a frame-accurate drag-to-trim NLE.
- Licensed web search is user-triggered through a controlled Wikimedia tool. The LLM cannot autonomously browse arbitrary sites or auto-download assets. This keeps licensing and download approval human-visible.
- Image assets can be used by AI plans as timed fullscreen cutaways or picture-in-picture overlays. Uploaded video assets are managed and preserved, but automatic secondary-video B-roll composition is not yet in the edit-plan schema.
- Self-review sends selected low-resolution output frames and structured plan context to a configured vision model. It is advisory and is not an objective prediction of enjoyment.
- Job state is in memory; completed project/version state is durable on disk. Restarting the server clears only the transient job list.

These boundaries correspond to the advanced Phase 6/7 work in the brief; they do not block upload → analysis → plan → render → self-review → feedback → regenerate → approve.

## Architecture

```text
Browser SPA (frontend/)
        │ JSON + multipart
        ▼
FastAPI controlled API (backend/app.py)
        ├── project storage       backend/storage.py
        ├── media analysis        backend/analyzer.py
        ├── FFmpeg/FFprobe        backend/media.py
        ├── AI adapter + schemas  backend/ai_client.py
        ├── validated renderer    backend/renderer.py
        ├── licensed assets       backend/assets.py
        └── bounded worker jobs   backend/jobs.py

AI → strict EditingPlan → project/order validation → app-built FFmpeg args → MP4
```

The LLM never manipulates video bytes, supplies shell commands, or submits raw FFmpeg expressions. Effects are modeled as bounded typed fields and converted into command arguments by application code.

## Requirements

- Python 3.11–3.14 (dependency pins include CPython 3.14 wheels)
- FFmpeg **and FFprobe** available on `PATH`
- A modern browser
- Optional: an API key for an OpenAI-compatible provider
- Optional: Tesseract and `faster-whisper` for fully local OCR/transcription

### Install FFmpeg

**macOS**

```bash
brew install ffmpeg tesseract
```

**Ubuntu/Debian**

```bash
sudo apt update
sudo apt install ffmpeg tesseract-ocr
```

**Windows**

Install an FFmpeg build from <https://ffmpeg.org/download.html>, add its `bin` directory to `PATH`, and optionally install Tesseract. Confirm both commands work:

```bash
ffmpeg -version
ffprobe -version
```

If binaries live elsewhere, set `FFMPEG_PATH` and `FFPROBE_PATH` in `.env`.

## Install and run

```bash
git clone <repository-url>
cd volunteera-OS
python -m venv .venv

# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
cp .env.example .env       # Windows: copy .env.example .env
python run.py
```

Open <http://localhost:8000>. The server binds to `0.0.0.0` by default; override with `HOST` or `PORT` in the environment.

Optional local speech/OCR Python integrations:

```bash
pip install -r requirements-optional.txt
```

`faster-whisper` downloads the selected model on first use. Tesseract itself is a separate system package. If either optional integration is absent, analysis continues, records a visible warning, and keeps all other results.

## First edit

1. Open **Projects** and create a project (or use the included copyright-free generated example project).
2. Upload clips in the intended narrative order.
3. Drag clips to confirm the exact order. The order is saved immediately.
4. Click **Analyze**. Metadata, frames, scenes, audio, transcript/OCR where available, and structured events are cached under `analysis/`.
5. Add project instructions and choose whether to use the configured model.
6. Click **Generate edit**. The app validates the plan before starting FFmpeg.
7. Watch the rendered MP4 and inspect **AI Self-Review**.
8. Accept it, rate it, give feedback and regenerate, or keep editing.
9. Accepting any old or new version copies it into `final/` and marks it as the human-selected final.

Without an API key, steps 5–9 still work using the conservative local smart plan and local self-review heuristic. The smart plan only removes transcript-confirmed long gaps; when transcript data is unavailable it preserves the complete clip rather than making destructive guesses.

## AI configuration

Use **AI Settings** in the app, or copy `.env.example` to `.env`:

```dotenv
AI_PROVIDER=openai-compatible
AI_API_BASE=https://api.openai.com/v1
AI_API_KEY=
AI_MODEL=gpt-4.1-mini
AI_VISION_MODEL=gpt-4.1-mini
AI_STT_MODEL=whisper-1
AI_TEMPERATURE=0.35
AI_MAX_TOKENS=6000
```

The adapter calls these OpenAI-compatible endpoints:

- `POST {AI_API_BASE}/chat/completions`
- `POST {AI_API_BASE}/audio/transcriptions` when remote transcription is selected

Provider support for JSON response mode and image message content is recommended. If JSON mode is rejected, the adapter retries without that parameter and still strictly validates the returned object.

Keys are never hard-coded or returned by the API. Environment variables take precedence. If a key is saved from the UI, it is stored in ignored `config/secrets.json`; the app requests user-only (`0600`) permissions where supported. For higher-security environments, use `AI_API_KEY` from a process-level secret manager instead.

## On-disk project format

```text
projects/my-project/
├── input/                  # original accepted clips
├── analysis/
│   └── <clip-id>/
│       ├── analysis.json
│       ├── audio.wav
│       └── frames/
├── assets/
├── music/
├── images/
├── sound_effects/
├── downloaded/
├── versions/
│   ├── v001/
│   │   ├── video.mp4
│   │   ├── edit_plan.json
│   │   ├── self_review.json
│   │   ├── version.json
│   │   ├── captions.ass        # when captions exist
│   │   ├── review_frames/
│   │   └── work/               # retained render intermediates
│   └── v002/
├── final/
│   └── v002.mp4
├── trash/                  # explicit removals are preserved here
└── project.json
```

Project JSON files are the durable storage system. This avoids a hidden database and makes backup, inspection, and migration straightforward. Writes use temporary files and atomic replacement.

## Editing-plan safety

`backend/schemas.py` rejects unknown fields and invalid ranges. `validate_plan_for_project()` additionally enforces:

- only known clip IDs and asset IDs
- nondecreasing user clip order
- chronological source segments inside each clip
- source times within probed clip duration
- minimum segment duration
- bounded effects and cue values

Malformed model output never reaches the renderer. It is recorded as a warning and replaced by a conservative local plan.

## Asset and download security

- Search uses Wikimedia Commons' API and only surfaces results with reuse terms.
- A user explicitly chooses an asset before download.
- Downloads require HTTPS, reject credentials in URLs, resolve DNS, and block private, loopback, link-local, and reserved destinations.
- Every redirect is revalidated.
- Downloads are streamed with a configured byte limit.
- Source URL, provider, license, download date, notes, and version usage are stored.
- AI-generated shell commands are never executed.
- Files are constrained to the selected project directory; path traversal filenames are sanitized.
- Assets used in rendered versions cannot be removed through the API.

Users remain responsible for confirming that a license fits their intended use and for satisfying attribution requirements.

## Environment options

See `.env.example`. Important values include:

| Variable | Default | Purpose |
|---|---:|---|
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable/path |
| `FFPROBE_PATH` | `ffprobe` | FFprobe executable/path |
| `PROJECTS_DIR` | `projects` | Durable project root |
| `MAX_UPLOAD_GB` | `20` | Per-video upload limit |
| `ASSET_DOWNLOAD_MAX_MB` | `500` | Controlled remote asset limit |
| `HOST` | `0.0.0.0` | Web bind address |
| `PORT` | `8000` | Web port |

Output size/FPS, frame sampling, OCR, Whisper model, caption burning, and retry limits are configurable in the UI.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall backend run.py
```

The core tests cover local plan generation, caption mapping, clip-order rejection, duration bounds, and filename safety. The implementation was also exercised end-to-end with generated landscape/audio and portrait/no-audio clips through upload, analysis, rendering, self-review, MP4 retrieval, and human acceptance.

## Troubleshooting

- **`pydantic-core` fails to build on Python 3.14:** pull the latest branch and confirm `requirements.txt` uses `pydantic==2.12.5`. Remove the partially created `.venv`, create it again, upgrade pip, and reinstall. This version has a prebuilt CPython 3.14 wheel and does not require a local Rust build.
- **A render was interrupted and left `versions/v001/` behind:** retry the render. Version allocation checks both `project.json` and existing directories, preserves the incomplete folder, and automatically advances to `v002` rather than overwriting anything.
- **FFmpeg setup needed:** run `ffmpeg -version` and `ffprobe -version` in the same terminal used for `python run.py`, or set absolute paths in `.env`.
- **Transcription skipped:** install `faster-whisper`, or disable local transcription and configure a provider/key that supports `/audio/transcriptions`.
- **OCR skipped:** install the Tesseract system executable and `pytesseract`.
- **Captions are sidecar-only:** the FFmpeg build lacks the `subtitles`/libass filter. The app preserves `captions.ass` and reports the fallback.
- **AI provider rejects JSON mode:** the adapter retries once without `response_format`; the result must still validate.
- **A remote asset is blocked:** only public HTTPS hosts are permitted. Download it yourself, verify its rights, and use project asset upload.
- **Insufficient space:** each immutable version intentionally keeps its MP4 and render work files. Back up projects before manually removing work intermediates.

## License

No license has been supplied for this repository. Third-party media added through the application retains its own recorded license and attribution requirements.
