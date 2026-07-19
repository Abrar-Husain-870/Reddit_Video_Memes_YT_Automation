# Architectural Design: Reddit to Shorts Pipeline

This document details the software architecture of the Reddit-to-Shorts content pipeline. The system is designed to be highly modular, testable, and robust against network, API, and rendering failures.

---

## 🏗 Component Diagram

```mermaid
flowchart TD
    %% Component Layers
    subgraph Ingestion["1. Ingestion Layer"]
        crawler["Reddit Ingestor (src/reddit/client.py)"]
        reddit_json["Anonymous JSON Feed"]
        redlib["Redlib Proxy Instances"]
        reddit_praw["PRAW API Client"]
        reddit_rss["RSS Feed Ingestor"]
        dedup_db[("Processed Posts DB (data_video_bot/database/processed_reddit_posts.json)")]
    end

    subgraph Safety["Safety Layer (src/safety/analyzer.py)"]
        safety_analyzer["Content Safety Analyzer"]
        appeal_check["Meme Suitability Check"]
        female_check["Female Presence Check"]
        rejected_db[("Rejected Posts DB (data_video_bot/database/rejected_posts.json)")]
    end

    subgraph LLM["2. Narration Layer"]
        llm_factory["LLM Factory (src/narration/__init__.py)"]
        groq["Groq Client"]
        openai["OpenAI / DeepSeek / OpenRouter / Ollama"]
        gemini["Gemini Client"]
        fallback_script["Local Regex Cleanup (Fallback)"]
    end

    subgraph Audio["3. Voice Layer (TTS)"]
        tts_factory["TTS Factory (src/voice/__init__.py)"]
        edge["Edge TTS (Free)"]
        eleven["ElevenLabs API"]
        oai_tts["OpenAI TTS"]
        aligner["Character-Duration Aligner (src/voice/helpers.py)"]
    end

    subgraph Video["4. Compositing Layer"]
        bg_mgr["Background Selector (src/video/cat_selector.py / greenscreen.py)"]
        overlay_draw["Reddit Card Drawer (src/video/overlay.py)"]
        ffmpeg_comp["Video Renderer (src/video/renderer.py)"]
        ass_gen["Kinetic Subtitle Generator (ASS)"]
    end

    subgraph Upload["5. Distribution Layer"]
        yt_upload["YouTube Upload Manager (src/upload/youtube.py)"]
        schedule_chk["Scheduler Checker (src/upload/scheduler.py)"]
        upload_db[("Upload History DB (data_video_bot/database/upload_history.json)")]
    end

    %% Data Flow Connections
    subgraph Trigger["0. Scheduling Trigger"]
        cron["Cron / GitHub Actions / Task Scheduler"]
    end

    cron -->|Check schedule| schedule_chk
    schedule_chk -->|Query limit & slot| upload_db
    schedule_chk -->|Run permitted| crawler

    crawler -->|1. Fetch posts| reddit_json & redlib & reddit_praw & reddit_rss
    crawler -->|2. Check duplicates| dedup_db
    crawler -->|3. Validate Ingestion Safety| safety_analyzer
    
    safety_analyzer -->|Log Rejections| rejected_db
    safety_analyzer -->|4. Emit Valid Post| llm_factory

    llm_factory -->|5. Request Script| groq & openai & gemini
    llm_factory -.->|LLM Fail Fallback| fallback_script
    llm_factory -->|6. Validate Script Safety| safety_analyzer
    llm_factory -->|7. Emit Narration & Accent Words| tts_factory

    tts_factory -->|8. Generate Audio| edge & eleven & oai_tts
    eleven & oai_tts -->|9. Estimate word timings| aligner
    tts_factory -->|10. Emit Audio & Timing Data| ffmpeg_comp

    bg_mgr -->|11. Fetch clip (Reaction selection)| ffmpeg_comp
    overlay_draw -->|12. Draw Card PNG| ffmpeg_comp
    ffmpeg_comp -->|13. Generate ASS| ass_gen
    ffmpeg_comp -->|14. Compositing via FFmpeg| yt_upload

    yt_upload -->|15. Final Pre-Upload Safety| safety_analyzer
    yt_upload -->|16. Upload Short (OAuth)| upload_db
```

---

## 📂 Directory Layout

The application is structured as a modular Python package:

