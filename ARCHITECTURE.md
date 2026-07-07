# GTA V Brainrot Shorts Pipeline — Architecture & Implementation Guide

## Overview

This project is a fully automated Python pipeline that creates viral **brainrot-style GTA V YouTube Shorts / Instagram Reels**. It downloads gameplay clips, extracts short segments via scene detection, generates a funny hook-first script using Groq LLM, synthesizes a voiceover with Edge TTS, and renders a 9:16 vertical video with kinetic word-by-word captions.

The pipeline can upload automatically to YouTube (via OAuth2 refresh token) and provides instructions for manual Instagram upload (since Instagram blocks automated API logins).

---

## Project Structure

```
GTA_VI_Automation/
├── config.py                # Central configuration (paths, API keys, settings)
├── download_clips.py        # Step 1: Download GTA V gameplay via yt-dlp
├── process_clips.py         # Step 2: Extract short clips via FFmpeg scene detection
├── generate_script.py       # Step 3: Generate brainrot script via Groq LLM
├── generate_voiceover.py    # Step 4: Synthesize voiceover via Edge TTS
├── render_short.py          # Step 5: Render 9:16 video with kinetic captions via FFmpeg
├── run_pipeline.py          # Orchestrator: runs all steps in sequence
├── upload_youtube.py        # Upload to YouTube Shorts via OAuth2
├── upload_instagram.py      # Manual upload guide (API login broken)
├── get_refresh_token.py     # Helper: generate YouTube OAuth refresh token
├── .env                     # Local config (API keys, credentials) — NEVER COMMIT
├── .env.example             # Template for .env
├── .gitignore               # Ignore .env, __pycache__, data/output/, data/raw/
├── requirements.txt         # Python dependencies
├── ARCHITECTURE.md          # This file
├── README.md                # Quick-start guide
└── data/
    ├── clips/               # Committed gameplay clips (tracked in git)
    ├── raw/                 # Downloaded raw videos (gitignored)
    ├── output/              # Generated voiceover.mp3, final_short.mp4 (gitignored)
    └── cache/               # Session files, download archives (gitignored)
```

---

## Data Flow

```
YouTube (yt-dlp)
    │
    ▼
data/raw/ (downloaded videos)
    │
    ▼ [FFmpeg scene detection]
data/clips/ (15-40s segments)
    │
    ▼ [random selection]
1 clip chosen
    │
    ├──► generate_script.py  ──► narration (40-65 words) + title + emphasis words
    │
    ├──► generate_voiceover.py ──► voiceover.mp3 + sentence timestamps
    │
    └──► render_short.py ──► final_short.mp4 (1080x1920, captions, music)
         │
         ├──► upload_youtube.py (automatic via OAuth2)
         └──► upload_instagram.py (manual upload guide)
```

---

## File-by-File Breakdown

### 1. `config.py` — Central Configuration

**Purpose:** Reads `.env` file, provides typed defaults for all configurable settings.

**Key variables:**

| Variable | Default | Description |
|---|---|---|
| `RAW_DIR` | `data/raw/` | Downloaded raw videos |
| `CLIPS_DIR` | `data/clips/` | Extracted short clips |
| `OUTPUT_DIR` | `data/output/` | Generated voiceover + rendered video |
| `CACHE_DIR` | `data/cache/` | Session files, archives |
| `YTDL_SEARCH_QUERY` | `"gta v funny moments gameplay 1080p"` | YouTube search query |
| `YTDL_MAX_DOWNLOADS` | `2` | Max videos per download run |
| `YTDL_FORMAT` | `bestvideo[height<=1080][ext=mp4]+bestaudio...` | yt-dlp format string |
| `SCENE_THRESHOLD` | `0.3` | FFmpeg scene detection sensitivity |
| `CLIP_MIN_DURATION` | `15` | Minimum clip length in seconds |
| `CLIP_MAX_DURATION` | `40` | Maximum clip length in seconds |
| `GROQ_API_KEY` | from `.env` | Groq LLM API key |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Groq model name |
| `TTS_VOICE` | `en-US-EricNeural` | Default Edge TTS voice |
| `YT_CLIENT_ID` | from `.env` | YouTube OAuth client ID |
| `YT_CLIENT_SECRET` | from `.env` | YouTube OAuth client secret |
| `YT_REFRESH_TOKEN` | from `.env` | YouTube OAuth refresh token |
| `IG_USERNAME` | from `.env` | Instagram username |
| `IG_PASSWORD` | from `.env` | Instagram password |

