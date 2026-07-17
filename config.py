"""
Central configuration for the Reddit to Shorts Automation Pipeline.
Reads from .env file, provides comprehensive default values.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

# Load local environment file
load_dotenv(ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    """Helper to fetch config from environment."""
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    return val


def _env_bool(key: str, default: bool = False) -> bool:
    """Helper to fetch boolean config from environment."""
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


# ── Directories ──────────────────────────────────────────────
DATA_DIR_NAME = _env("DATA_DIR_NAME", "data_video_bot")
RAW_DIR = ROOT / DATA_DIR_NAME / "raw"                 # Raw background videos
CLIPS_DIR = ROOT / DATA_DIR_NAME / "clips"             # Sliced background clips
OUTPUT_DIR = ROOT / DATA_DIR_NAME / "output"           # Rendered assets/short
CACHE_DIR = ROOT / DATA_DIR_NAME / "cache"             # Session tokens/archives
DB_DIR = ROOT / DATA_DIR_NAME / "database"             # Processing history, database files

for d in [RAW_DIR, CLIPS_DIR, OUTPUT_DIR, CACHE_DIR, DB_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Database Files
HISTORY_FILE = DB_DIR / "processed_reddit_posts.json"
UPLOAD_HISTORY_FILE = DB_DIR / "upload_history.json"
BACKGROUND_HISTORY_FILE = DB_DIR / "used_backgrounds.json"

# ── Reddit Ingestion Settings ────────────────────────────────
SUBREDDITS = [
    s.strip()
    for s in _env(
        "SUBREDDITS",
        "memes, dankmemes, me_irl, funny, wholesomememes, AdviceAnimals, blursedimages, perfectlycutscreams, AnimalsBeingDerps, cats, dogs, meme, meirl, MemeVideos",
    ).split(",")
    if s.strip()
]

# Configurable selection weights for each subreddit
SUBREDDIT_WEIGHTS = {}
for s in SUBREDDITS:
    SUBREDDIT_WEIGHTS[s] = 1.0

# High priority subreddits get configured weights
_preferred_weights = {
    "memes": 5.0,
    "dankmemes": 4.0,
    "me_irl": 4.0,
    "funny": 3.0,
    "wholesomememes": 5.0,
    "AdviceAnimals": 3.0,
    "blursedimages": 2.0,
    "perfectlycutscreams": 3.0,
    "AnimalsBeingDerps": 4.0,
    "cats": 4.0,
    "dogs": 4.0,
    "MemeVideos": 5.0,
}
for s, w in _preferred_weights.items():
    if s in SUBREDDIT_WEIGHTS:
        SUBREDDIT_WEIGHTS[s] = float(_env(f"SUBREDDIT_WEIGHT_{s.upper()}", str(w)))

REDDIT_SORT = _env("REDDIT_SORT", "top")  # top, hot, rising, new
REDDIT_TIME_FILTER = _env("REDDIT_TIME_FILTER", "week")  # day, week, month, year, all
REDDIT_MIN_SCORE = int(_env("REDDIT_MIN_SCORE", "100"))
REDDIT_MIN_COMMENTS = int(_env("REDDIT_MIN_COMMENTS", "10"))
REDDIT_FILTER_NSFW = _env_bool("REDDIT_FILTER_NSFW", True)
REDDIT_FILTER_DELETED = _env_bool("REDDIT_FILTER_DELETED", True)
REDDIT_FILTER_PINNED = _env_bool("REDDIT_FILTER_PINNED", True)
REDDIT_FILTER_CROSSPOSTS = _env_bool("REDDIT_FILTER_CROSSPOSTS", True)
REDDIT_POST_MIN_LEN = int(_env("REDDIT_POST_MIN_LEN", "50"))
REDDIT_POST_MAX_LEN = int(_env("REDDIT_POST_MAX_LEN", "1500"))

# Optional PRAW Reddit Credentials (if empty, uses anonymous JSON feeds)
REDDIT_CLIENT_ID = _env("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = _env("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = _env("REDDIT_USER_AGENT", "RedditShortsBot/1.0")

# ── AI Narration Settings ────────────────────────────────────
NARRATION_MODE = _env("NARRATION_MODE", "commentary")  # natural, commentary
LLM_PROVIDER = _env("LLM_PROVIDER", "")
if not LLM_PROVIDER:
    if os.environ.get("GEMINI_API_KEY"):
        LLM_PROVIDER = "gemini"
    else:
        LLM_PROVIDER = "groq"
LLM_MODEL = _env("LLM_MODEL", "")  # Autoresolved below if empty

# Provider Keys & Custom API Base URLs
GROQ_API_KEY = _env("GROQ_API_KEY", "")
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
OPENAI_API_KEY = _env("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "")
OLLAMA_API_URL = _env("OLLAMA_API_URL", "http://localhost:11434/v1")

# Resolve default model names if none is provided
if not LLM_MODEL:
    if LLM_PROVIDER == "groq":
        LLM_MODEL = "llama-3.1-8b-instant"
    elif LLM_PROVIDER == "deepseek":
        LLM_MODEL = "deepseek-chat"
    elif LLM_PROVIDER == "gemini":
        LLM_MODEL = "gemini-2.0-flash"
    elif LLM_PROVIDER == "openai":
        LLM_MODEL = "gpt-4o-mini"
    elif LLM_PROVIDER == "openrouter":
        LLM_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
    elif LLM_PROVIDER == "ollama":
        LLM_MODEL = "llama3"
    else:
        LLM_MODEL = "llama-3.1-8b-instant"

# ── Voice / TTS Settings ─────────────────────────────────────
TTS_PROVIDER = _env("TTS_PROVIDER", "edge")  # edge, elevenlabs, openai, azure, fish, xtts
TTS_VOICE = _env("TTS_VOICE", "")  # Autoresolved based on provider below if empty

ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel

OPENAI_TTS_MODEL = _env("OPENAI_TTS_MODEL", "tts-1")
AZURE_TTS_KEY = _env("AZURE_TTS_KEY", "")
AZURE_TTS_REGION = _env("AZURE_TTS_REGION", "eastus")
FISH_AUDIO_API_KEY = _env("FISH_AUDIO_API_KEY", "")
FISH_AUDIO_VOICE_ID = _env("FISH_AUDIO_VOICE_ID", "")
XTTS_API_URL = _env("XTTS_API_URL", "http://localhost:8020")
XTTS_SPEAKER_WAV = _env("XTTS_SPEAKER_WAV", "")  # Path to speaker reference wave

# Resolve default voice identifiers
if not TTS_VOICE:
    if TTS_PROVIDER == "edge":
        TTS_VOICE = "en-US-AndrewNeural"
    elif TTS_PROVIDER == "openai":
        TTS_VOICE = "onyx"  # alloy, echo, fable, onyx, nova, shimmer
    elif TTS_PROVIDER == "azure":
        # Azure XML format
        TTS_VOICE = "en-US-AndrewNeural"
    elif TTS_PROVIDER == "elevenlabs":
        TTS_VOICE = ELEVENLABS_VOICE_ID
    else:
        TTS_VOICE = "en-US-AndrewNeural"

# ── Background Video Settings ────────────────────────────────
BACKGROUND_PROVIDERS = [
    p.strip()
    for p in _env(
        "BACKGROUND_PROVIDERS",
        "minecraft parkour gameplay 1080p, subway surfers gameplay 1080p, satisfying videos 1080p",
    ).split(",")
    if p.strip()
]
LOCAL_BACKGROUNDS_DIR = ROOT / _env("LOCAL_BACKGROUNDS_DIR", f"{DATA_DIR_NAME}/raw")
YTDL_MAX_DOWNLOADS = int(_env("YTDL_MAX_DOWNLOADS", "2"))
YTDL_FORMAT = _env(
    "YTDL_FORMAT", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
)

# ── Render Settings ──────────────────────────────────────────
RENDER_FPS = int(_env("RENDER_FPS", "60"))
RENDER_WIDTH = 1080
RENDER_HEIGHT = 1920
CAPTION_STYLE = _env("CAPTION_STYLE", "chaotic")  # chaotic, meme, story, npc
CAPTION_FONT = _env("CAPTION_FONT", "Impact")

OVERLAY_REDDIT_SCREENSHOT = _env_bool("OVERLAY_REDDIT_SCREENSHOT", True)
OVERLAY_PROFILE_ICON = _env_bool("OVERLAY_PROFILE_ICON", True)
OVERLAY_SUBREDDIT_TAG = _env_bool("OVERLAY_SUBREDDIT_TAG", True)
OVERLAY_PROGRESS_BAR = _env_bool("OVERLAY_PROGRESS_BAR", True)
OVERLAY_BACKGROUND_BLUR = _env_bool("OVERLAY_BACKGROUND_BLUR", True)

# ── YouTube Upload Settings ──────────────────────────────────
YT_CLIENT_ID = _env("YT_MEME_CLIENT_ID", _env("YT_CLIENT_ID"))
YT_CLIENT_SECRET = _env("YT_MEME_CLIENT_SECRET_VALUE", _env("YT_CLIENT_SECRET_VALUE"))
YT_REFRESH_TOKEN = _env("YT_MEME_REFRESH_TOKEN", _env("YT_REFRESH_TOKEN"))
YT_PRIVACY = _env("YOUTUBE_PRIVACY", "public")

# Engagement Metadata Customizations
METADATA_MENTIONS = _env("METADATA_MENTIONS", "")
METADATA_TRENDING_TAGS = _env("METADATA_TRENDING_TAGS", "viral, fyp, shortvideo, story, dailycontent, interesting, mustwatch")
METADATA_DISCLAIMER = _env("METADATA_DISCLAIMER", "Narration is an AI-generated retelling and commentary of this story.")

# ── Scheduler & Limits ───────────────────────────────────────
MAX_VIDEOS_PER_DAY = int(_env("MAX_VIDEOS_PER_DAY", "4"))
UPLOAD_SCHEDULE_TIMES = [
    t.strip()
    for t in _env("UPLOAD_SCHEDULE_TIMES", "09:00, 13:00, 17:00, 21:00").split(",")
    if t.strip()
]

# ── Instagram (Legacy compatibility / Manual instructions) ───
IG_USERNAME = _env("IG_USERNAME", "")
IG_PASSWORD = _env("IG_PASSWORD", "")
# ── Content Safety Settings ──────────────────────────────────
ENABLE_CONTENT_SAFETY = _env_bool("ENABLE_CONTENT_SAFETY", True)
SAFETY_MODE = _env("SAFETY_MODE", "strict").lower().strip()  # strict, standard, lenient
MAX_ALLOWED_RISK = _env("MAX_ALLOWED_RISK", "low").lower().strip()  # safe, low, medium, high
REJECT_FEMALE_HUMANS = _env_bool("REJECT_FEMALE_HUMANS", True)

CAT_REACTION_FOLDER = ROOT / _env("CAT_REACTION_FOLDER", f"{DATA_DIR_NAME}/assets/cat_reactions")
CAT_LAYOUT_HEIGHT = float(_env("CAT_LAYOUT_HEIGHT", "0.25"))
MEME_LAYOUT_HEIGHT = float(_env("MEME_LAYOUT_HEIGHT", "0.75"))
ENABLE_CAT_REACTIONS = _env_bool("ENABLE_CAT_REACTIONS", True)
CAT_SELECTION_MODE = _env("CAT_SELECTION_MODE", "random")
CAT_AVOID_REPEAT = _env_bool("CAT_AVOID_REPEAT", True)
CAT_HISTORY_FILE = CACHE_DIR / "cat_history.json"

# ── Greenscreen Compilation Settings ────────────────────────────────────────
# Name of the long greenscreen compilation inside CAT_REACTION_FOLDER.
# The bot will never treat this file as a normal clip — it uses random segments.
GREENSCREEN_FILE = ROOT / _env(
    "GREENSCREEN_FILE",
    f"{DATA_DIR_NAME}/assets/cat_reactions/Long green screen video.mp4"
)

# Whether to enable greenscreen segment extraction at all
USE_GREENSCREEN = _env_bool("USE_GREENSCREEN", True)

# Probability (0.0 – 1.0) of choosing the greenscreen compilation instead of
# a normal clip when both are available
GREENSCREEN_PROBABILITY = float(_env("GREENSCREEN_PROBABILITY", "0.40"))

# Length of the randomly extracted segment (seconds)
GREENSCREEN_SEGMENT_MIN = float(_env("GREENSCREEN_SEGMENT_MIN", "4.5"))
GREENSCREEN_SEGMENT_MAX = float(_env("GREENSCREEN_SEGMENT_MAX", "4.5"))

# Chromakey parameters — tune these for the specific green used in the compilation
GREENSCREEN_CHROMA_COLOR = _env("GREENSCREEN_CHROMA_COLOR", "0x00B140")  # YouTube green
GREENSCREEN_CHROMA_SIMILARITY = float(_env("GREENSCREEN_CHROMA_SIMILARITY", "0.12"))
GREENSCREEN_CHROMA_BLEND = float(_env("GREENSCREEN_CHROMA_BLEND", "0.08"))

# Keyed greenscreen overlay canvas size constraint (prevents the animal reaction from looking tiny)
GREENSCREEN_MAX_WIDTH = int(_env("GREENSCREEN_MAX_WIDTH", "800"))
GREENSCREEN_MAX_HEIGHT = int(_env("GREENSCREEN_MAX_HEIGHT", "800"))

# ── Curator Mode Settings ───────────────────────────────────────────
CURATOR_MODE = _env_bool("CURATOR_MODE", True)
MAX_MEME_DURATION = float(_env("MAX_MEME_DURATION", "30.0"))
MIN_MEME_DURATION = float(_env("MIN_MEME_DURATION", "3.0"))
ONLY_REDDIT_HOSTED = _env_bool("ONLY_REDDIT_HOSTED", True)
REJECT_WATERMARKS = _env_bool("REJECT_WATERMARKS", True)
BRANDING_TEXT = _env("BRANDING_TEXT", "@RedditMemes")
ADD_INTRO_OUTRO = _env_bool("ADD_INTRO_OUTRO", True)