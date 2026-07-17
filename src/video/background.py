import json
import random
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

import config
from src.logger import logger

SUBPROCESS_TIMEOUT = 300


def load_background_history() -> Dict[str, int]:
    """Load count of times each background clip has been used."""
    if config.BACKGROUND_HISTORY_FILE.exists():
        try:
            with open(config.BACKGROUND_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read background history: {e}. Starting fresh.")
    return {}


def save_background_history(history: Dict[str, int]) -> None:
    """Save background usage history to file."""
    try:
        config.BACKGROUND_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(config.BACKGROUND_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save background history: {e}")


def increment_background_usage(clip_name: str) -> None:
    """Increment the usage count of a background clip."""
    history = load_background_history()
    history[clip_name] = history.get(clip_name, 0) + 1
    save_background_history(history)
    logger.info(f"Updated background usage: {clip_name} has been used {history[clip_name]} time(s)")


def _get_video_duration(path: Path) -> float:
    """Get video duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return float(result.stdout.strip())


def _detect_scenes(path: Path, threshold: float = 0.3) -> List[float]:
    """Detect scene change timestamps in a video."""
    cmd = [
        "ffmpeg", "-i", str(path),
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

    timestamps = []
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            try:
                parts = line.split()
                for p in parts:
                    if p.startswith("pts_time:"):
                        ts = float(p.split(":")[1])
                        timestamps.append(ts)
            except (ValueError, IndexError):
                continue
    return sorted(list(set(timestamps)))


def _slice_into_clips(video_path: Path, scene_times: List[float], duration: float) -> List[Path]:
    """Slice video at scene boundaries and save to clips directory."""
    clips = []
    boundaries = [0.0] + scene_times + [duration]
    
    # Target clip duration: 30-55s (customizable via config)
    min_dur = float(_env_fallback("CLIP_LENGTH_MIN", "30"))
    max_dur = float(_env_fallback("CLIP_LENGTH_MAX", "55"))

    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        clip_dur = end - start

        if clip_dur < min_dur:
            continue
        if clip_dur > max_dur:
            end = start + max_dur

        out_path = config.CLIPS_DIR / f"{video_path.stem}_clip_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(end - start),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.warning(f"Slicing clip {i:03d} timed out, skipping")
            continue

        if out_path.exists() and out_path.stat().st_size > 100_000:
            clips.append(out_path)
            logger.info(f"Created background clip {out_path.name} ({clip_dur:.1f}s)")
            
    return clips


def _env_fallback(key: str, default: str) -> str:
    import os
    return os.environ.get(key, default)


def download_fresh_background() -> List[Path]:
    """Download a raw video from YouTube based on background providers and segment it."""
    if not config.BACKGROUND_PROVIDERS:
        logger.error("No background providers/queries configured.")
        return []

    query = random.choice(config.BACKGROUND_PROVIDERS)
    logger.info(f"Downloading new background video for query: '{query}'")
    
    output_tpl = str(config.RAW_DIR / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--format", config.YTDL_FORMAT,
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--output", output_tpl,
        "--max-downloads", "1",
        "--download-archive", str(config.CACHE_DIR / "background_archive.txt"),
        "--no-playlist",
        "--quiet",
        "--print", "after_move:filepath",
        "--extractor-retries", "1",
        "--retries", "2",
        f"ytsearch1:{query}",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            logger.error(f"yt-dlp background download failed: {result.stderr}")
            return []
            
        downloaded_paths = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                p = Path(line)
                if p.exists():
                    downloaded_paths.append(p)
                    
        if not downloaded_paths:
            logger.warning("No files downloaded by yt-dlp.")
            return []
            
        raw_video = downloaded_paths[0]
        logger.info(f"Successfully downloaded background video: {raw_video.name}")
        
        # Get duration and scene cuts
        dur = _get_video_duration(raw_video)
        logger.info(f"Detecting scenes in {raw_video.name}...")
        scenes = _detect_scenes(raw_video)
        
        # Slice into segments
        clips = _slice_into_clips(raw_video, scenes, dur)
        
        # Clean up the raw download to save space
        raw_video.unlink(missing_ok=True)
        return clips
        
    except Exception as e:
        logger.error(f"Failed to download/process background: {e}")
        return []


def get_background_clip(skip_download: bool = False) -> Optional[Path]:
    """
    Selects a background clip using least-frequently-used selection.
    If no clips exist and skip_download is False, it downloads one.
    """
    # Look for existing clips in CLIPS_DIR
    clips = sorted(config.CLIPS_DIR.glob("*.mp4"))
    
    if not clips and not skip_download:
        logger.info("No clips found in clips directory. Attempting to download background...")
        clips = download_fresh_background()
        
    # If still no clips, check local raw files as backup
    if not clips and config.LOCAL_BACKGROUNDS_DIR.exists():
        clips = sorted(config.LOCAL_BACKGROUNDS_DIR.glob("*.mp4"))
        
    if not clips:
        logger.error("No background clips could be found or downloaded.")
        return None
        
    history = load_background_history()
    
    # Score clips based on usage history (least-frequently-used)
    clip_scores = []
    for c in clips:
        name = c.name
        count = history.get(name, 0)
        clip_scores.append((count, c))
        
    # Sort by count, then pick randomly among the least used
    clip_scores.sort(key=lambda x: x[0])
    min_count = clip_scores[0][0]
    
    candidates = [c for count, c in clip_scores if count == min_count]
    chosen = random.choice(candidates)
    
    logger.info(f"Selected background clip: '{chosen.name}' (used {min_count} times previously)")
    return chosen