**How to add new config:**
```python
MY_SETTING = _env("MY_SETTING", "default_value")
```
Then add `MY_SETTING=value` to `.env`.

---

### 2. `download_clips.py` — Download Gameplay Videos

**Purpose:** Uses yt-dlp to search YouTube and download GTA V gameplay clips.

**Key function:**
```python
def download_fresh_clips() -> list[Path]:
```
- Searches YouTube using `config.YTDL_SEARCH_QUERY`
- Downloads up to `config.YTDL_MAX_DOWNLOADS` videos
- Saves to `data/raw/`
- Has 120-second timeout (yt-dlp can hang on cloud IPs)
- Tracks downloaded video IDs in `data/cache/archive.txt`

**CLI usage:**
```bash
python download_clips.py --query "gta v funny moments 1080p" --count 2
```

---

### 3. `process_clips.py` — Extract Short Clips

**Purpose:** Splits raw videos into short segments using FFmpeg's scene detection.

**Key functions:**
```python
def process_all_raw() -> list[Path]:
```
- Scans all files in `data/raw/`
- Uses FFmpeg `select='gt(scene,0.3)'` filter to detect scene changes
- Extracts clips between scene boundaries (15-40s range)
- Keeps the 10 largest clips (deletes the rest)
- Cleans up raw videos after processing

```python
def get_random_clip() -> Path | None:
```
- Picks a random clip from `data/clips/`

**CLI usage:**
```bash
python process_clips.py                           # Process all raw videos
python process_clips.py path/to/video.mp4          # Process a single video
```

---

### 4. `generate_script.py` — AI Script Generation

**Purpose:** Generates a funny brainrot script using Groq LLM with hook-first psychology.

**Returns:** `(narration: str, title: str, emphasis_words: list[str])`

**Script Structure (40-65 words total):**
- **HOOK** (5-10 words): Attention grabber — question or shocking statement
- **BODY** (25-45 words): Quick mini-story (setup → escalation)
- **PUNCHLINE** (5-10 words): Funny closing line
- **EMPHASIS** (2-3 words): ALL CAPS words from the script that get visual emphasis

**Key function:**
```python
def generate_brainrot_script(
    clip_description: str = "",
    style: str = "chaotic",
) -> tuple[str, str, list[str]]:
```

**Prompt Engineering:**
- System prompt defines the style (chaotic gamer, relatable, funny)
- User prompt enforces HOOK|BODY|PUNCHLINE|EMPHASIS|TITLE format
- NO emojis — plain text only for TTS compatibility
- Retries once on API error
- Falls back to hardcoded narration if both attempts fail

**Parser (`_parse_structured_response`):**
- Handles strict HOOK/BODY/PUNCHLINE/EMPHASIS/TITLE labels
- Falls back to free-form text if no labels found
- Generates title from HOOK if TITLE label missing
- Extracts emphasis words (ALL CAPS) as fallback
- Strips emojis from output

**Legacy compatibility:**
```python
def generate_brainrot_script_legacy(...) -> tuple[str, str]:
```
Returns `(narration, title)` without emphasis for backward compatibility.

**Style options:** `chaotic`, `meme`, `story`, `npc`

**CLI usage:**
```bash
python generate_script.py --style chaotic
```

---

### 5. `generate_voiceover.py` — Text-to-Speech

**Purpose:** Synthesizes the narration script into audio using Microsoft Edge TTS (free, no API key).

**Key function:**
```python
def synthesize_brainrot_voiceover(
    narration: str,
    output_path: Path | None = None,
    voice: str | None = None,
) -> tuple[float, list[SentenceTiming]]:
```
- Returns `(total_duration_seconds, sentence_timings)`
- `SentenceTiming` is `{"text": str, "offset_ms": int, "duration_ms": int}`
- Uses Edge TTS's `SentenceBoundary` events for word-level timing
- Timestamps are in 100-nanosecond units, converted to milliseconds via `// 10_000`
- 120-second timeout