```text
Reddit-Memes-Automation-for-YT-Shorts/
│
├── config.py                 # Central Configuration Manager (reads from .env)
├── run_pipeline.py           # Core Master Pipeline Orchestrator (CLI Entry Point)
├── get_refresh_token.py      # OAuth Helper tool to fetch YT Refresh Tokens
├── test_curator_mode.py      # Integration testing script for Curator Mode rendering
├── requirements.txt          # Pip dependencies list
│
├── src/                      # Source Code Package
│   ├── __init__.py           # Package Initializer
│   ├── logger.py             # Structured Rotational Telemetry Logger
│   │
│   ├── reddit/               # Reddit Crawling & Verification Submodule
│   │   ├── models.py         # Structured RedditPost Dataclass
│   │   ├── providers.py      # Dual-ingestion strategy implementations (PRAW, RSS, Anonymous)
│   │   └── client.py         # Ingestion manager, download client, and historical tracking
│   │
│   ├── safety/               # Content Moderation Submodule
│   │   ├── __init__.py
│   │   └── analyzer.py       # Local Regex rules & vision/text LLM safety gates
│   │
│   ├── narration/            # LLM Narration Scripting Submodule
│   │   ├── __init__.py       # Provider Factory & Local Fallback cleanups
│   │   ├── base.py           # LLM Base Abstract Client Interface
│   │   ├── groq.py           # Groq Client wrapper
│   │   ├── gemini.py         # Gemini Client wrapper
│   │   ├── openai_like.py    # OpenAI, DeepSeek, OpenRouter & Ollama client wrapper
│   │   ├── prompts.py        # System Prompt templates for commentary / verbatim modes
│   │   └── helpers.py        # Script parsing, emoji & markdown stripping, emphasis tagger
│   │
│   ├── voice/                # Text-to-Speech Submodule
│   │   ├── __init__.py       # TTS Factory & Edge-TTS Fallback orchestration
│   │   ├── base.py           # Abstract Base Class for TTS engines
│   │   ├── edge.py           # Microsoft Edge TTS Client (sentence timing streams)
│   │   ├── elevenlabs.py     # ElevenLabs REST Client
│   │   ├── openai_tts.py     # OpenAI Speech REST Client
│   │   └── helpers.py        # Character-Duration alignment for REST providers
│   │
│   └── video/                # Video Compositing Submodule
│   │   ├── __init__.py       # Exposes selection, overlay drawing, and FFmpeg renderers
│   │   ├── background.py     # yt-dlp downloader, FFmpeg scene cutter, LFU clip picker
│   │   ├── cat_selector.py   # Adaptive selection logic for cat reaction overlays
│   │   ├── greenscreen.py    # Keyed overlay extractor for green screen reaction files
│   │   ├── overlay.py        # Pill-based high-res Reddit card image generator
│   │   └── renderer.py       # Compositing filter graph (blurs, card, progress bars, subtitles)
│   │
│   └── upload/               # Distribution & Scheduler Submodule
│       ├── __init__.py
│       ├── metadata.py       # LLM metadata generator for titles, tags, categories
│       ├── youtube.py        # Chunked resumable YouTube uploads & daily quota tracking
│       └── scheduler.py      # State-based time window slot parser
│
└── data_video_bot/           # Persistent App Data Directory (Auto-created)
    ├── raw/                  # Temp storage for downloaded raw meme videos
    ├── output/               # Rendered final shorts and subtitle assets
    ├── clips/                # Pre-sliced background video segments
    ├── cache/                # yt-dlp download logs & YouTube OAuth token cache
    └── database/             # App State Databases
        ├── processed_reddit_posts.json  # Crawled posts history (prevents duplicates)
        ├── used_backgrounds.json        # Least-Frequently-Used background counts
        ├── rejected_posts.json          # Blocked safety-violation posts registry
        └── upload_history.json          # YouTube uploaded IDs & daily limit tracker
```

---

## 🛡️ Content Safety Layer (4 Gates)

The system deploys a multi-phase safety guardrails analyzer (`ContentSafetyAnalyzer`) to protect the target publishing channel from policies/strikes:

1. **Stage 1 (Post Ingestion & Video Download)**: Analyzes the post content and downscaled video frames using local regexes (14 banned categories) + LLM context checks + Meme Suitability check (relatability, humor) + Watermark rejection + Female presence rejection (if configured).
2. **Stage 2 (Post Scripting)**: Scans the LLM-generated script, title, description, and tags to ensure no unsafe terminology was introduced.
3. **Stage 3 (Pre-Rendering)**: Re-validates all parameters before committing expensive rendering compute resources.
4. **Stage 4 (Pre-Upload)**: Ensures the final title, description, and metadata comply with advertiser policies immediately before the YouTube API upload request.

If any check fails, the post ID is logged to `rejected_posts.json`, and the orchestrator automatically fetches another post from the ingestion queue.

---

## 🎨 Rendering Engine Layouts

The rendering layer (`src/video/renderer.py`) supports two distinct operational modes:

### 1. Curator Mode (`CURATOR_MODE=True`)
* Preserves the original audio and video of the meme.
* Applies a blurred replica of the meme to fit a 1080x1920 portrait canvas.
* Places the scaled sharp meme in the center.
* Layers customizable text watermarks, fade in/out transitions, and progress bars.

### 2. Cat / Greenscreen Reaction Mode (`CURATOR_MODE=False`)
* Synthesizes AI script narration via edge-tts, ElevenLabs, or OpenAI.
* Layers a Minecraft/Subway Surfers gameplay clip or reaction video as a base.
* Overlays a PIL-generated Reddit dark mode post card in the center.
* Generates an Advanced SubStation Alpha (`captions.ass`) subtitle file with word-popping effects (`\fscx125` scaling).
* Muxes the narration audio (delayed by 2.0 seconds) with the meme's original audio track at 15% volume.
* If a greenscreen reaction is selected, the chromakey filter (`chromakey=color=0x00B140`) chromatically removes the background on-the-fly and overlays the reaction clip dynamically without overlap.