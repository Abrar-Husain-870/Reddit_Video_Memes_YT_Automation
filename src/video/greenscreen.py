"""
Greenscreen Reaction Module
===========================
Handles the "Long green screen video.mp4" compilation asset.

Responsibilities:
  1. Detect whether the selected clip is the greenscreen compilation.
  2. Choose a random start timestamp and extract a 4–7 second segment (trim-only,
     no full decode of the 3-minute source).
  3. Apply FFmpeg chromakey to remove the green background.
  4. Export a trimmed, keyed MP4 with transparency baked on a black canvas so the
     rest of the pipeline receives a normal MP4 it can composite as usual.

Design principles:
  • Never load the whole compilation into memory — always use -ss / -t seeking.
  • All processing is scoped to the extracted segment only.
  • Falls back gracefully on any error (returns None → pipeline falls back to normal clip).
"""
from __future__ import annotations

import random
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import config
from src.logger import logger

# ── FFprobe helper (reused from renderer pattern) ──────────────────────────
FFPROBE_TIMEOUT = 60
RENDER_TIMEOUT  = 300   # 5 min ceiling for segment extraction + keying


def _get_video_duration(path: Path) -> float:
    """Return total duration of a video file in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
    val = r.stdout.strip()
    if not val:
        raise ValueError(f"ffprobe returned no duration for {path}")
    return float(val)


def is_greenscreen_file(path: Path) -> bool:
    """
    Returns True if the given path is the designated greenscreen compilation.
    Matching is done by filename (case-insensitive) so the user can rename it
    without touching the code — as long as the config points at the right folder.
    """
    gs_name = Path(config.GREENSCREEN_FILE).name.lower()
    return path.name.lower() == gs_name


def extract_random_segment(
    source: Path,
    min_dur: float | None = None,
    max_dur: float | None = None,
    output_dir: Path | None = None,
) -> Optional[Tuple[Path, float, float]]:
    """
    Extract a random segment from the greenscreen compilation using fast
    stream-copy seeking (no full decode).

    Returns:
        (segment_path, start_t, end_t) on success, or None on failure.

    The segment is saved as a plain MP4 (still has green background — keying
    happens in a separate step).
    """
    min_dur  = min_dur  if min_dur  is not None else config.GREENSCREEN_SEGMENT_MIN
    max_dur  = max_dur  if max_dur  is not None else config.GREENSCREEN_SEGMENT_MAX
    out_dir  = output_dir or config.OUTPUT_DIR

    try:
        total_dur = _get_video_duration(source)
    except Exception as e:
        logger.error(f"[Greenscreen] Could not read duration of {source.name}: {e}")
        return None

    # Compilation cuts/boundaries to separate different animal reaction clips
    # This prevents extracting a segment that overlaps two different clips
    COMPILATION_BOUNDARIES = [
        0.0, 14.5, 21.467, 32.1, 38.6, 43.667, 51.433, 61.0, 67.833, 75.567, 
        83.067, 97.333, 104.2, 111.0, 117.2, 123.3, 139.167, 142.433, 145.633, 
        155.433, 162.0, 167.567, 175.133, 179.133, 191.367, 201.2
    ]

    # Filter out boundaries to list clips that are long enough
    eligible_clips = []
    for i in range(len(COMPILATION_BOUNDARIES) - 1):
        c_start = COMPILATION_BOUNDARIES[i]
        c_end = COMPILATION_BOUNDARIES[i+1]
        c_dur = c_end - c_start
        if c_dur >= min_dur:
            eligible_clips.append((c_start, c_end, c_dur))

    if not eligible_clips:
        logger.warning("[Greenscreen] No clips within boundaries are long enough. Falling back to whole-file random selection.")
        seg_len = random.uniform(min_dur, max_dur)
        max_start = max(0.0, total_dur - seg_len)
        start_t = random.uniform(0.0, max_start) if max_start > 0 else 0.0
    else:
        # Pick one random clip boundary
        c_start, c_end, c_dur = random.choice(eligible_clips)
        # Select target duration bounded by the clip's actual duration
        seg_len = random.uniform(min_dur, min(max_dur, c_dur))
        # Choose start timestamp completely within the clip boundaries
        max_start = c_end - seg_len
        start_t = random.uniform(c_start, max_start)

    end_t   = start_t + seg_len
    seg_len = round(seg_len, 3)
    start_t = round(start_t, 3)

    logger.info(
        f"[Greenscreen] Extracting segment: {start_t:.2f}s → {end_t:.2f}s "
        f"({seg_len:.2f}s) from {source.name} ({total_dur:.1f}s total)"
    )

    # Write to a temp file in the output dir so it gets cleaned up automatically
    out_path = out_dir / f"gs_segment_{int(start_t*1000)}.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_t),          # fast seek BEFORE input (keyframe-accurate)
        "-t",  str(seg_len),
        "-i",  str(source),
        "-c",  "copy",                # stream copy — no decode, very fast
        str(out_path),
    ]

    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=RENDER_TIMEOUT,
        )
        if r.returncode != 0:
            logger.error(f"[Greenscreen] Segment extraction failed:\n{r.stderr[-1000:]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("[Greenscreen] Segment extraction timed out.")
        return None

    if not out_path.exists() or out_path.stat().st_size < 1000:
        logger.error("[Greenscreen] Segment output file missing or too small.")
        return None

    return out_path, start_t, end_t


def apply_chromakey(
    segment: Path,
    output_dir: Path | None = None,
) -> Optional[Path]:
    """
    Apply FFmpeg chromakey to remove the green background from the segment.

    The green screen removal pipeline:
      1. chromakey=color=green  — detect + replace green with transparency
      2. despill                — remove green colour spill from fur/edges
      3. alphamerge             — clean up alpha channel
      4. Composite over a black canvas so the rest of the pipeline gets a
         normal MP4 with the animal on a clean black background.

    FFmpeg chromakey parameters:
      color=0x00B140  — typical "chroma key green" (#00B140 / YouTube green)
      similarity=0.30 — how aggressively to pull the key (0.01=strict, 1=loose)
      blend=0.08      — soften the key edges (anti-aliasing)
    """
    out_dir  = output_dir or config.OUTPUT_DIR
    out_path = out_dir / f"gs_keyed_{segment.stem}.mp4"

    # Chromakey colour — user-configurable via env
    ck_color      = config.GREENSCREEN_CHROMA_COLOR   # e.g. "0x00B140"
    ck_similarity = config.GREENSCREEN_CHROMA_SIMILARITY  # e.g. 0.30
    ck_blend      = config.GREENSCREEN_CHROMA_BLEND       # e.g. 0.08

    logger.info(
        f"[Greenscreen] Applying chromakey: color={ck_color} "
        f"similarity={ck_similarity} blend={ck_blend}"
    )

    # Filter chain explanation:
    #   [0:v] chromakey → alpha channel created
    #   [base] black canvas same size as input
    #   [base][keyed] overlay → animal on black bg
    #   Result: normal MP4, green replaced by black, edges softened
    filter_complex = (
        f"[0:v]chromakey=color={ck_color}:similarity={ck_similarity}:"
        f"blend={ck_blend}[keyed];"
        f"color=black:s=iw*1:ih*1:r=60[base];"
        f"[base][keyed]overlay=shortest=1[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(segment),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-an",              # drop audio from green screen segment
        str(out_path),
    ]

    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=RENDER_TIMEOUT,
        )
        if r.returncode != 0:
            logger.error(f"[Greenscreen] Chromakey failed:\n{r.stderr[-1000:]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("[Greenscreen] Chromakey timed out.")
        return None

    if not out_path.exists() or out_path.stat().st_size < 1000:
        logger.error("[Greenscreen] Keyed output file missing or too small.")
        return None

    logger.info(f"[Greenscreen] Chromakey complete → {out_path.name}")
    return out_path


def prepare_greenscreen_clip(source: Path) -> Optional[Path]:
    """
    High-level entry point called by cat_selector.

    Extracts a random 4-7s segment from the greenscreen compilation and returns
    its path. The chromakey green-screen removal filter will be applied on-the-fly
    in renderer.py's filter complex, which is faster and avoids losing dark fur
    details by keying over a black canvas.
    """
    result = extract_random_segment(source)
    if result is None:
        return None

    segment_path, start_t, end_t = result
    return segment_path
