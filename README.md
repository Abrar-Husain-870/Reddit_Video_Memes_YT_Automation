# Reddit to YouTube Shorts / Reels Automation System

A fully automated, modular Python pipeline that crawls posts from subreddits or RSS feeds, performs multi-phase content safety moderation, and renders high-quality 9:16 vertical Shorts at 60 FPS. The system is designed to work out-of-the-box for both local development and autonomous AI agents (like Antigravity, Cursor, or Claude) to build on top of.

---

## 🚀 Key Features

* **Dual Operational Modes**:
  * **Curator Mode (Default)**: Automatically curates video memes from Reddit, letterboxes/pads them with blurred backgrounds, applies subtle branding overlays and smooth fade transitions, and publishes them keeping the original video's audio.
  * **Normal (Commentary) Mode**: Generates AI scripts, synthesizes voiceovers, overlays PIL-rendered dark mode Reddit post cards on top of gameplay backgrounds, and burns bouncy, word-highlighting captions (ASS subtitles).
* **Robust Ingestion Chain (Credential-Free Fallback)**:
  * Prioritizes **Reddit PRAW API** if credentials are set.
  * Falls back to rotated **Redlib proxy instances** and **Anonymous JSON Feeds** to bypass 403 rate-limits and GitHub Actions/CI IP blocks.
  * Supports credential-free **RSS Ingestion** (from RSS.app or direct subreddit feeds) to fetch media links.
* **4-Stage Content Safety Gatekeeper**:
  * **Stage 1 (Ingestion)**: Local regex filter (14 prohibited categories) + LLM context evaluation + appeal/relatability suitability score + watermark detector + female presence rejection.
  * **Stage 2 (Scripting)**: Scans generated narrative text, titles, descriptions, and tags.
  * **Stage 3 (Pre-Rendering)**: Final validation check before executing FFmpeg.
  * **Stage 4 (Pre-Upload)**: Final validation before transmitting to YouTube's publishing API.
* **Greenscreen & Reaction Layering**:
  * Extract and key out greenscreen reaction elements (`chromakey` filtering on-the-fly) and lay them over the main content zone adaptively to prevent overlap.
* **Automated YouTube Distribution**:
  * OAuth2 authorization helper and chunked resumable upload script.
  * Scheduler parser that automatically restricts publishing to pre-defined daily slots (e.g. 4 times a day) based on history logs.

---

## 🛠 Setup & Installation

### Prerequisites
* **Python 3.12 or 3.13**
* **FFmpeg** (installed and added to your system's PATH)

### Installation
1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd Reddit-Memes-Automation-for-YT-Shorts
   ```
2. Initialize virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in your keys and preferences:
```bash
cp .env.example .env
```

### Key Config Options
| Variable | Default / Example | Description |
|---|---|---|
| `CURATOR_MODE` | `True` | `True` to compile video memes directly; `False` for AI commentary mode |
| `SUBREDDITS` | `memes, dankmemes, me_irl` | Comma-separated target subreddits |
| `RSS_ENABLED` | `False` | Enable fallback/credential-free RSS crawling |
| `GEMINI_API_KEY` | `""` | Required for LLM Safety checks and Watermark Rejection |
| `ENABLE_CONTENT_SAFETY` | `True` | Run the 4-stage safety evaluation pipeline |
| `SAFETY_MODE` | `strict` | Content safety mode (`strict`, `standard`, `lenient`) |
| `REJECT_FEMALE_HUMANS`| `True` | Rejects videos containing female human presence |
| `YT_REFRESH_TOKEN` | `""` | YouTube OAuth2 refresh token for publishing |
| `MAX_VIDEOS_PER_DAY` | `4` | Limit on daily published videos |

---

## 🎮 Usage

### Run End-to-End Pipeline
Execute the full pipeline orchestrator with a single command:
```bash
python run_pipeline.py
```

### Command Line Flags
* **Bypass Scheduler**: Force run immediately regardless of scheduled hours:
  ```bash
  python run_pipeline.py --force
  ```
* **Skip YouTube Upload**: Generate and render video locally without uploading:
  ```bash
  python run_pipeline.py --no-upload
  ```
* **Target a Subreddit**: Fetch only from a specific subreddit for this run:
  ```bash
  python run_pipeline.py --subreddit dankmemes
  ```
* **Style Captions (Normal Mode)**: Choose caption styling preset (`chaotic` [orange], `meme` [green], `story` [white], `npc` [purple]):
  ```bash
  python run_pipeline.py --style meme
  ```
* **Skip Background Download**: Use pre-existing local clips instead of fetching new ones:
  ```bash
  python run_pipeline.py --skip-download
  ```

### Verification & Testing
To verify the Curator Mode video rendering engine locally, run the integration test script:
```bash
python test_curator_mode.py
```
This script downloads a sample video from GitHub, passes it through ingestion filters, and renders `test_curator_final.mp4` under `data_video_bot/output/`.

### YouTube Authentication Setup
Generate a YouTube OAuth refresh token locally:
```bash
python get_refresh_token.py
```
This opens a local browser server to auth with Google and prints the credentials to paste into your `.env` file under `YT_REFRESH_TOKEN`.