**Available voices:** Ana, Andrew, Aria, Ava, Brian, Christopher, Emma, Eric, Guy, Jenny, Michelle, Roger, Steffan (en-US)

**CLI usage:**
```bash
python generate_voiceover.py --text "Your narration here"
```

---

### 6. `render_short.py` — Video Rendering

**Purpose:** Combines gameplay clip + voiceover into a 9:16 Short with kinetic captions.

**Key function:**
```python
def render(
    clip_path: Path,
    audio_path: Path,
    narration: str,
    output_path: Path | None = None,
    sentence_timings: list[dict] | None = None,
    style: str = "chaotic",
    emphasis_words: list[str] | None = None,
    video_title: str = "",
) -> Path:
```

**Render pipeline (FFmpeg filter graph):**
1. Scale clip to 1080x1920 (Lanczos upscale)
2. Crop to 9:16 aspect ratio
3. Apply unsharp filter for crisp visuals
4. Overlay subtitles (ASS format with kinetic styling)
5. Encode with libx264 at CRF 16-20, 6-12 Mbps

**Kinetic Caption System (`_build_ass_subtitles`):**
- **Title card:** Shows `video_title` at the top of the screen for the first 2.5 seconds
- **Normal words:** 90pt Impact font at alternating vertical positions (1200-1700px)
- **Emphasis words:** 130pt Impact font (1.5x larger), same alternating positions
- **Style-based color palettes:**
  - `chaotic`: Red/orange tones
  - `meme`: Green tones
  - `story`: White/silver tones
  - `npc`: Purple tones

**Cascading quality presets:**
1. `slow` preset, CRF 16, 12Mbps
2. `medium` preset, CRF 16, 10Mbps (fallback)
3. `fast` preset, CRF 20, 6Mbps (last resort)

Each has a 10-minute timeout. Uses `subprocess.DEVNULL` to avoid pipe buffer deadlocks. Uses relative ASS path (`data/output/captions.ass`) to avoid Windows drive colon parsing issues.

**CLI usage:**
```bash
python render_short.py --clip clip.mp4 --audio voiceover.mp3 --narration "text" --style chaotic --emphasis "WORD1,WORD2" --title "Video Title"
```

---

### 7. `run_pipeline.py` — Master Orchestrator

**Purpose:** Runs the entire pipeline end-to-end.

**Steps:**
1. Download gameplay clips (or skip if `--skip-download`)
2. Process raw videos into clips (or use pre-committed clips on CI)
3. Select a random clip
4. Generate brainrot script → narration, title, emphasis words
5. Clean narration (strip markdown, keep TTS-safe)
6. Synthesize voiceover with style-appropriate TTS voice
7. Render video with kinetic captions + title card
8. Upload to YouTube (if not `--no-upload`)
9. Print manual upload guide for Instagram

**CLI arguments:**
```
--no-upload          Skip YouTube/IG upload
--skip-download      Skip downloading new clips
--style              chaotic / meme / story / npc / random (default)
--privacy            public / unlisted / private
```

**Style-based TTS voice mapping:**
| Style | Voice | Character |
|---|---|---|
| chaotic | AndrewNeural | Deeper, dramatic |
| meme | AndrewNeural | Energetic |
| story | BrianNeural | Conversational |
| npc | GuyNeural | Calm storyteller |

**Safety features:**
- 40-minute global pipeline timeout (threading.Timer)
- Catches all subprocess timeouts gracefully
- Fallback clip generation if no clips available

---

### 8. `upload_youtube.py` — YouTube Upload

**Purpose:** Uploads the rendered video to YouTube Shorts.

**Key function:**
```python
def upload_short(
    video_path: Path,
    title: str,
    description: str = "",
    privacy: str = "public",
) -> str:
```

**Authentication (`_get_authenticated_service`):**
1. Loads cached token from `data/cache/yt_token.json`
2. If no cache, creates credentials from `YT_REFRESH_TOKEN` env var directly
3. Refreshes token if expired
4. Falls back to browser OAuth flow only as last resort
5. Saves refreshed token for next run

**Upload details:**
- Resumable upload (4MB chunks)
- Category: Gaming (ID 20)
- Tags: GTA VI, GTA 6, Gaming, Shorts, etc.
- Returns video ID on success

