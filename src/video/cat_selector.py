"""
Cat / Reaction Clip Selector
=============================
Selects a reaction asset for each Short.  Supports two asset types:

  Type 1 – Normal reaction clips (cat_02.mp4 … cat_06.mp4)
            Standard MP4 files selected randomly with repeat-prevention.

  Type 2 – Greenscreen compilation ("Long green screen video.mp4")
            A single long video treated as a library of reactions.
            A random 4–7 second segment is extracted and chromakeyed
            each time it is used.

Selection logic:
  • If USE_GREENSCREEN is True AND the compilation file exists AND normal
    clips also exist → randomly pick between the two types according to
    GREENSCREEN_PROBABILITY (e.g. 0.40 = 40% chance of greenscreen).
  • If only one type is available → always use that type.
  • Returns (clip_path, is_greenscreen) so callers know which path was taken.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Optional, Tuple

import config
from src.logger import logger

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


# ── History helpers (unchanged from original) ─────────────────────────────

def load_cat_history() -> List[str]:
    """Load the list of recently used cat reaction clip filenames."""
    if config.CAT_HISTORY_FILE.exists():
        try:
            with open(config.CAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [str(x) for x in data]
        except Exception as e:
            logger.warning(f"Failed to read cat history: {e}. Starting fresh.")
    return []


def save_cat_history(history: List[str]) -> None:
    """Save the recently used cat reaction clip filenames to history."""
    try:
        config.CAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(config.CAT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save cat history: {e}")


# ── Normal clip selector (original behaviour, unchanged) ──────────────────

def _select_normal_clip(clips: List[Path]) -> Optional[Path]:
    """
    Pick one normal reaction clip with repeat-prevention history.
    Identical logic to the original get_cat_reaction_clip().
    """
    if not clips:
        return None

    if len(clips) == 1:
        logger.info(f"Only one normal cat clip available: '{clips[0].name}'. Using it.")
        return clips[0]

    history = load_cat_history()
    max_history_len = max(1, len(clips) - 1)

    if len(history) > max_history_len:
        history = history[-max_history_len:]

    candidates = clips
    if config.CAT_AVOID_REPEAT:
        candidates = [c for c in clips if c.name not in history]
        if not candidates:
            logger.info("All cat clips used recently. Resetting history filter.")
            candidates = clips
            history = []

    if config.CAT_SELECTION_MODE.lower() == "sequential":
        candidates_sorted = sorted(
            candidates,
            key=lambda c: history.index(c.name) if c.name in history else -1,
        )
        chosen = candidates_sorted[0]
    else:
        chosen = random.choice(candidates)

    # Update history
    if chosen.name in history:
        history.remove(chosen.name)
    history.append(chosen.name)
    if len(history) > max_history_len:
        history = history[-max_history_len:]

    save_cat_history(history)
    logger.info(
        f"Selected cat clip: '{chosen.name}' "
        f"(history size: {len(history)}/{max_history_len})"
    )
    return chosen


# ── Greenscreen selector ───────────────────────────────────────────────────

def _select_greenscreen_clip(gs_file: Path) -> Optional[Path]:
    """
    Use the greenscreen module to extract a random segment and apply chromakey.
    Returns the processed clip path, or None on failure.
    """
    try:
        from src.video.greenscreen import prepare_greenscreen_clip
        keyed = prepare_greenscreen_clip(gs_file)
        if keyed and keyed.exists():
            return keyed
        logger.warning("[Greenscreen] prepare_greenscreen_clip returned None. Falling back.")
        return None
    except Exception as e:
        logger.error(f"[Greenscreen] Unexpected error during processing: {e}")
        return None


# ── Public entry point ────────────────────────────────────────────────────

def get_cat_reaction_clip() -> Tuple[Optional[Path], bool]:
    """
    Select a reaction clip for the current Short.

    Returns:
        (clip_path, is_greenscreen)
        • clip_path      – Path to the selected/processed clip, or None on failure.
        • is_greenscreen – True if this clip was produced from the greenscreen
                           compilation (chromakey applied, animal on black bg).

    Callers should check is_greenscreen to decide whether to apply the
    chromakey compositing mode in the renderer.
    """
    folder = Path(config.CAT_REACTION_FOLDER)
    if not folder.exists():
        logger.warning(f"Cat reaction folder does not exist: {folder}. Creating it.")
        folder.mkdir(parents=True, exist_ok=True)
        return None, False

    gs_file = Path(config.GREENSCREEN_FILE)

    # Discover all files; separate greenscreen compilation from normal clips
    all_files = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
    ]

    # Greenscreen compilation: identified by filename
    gs_candidates = [p for p in all_files if p.name.lower() == gs_file.name.lower()]
    normal_clips   = [p for p in all_files if p.name.lower() != gs_file.name.lower()]

    gs_available     = bool(gs_candidates) and config.USE_GREENSCREEN
    normal_available = bool(normal_clips)

    logger.info(
        f"Reaction assets: {len(normal_clips)} normal clip(s), "
        f"greenscreen={'available' if gs_available else 'unavailable/disabled'}"
    )

    # ── Choose which type to use ──────────────────────────────────────────
    use_greenscreen = False

    if gs_available and normal_available:
        history = load_cat_history()
        # If the last 3 items in history do not contain the greenscreen filename, force greenscreen
        if len(history) >= 3 and all(name != gs_file.name for name in history[-3:]):
            use_greenscreen = True
            logger.info("[Cat Selector] Greenscreen hasn't been used in the last 3 runs. Forcing greenscreen for variety!")
        # If the immediate previous run was greenscreen, force normal to prevent back-to-back repeats
        elif history and history[-1] == gs_file.name:
            use_greenscreen = False
            logger.info("[Cat Selector] Greenscreen was used in the last run. Forcing normal clip to prevent back-to-back repeats.")
        else:
            # Both types present and not forced: roll the dice
            use_greenscreen = random.random() < config.GREENSCREEN_PROBABILITY
            logger.info(
                f"Asset type roll (p_gs={config.GREENSCREEN_PROBABILITY:.0%}): "
                f"{'→ GREENSCREEN' if use_greenscreen else '→ normal clip'}"
            )
    elif gs_available and not normal_available:
        use_greenscreen = True
        logger.info("Only greenscreen compilation available. Using it.")
    elif normal_available and not gs_available:
        use_greenscreen = False
        logger.info("Only normal clips available (greenscreen disabled or missing).")
    else:
        logger.warning("No reaction assets found in cat reaction folder.")
        return None, False

    # ── Execute the chosen path ───────────────────────────────────────────
    if use_greenscreen:
        gs_src = gs_candidates[0]
        clip = _select_greenscreen_clip(gs_src)
        if clip:
            # Update history with greenscreen filename
            history = load_cat_history()
            if gs_file.name in history:
                history.remove(gs_file.name)
            history.append(gs_file.name)
            max_history_len = len(normal_clips)
            if len(history) > max_history_len:
                history = history[-max_history_len:]
            save_cat_history(history)
            return clip, True
        # Greenscreen processing failed — fall back to normal clips
        logger.warning("[Greenscreen] Processing failed. Falling back to normal clip.")
        if not normal_available:
            return None, False

    # Normal clip path (original behaviour)
    clip = _select_normal_clip(normal_clips)
    return clip, False