**To generate a refresh token:** Run `python get_refresh_token.py` locally.

---

### 9. `upload_instagram.py` — Instagram Upload Guide

**Purpose:** Instagram's API blocks automated logins (CSRF error), so this prints a manual upload guide.

**Behavior:**
- Prints the video path and caption
- Provides step-by-step instructions for uploading via the Instagram app or browser
- Returns empty string (no actual upload)

---

### 10. `get_refresh_token.py` — YouTube Auth Helper

**Purpose:** One-time script to generate a YouTube OAuth refresh token.

**Usage:**
```bash
python get_refresh_token.py
```
Opens a browser for OAuth consent, then prints the refresh token to add to `.env`.

---

## GitHub Actions CI/CD

The pipeline has a scheduled workflow (`.github/workflows/daily_brainrot.yml`) that runs twice daily.

**Key points:**
- Uses `--skip-download` because yt-dlp gets bot-blocked on GitHub runner IPs
- Relies on pre-committed clips in `data/clips/` (9 clips from 3 sources)
- YouTube upload works via OAuth refresh token from repo secrets
- Instagram upload is skipped on CI (prints manual guide)
- 45-minute timeout on the workflow itself

**Required GitHub Secrets:**
- `GROQ_API_KEY`
- `YT_CLIENT_ID`
- `YT_CLIENT_SECRET_VALUE`
- `YT_REFRESH_TOKEN`
- `IG_USERNAME`
- `IG_PASSWORD`

---

## Known Issues & Workarounds

### Instagram API Block
Instagram's API actively blocks automated logins even from consumer IPs. The `upload_instagram.py` prints a manual upload guide instead. **Workaround:** Upload `data/output/final_short.mp4` manually from your phone or browser.

### yt-dlp Bot Block on CI
YouTube blocks yt-dlp requests from cloud IPs (GitHub runners). **Workaround:** The CI uses `--skip-download` and relies on pre-committed clips.

### Windows Path Colons in FFmpeg
The `subtitles` filter in FFmpeg parses colons as option separators. A path like `D:/folder/file.ass` breaks the filter graph. **Fix:** Use a relative path (`data/output/captions.ass`) instead of an absolute path.

### FFmpeg Pipe Buffer Deadlock
`subprocess.run(cmd, capture_output=True, text=True)` with ffmpeg causes a deadlock because ffmpeg fills the stderr pipe buffer, blocking both processes. **Fix:** Use `stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL`.

### TTS Voice Name Validation
Edge TTS voice names must exactly match the available voices. Run `python -c "import edge_tts, asyncio; [print(v['Name']) for v in asyncio.get_event_loop().run_until_complete(edge_tts.list_voices()) if 'en-US' in v['Locale']]"` to list available voices.

---

## How to Extend

### Adding a New Style
1. Add the style name to `STYLES` in `run_pipeline.py`
2. Add a color palette in `render_short.py`'s `STYLE_PALETTES`
3. Add a TTS voice in `run_pipeline.py`'s `style_voices`
4. Add hook templates in `_build_description` if needed

### Adding a New LLM Provider
Replace `generate_script.py`'s Groq client with any OpenAI-compatible API. The function signature `(clip_description, style) -> (narration, title, emphasis_words)` is the contract.

### Adding a New TTS Provider
Replace `generate_voiceover.py`'s Edge TTS calls. The function must return `(duration_seconds, list_of_sentence_timings)` where each timing has `text`, `offset_ms`, `duration_ms`.

### Adding New Video Effects
Add more FFmpeg filters in `render_short.py`'s `_build_cmd()` function. The `[0:v]...subtitles=...` chain can be extended with `drawtext`, `fade`, `colorbalance`, etc.

---

## Dependencies

```
groq>=0.12.0              # LLM API client
python-dotenv>=1.0.0      # .env file loading
edge-tts>=6.1.0           # Free TTS
yt-dlp>=2024.0.0          # YouTube video download
instagrapi>=2.0.0         # Instagram API (upload broken)
google-auth-oauthlib>=1.0.0  # YouTube OAuth
google-auth-httplib2>=0.1.0
google-api-python-client>=2.100.0
```

System dependency: **FFmpeg** (must be installed and in PATH)