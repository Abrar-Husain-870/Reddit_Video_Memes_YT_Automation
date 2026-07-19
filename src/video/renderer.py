import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Dict

import config
from src.logger import logger

RENDER_TIMEOUT = 600
FFPROBE_TIMEOUT = 60

# Palette color schemes (ASS colors are in format &HAAABBBCC, where AA is alpha, BB is blue, GG is green, RR is red)
STYLE_PALETTES = {
    "chaotic": ["&H000045FF", "&H004763FF", "&H000000FF", "&H0000A5FF",
                "&H000045FF", "&H004763FF", "&H000000FF", "&H0000A5FF"],  # Orange/Red/Yellows (reversed BB/GG/RR for ASS)
    "meme":   ["&H0000FF00", "&H0032CD32", "&H002FDFFF", "&H0000FF7F",
               "&H0000FF00", "&H0032CD32", "&H002FDFFF", "&H0000FF7F"],  # Greens
    "story":  ["&H00FFFFFF", "&H00FFF8F0", "&H00E0E0E0", "&H00D3D3D3",
               "&H00FFFFFF", "&H00FFF8F0", "&H00E0E0E0", "&H00D3D3D3"],  # Whites/Silvers
    "npc":    ["&H00DC7F93", "&H00E22B8A", "&H00D355BA", "&H00D670DA",
               "&H00DC7F93", "&H00E22B8A", "&H00D355BA", "&H00D670DA"],  # Purples/Pinks
}

# y values are pixel distances from the TOP of the screen (used with \an8 top-center alignment)
# Kept within the 0–320px caption bar; centred around y=160
ALTERNATE_Y = [140, 160, 180, 150, 170, 155]


def _get_audio_duration(path: Path) -> float:
    """Get duration of audio file using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", 
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", 
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
    return float(r.stdout.strip())


def _build_word_timings(
    words: List[str], 
    audio_dur: float,
    sentence_timings: List[dict] | None = None
) -> List[Tuple[float, float, str]]:
    """Map words to timestamps using sentence boundaries for alignment."""
    result = []
    n = len(words)

    if sentence_timings and len(sentence_timings) > 0:
        word_idx = 0
        for sent in sentence_timings:
            s_text = sent.get("text", "")
            s_start = sent.get("offset_ms", 0) / 1000
            s_dur = sent.get("duration_ms", 1000) / 1000
            s_end = s_start + s_dur
            s_words = [w for w in s_text.split() if w.strip()]
            n_s_words = len(s_words)
            
            if n_s_words > 0 and word_idx < n:
                w_per = s_dur / n_s_words
                for j in range(n_s_words):
                    if word_idx >= n:
                        break
                    ws = s_start + j * w_per
                    we = min(ws + w_per, s_end)
                    result.append((ws, we, words[word_idx]))
                    word_idx += 1
                    
        # Allocate any leftover words to the end of the audio
        remaining = n - word_idx
        if remaining > 0:
            last_end = result[-1][1] if result else 0.0
            time_left = max(0.1, audio_dur - last_end)
            w_per = time_left / max(remaining, 1)
            for j in range(remaining):
                ws = last_end + j * w_per
                we = min(ws + w_per, audio_dur)
                result.append((ws, we, words[word_idx]))
                word_idx += 1
    else:
        # Uniform fallback mapping
        w_per = audio_dur / max(n, 1)
        for i, w in enumerate(words):
            ws = i * w_per
            we = min((i + 1) * w_per, audio_dur)
            result.append((ws, we, w))
            
    return result


def _build_ass_subtitles(
    timings: List[Tuple[float, float, str]],
    style: str = "chaotic",
    emphasis_words: List[str] | None = None,
    alternate_y: List[int] | None = None
) -> str:
    """Build ASS formatted subtitle content with word popping effects."""
    palette = STYLE_PALETTES.get(style, STYLE_PALETTES["chaotic"])
    emphasis_set = set(w.upper() for w in (emphasis_words or []))
    
    y_list = alternate_y if alternate_y is not None else ALTERNATE_Y

    # Font sizes: 90pt normal, 130pt emphasis (1.5x)
    ass = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Emphasis,{config.CAPTION_FONT},130,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,4,5,0,0,0,1\n"
        f"Style: Normal,{config.CAPTION_FONT},90,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,4,5,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def _t(s):
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        return f"{h}:{m:02d}:{s % 60:05.2f}"

    for i, (ts, te, w) in enumerate(timings):
        word_clean = w.strip(".,!?;:\"'()[]{}*-").upper()
        is_emphasis = word_clean in emphasis_set
        style_name = "Emphasis" if is_emphasis else "Normal"
        color = palette[i % len(palette)]
        alt_idx = i % len(y_list)
        y_margin = y_list[alt_idx]
        
        # Word popping animation: start 1.25x larger and scale down to 1x over 100ms
        pop_effect = "\\fscx125\\fscy125\\t(0,100,\\fscx100,\\fscy100)"
        
        # Shift timings forward by 2.0 seconds
        ts_shifted = ts + 2.0
        te_shifted = te + 2.0
        
        # \an8 = top-center alignment; MarginV is the pixel distance from the TOP edge
        ass += f"Dialogue: 0,{_t(ts_shifted)},{_t(te_shifted)},{style_name},,0,0,{y_margin},,{{\\c{color}\\an8{pop_effect}}}{w}\n"

    return ass


def _get_video_dimensions(path: Path) -> Tuple[int, int]:
    """Get video width and height using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
    out = r.stdout.strip()
    if not out:
        raise ValueError(f"Empty ffprobe output for {path}")
    line = out.split()[0] if out.split() else out
    w, h = map(int, line.split('x'))
    return w, h


def _has_audio(path: Path) -> bool:
    """Check if the video file contains an audio stream using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(path)
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
        return "audio" in r.stdout.lower()
    except Exception:
        return False


def _get_image_dimensions(path: Path) -> Tuple[int, int]:
    """Get image width and height using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
    out = r.stdout.strip()
    if not out:
        raise ValueError(f"Empty ffprobe output for {path}")
    line = out.split()[0] if out.split() else out
    w, h = map(int, line.split('x'))
    return w, h


def make_even(val: float) -> int:
    """Helper to ensure dimensions/offsets are even integers (required by FFmpeg)."""
    v = int(round(val))
    return v if v % 2 == 0 else v + 1


def render_short(
    clip_path: Path,
    audio_path: Path,
    narration: str,
    overlay_card_path: Path | None = None,
    output_path: Path | None = None,
    sentence_timings: List[dict] | None = None,
    style: str = "chaotic",
    emphasis_words: List[str] | None = None,
    is_cat_clip: bool = False,
    is_greenscreen: bool = False
) -> Path:
    """Render a fully customized 9:16 vertical Short at 60 FPS."""
    if output_path is None:
        output_path = config.OUTPUT_DIR / "final_short.mp4"
        
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Get audio duration to sync video cut length
    audio_dur = _get_audio_duration(audio_path)
    
    # Detect if overlay meme is a video
    is_meme_video = overlay_card_path and overlay_card_path.suffix.lower() in ('.mp4', '.webm', '.gif')
    meme_dur = 0.0
    if is_meme_video:
        try:
            meme_dur = _get_audio_duration(overlay_card_path)
            logger.info(f"Meme video duration: {meme_dur:.2f} seconds")
        except Exception as e:
            logger.warning(f"Could not read meme video duration: {e}. Defaulting to 10 seconds.")
            meme_dur = 10.0
            
    # Calculate freeze duration and total render duration
    if is_cat_clip:
        base_freeze_dur = 2.0 + audio_dur
        if is_meme_video:
            freeze_dur = max(base_freeze_dur, 2.0 + meme_dur)
        else:
            freeze_dur = base_freeze_dur
            
        cat_dur = 4.5
        render_duration = freeze_dur + cat_dur
        
        # Max limit for YouTube Shorts is 59 seconds
        if render_duration > 59.0:
            render_duration = 59.0
            freeze_dur = render_duration - cat_dur
            
        logger.info(f"Rendering pipeline (Cat Reaction): Static freeze for {freeze_dur:.2f}s, then motion for {cat_dur:.2f}s | Total duration: {render_duration:.2f}s | Greenscreen: {is_greenscreen}")
    else:
        freeze_dur = 0.0
        base_render_dur = audio_dur + 2.0 + 3.0
        if is_meme_video:
            render_duration = max(base_render_dur, 2.0 + meme_dur)
        else:
            render_duration = base_render_dur
            
        render_duration = min(59.0, render_duration)
        logger.info(f"Rendering pipeline (Standard Background): Target Short duration: {render_duration:.2f}s")
    
    # Clean narration string
    clean_narration = narration.replace("**", "").replace("__", "").replace("*", "")
    clean_narration = re.sub(r'[^\w\s\'",.!?;:\-]', "", clean_narration).strip()
    words = [w for w in clean_narration.split() if w.strip()]
    # ── Global canvas constants ───────────────────────────────────────────────
    CANVAS_W  = 1080
    CANVAS_H  = 1920
    CAPTION_H = 320    # top caption bar, always reserved for subtitles
    CONTENT_H = CANVAS_H - CAPTION_H   # 1600px available for meme + cat
    MARGIN    = 24     # safe margin around every asset (never touch edges)

    # ── Step 1: Read asset dimensions ────────────────────────────────────────
    mw, mh = 1080, 1080   # meme defaults
    if overlay_card_path and overlay_card_path.exists():
        try:
            if is_meme_video:
                mw, mh = _get_video_dimensions(overlay_card_path)
            else:
                mw, mh = _get_image_dimensions(overlay_card_path)
        except Exception as e:
            logger.warning(f"Could not read meme dimensions: {e}. Assuming square.")

    cw, ch = 1280, 720    # cat defaults (landscape)
    if is_cat_clip:
        try:
            cw, ch = _get_video_dimensions(clip_path)
        except Exception as e:
            logger.warning(f"Could not read cat dimensions: {e}. Assuming 16:9.")

    meme_ar = mw / mh
    cat_ar  = cw / ch

    # ── Step 2: Choose meme zone height (content split) ──────────────────────
    # Classify meme aspect ratio and give it proportional vertical space
    #
    #   ultra-portrait  (AR < 0.55):  meme gets ~68% of content → 1088px
    #   portrait        (AR < 0.85):  meme gets ~60% of content →  960px
    #   square          (AR < 1.20):  meme gets ~55% of content →  880px
    #   landscape       (AR < 2.00):  meme gets ~47% of content →  752px
    #   ultra-wide      (AR >= 2.0):  meme gets ~42% of content →  672px
    #
    # Both normal and greenscreen reaction layouts use adaptive vertical splits
    # to guarantee that the reaction overlay never overlaps or obstructs the meme image.
    if meme_ar < 0.55:
        meme_zone_h = 1088
    elif meme_ar < 0.85:
        meme_zone_h = 960
    elif meme_ar < 1.20:
        meme_zone_h = 880
    elif meme_ar < 2.00:
        meme_zone_h = 752
    else:
        meme_zone_h = 672

    meme_zone_h = make_even(meme_zone_h)
    cat_zone_h  = make_even(CONTENT_H - meme_zone_h)   # fills rest of content area

    logger.info(
        f"Adaptive layout: meme_ar={meme_ar:.2f} cat_ar={cat_ar:.2f} → "
        f"meme_zone={meme_zone_h}px cat_zone={cat_zone_h}px"
    )

    # ── Step 3: Fit dimensions for foreground assets (with safe margins) ──────
    meme_fit_w = CANVAS_W - 2 * MARGIN   # 1032
    meme_fit_h = meme_zone_h - 2 * MARGIN

    if is_greenscreen:
        # Bounding box constraint from config (prevents the animal from looking tiny)
        # Constrain height to fit completely within the dedicated bottom cat zone
        max_gs_w = config.GREENSCREEN_MAX_WIDTH
        max_gs_h = min(config.GREENSCREEN_MAX_HEIGHT, cat_zone_h - 2 * MARGIN)
        gs_w = max_gs_w
        gs_h = int(max_gs_w / cat_ar)
        if gs_h > max_gs_h:
            gs_h = max_gs_h
            gs_w = int(max_gs_h * cat_ar)
        cat_fit_w = make_even(gs_w)
        cat_fit_h = make_even(gs_h)
    else:
        cat_fit_w  = CANVAS_W - 2 * MARGIN
        cat_fit_h  = cat_zone_h  - 2 * MARGIN

    # ── Step 4: Subtitle y-positions (within caption bar, \an8 top-center) ───
    y_list = [140, 160, 180, 150, 170]

    # ── Step 5: Build word timings & ASS subtitle file ────────────────────────
    timings     = _build_word_timings(words, audio_dur, sentence_timings)
    ass_content = _build_ass_subtitles(timings, style, emphasis_words, alternate_y=y_list)
    ass_path    = config.OUTPUT_DIR / "captions.ass"
    ass_path.write_text(ass_content, encoding="utf-8")
    ass_safe_path = f"{config.DATA_DIR_NAME}/output/captions.ass"

    # ── Filter Complex ──────────────────────────────────────────────────────
    filter_chains = []

    # Handle meme video padding/freezing if it is a video
    meme_v_tag = "[2:v]"
    if is_meme_video and overlay_card_path and overlay_card_path.exists():
        # Use tpad to freeze the last frame of the meme video infinitely
        filter_chains.append(
            f"[2:v]tpad=stop=-1:stop_mode=clone[meme_padded]"
        )
        meme_v_tag = "[meme_padded]"

    if is_cat_clip and overlay_card_path and overlay_card_path.exists():
        # ── 3 inputs: [0] cat video  [1] narration audio  [2] meme image ────
        inputs = [
            "-i", str(clip_path),
            "-i", str(audio_path),
            "-i", str(overlay_card_path),
        ]

        # ── A. Caption bar: very dark background (not pure black) ─────────
        filter_chains.append(
            f"color=c=#111111:s={CANVAS_W}x{CAPTION_H}:r=60[caption_bar]"
        )

        if is_greenscreen:
            # ── B. Greenscreen keying on-the-fly ───────────────────────────
            ck_color      = config.GREENSCREEN_CHROMA_COLOR
            ck_similarity = config.GREENSCREEN_CHROMA_SIMILARITY
            ck_blend      = config.GREENSCREEN_CHROMA_BLEND

            filter_chains.append(
                f"[0:v]chromakey=color={ck_color}:similarity={ck_similarity}:"
                f"blend={ck_blend},despill,split=2[cat_keyed1][cat_keyed2]"
            )
            filter_chains.append(
                f"[cat_keyed1]trim=duration=0.1,loop=loop=-1:size=1:start=0,"
                f"setpts=PTS-STARTPTS,trim=duration={freeze_dur}[cat_frozen]"
            )
            filter_chains.append(
                f"[cat_keyed2]trim=duration={cat_dur},setpts=PTS-STARTPTS[cat_live]"
            )
            filter_chains.append(
                f"[cat_frozen][cat_live]concat=n=2:v=1:a=0[cat_combined]"
            )
            # Scale key-rendered animal keeping aspect ratio (adaptive scaling)
            filter_chains.append(
                f"[cat_combined]scale={cat_fit_w}:{cat_fit_h}:"
                f"force_original_aspect_ratio=decrease[cat_scaled]"
            )

            # ── C. Meme zone (full content height for greenscreen overlay) ──
            filter_chains.append(f"{meme_v_tag}split=2[meme_bg_src][meme_fg_src]")
            filter_chains.append(
                f"[meme_bg_src]scale={CANVAS_W}:{meme_zone_h}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{meme_zone_h},"
                f"boxblur=45:5[meme_blur_bg]"
            )
            filter_chains.append(
                f"[meme_fg_src]scale={meme_fit_w}:{meme_fit_h}:"
                f"force_original_aspect_ratio=decrease[meme_fg]"
            )
            filter_chains.append(
                f"[meme_blur_bg][meme_fg]overlay="
                f"x=(W-w)/2:y='(H-h)/2+6*sin(2*PI*t/3.5)'[meme_zone]"
            )

            # Create a dedicated dark background for the reaction zone at the bottom
            filter_chains.append(
                f"color=c=#111111:s={CANVAS_W}x{cat_zone_h}:r=60[cat_bg_zone]"
            )
            # Stack Caption + Meme Zone + Cat Background Zone
            filter_chains.append(
                "[caption_bar][meme_zone][cat_bg_zone]vstack=inputs=3[v_base]"
            )

            # ── D. Overlay keyed animal at random position ─────────────────
            placement = random.choice([
                "bottom-center",
                "bottom-left",
                "bottom-right",
                "slightly-above-bottom"
            ])
            logger.info(f"Greenscreen animal placement chosen: {placement}")
            
            if placement == "bottom-left":
                overlay_x = "24"
                overlay_y = "1920-h-24"
            elif placement == "bottom-right":
                overlay_x = "1080-w-24"
                overlay_y = "1920-h-24"
            elif placement == "slightly-above-bottom":
                overlay_x = "(1080-w)/2"
                overlay_y = "1920-h-180"
            else: # bottom-center
                overlay_x = "(1080-w)/2"
                overlay_y = "1920-h-24"

            filter_chains.append(
                f"[v_base][cat_scaled]overlay=x='{overlay_x}':y='{overlay_y}'[v_layout]"
            )
            last_v_tag = "v_layout"

        else:
            # ── Normal cat layout (3-section stack) ────────────────────────
            filter_chains.append("[0:v]split=2[cat_src1][cat_src2]")
            filter_chains.append(
                f"[cat_src1]trim=duration=0.1,loop=loop=-1:size=1:start=0,"
                f"setpts=PTS-STARTPTS,trim=duration={freeze_dur}[cat_frozen]"
            )
            filter_chains.append(
                f"[cat_src2]trim=duration={cat_dur},setpts=PTS-STARTPTS[cat_live]"
            )
            filter_chains.append(
                f"[cat_frozen][cat_live]concat=n=2:v=1:a=0[cat_combined]"
            )

            # Cat zone: blurred background + letterboxed foreground
            filter_chains.append("[cat_combined]split=2[cat_bg_src][cat_fg_src]")
            filter_chains.append(
                f"[cat_bg_src]scale={CANVAS_W}:{cat_zone_h}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{cat_zone_h},"
                f"boxblur=35:5[cat_blur_bg]"
            )
            filter_chains.append(
                f"[cat_fg_src]scale={cat_fit_w}:{cat_fit_h}:"
                f"force_original_aspect_ratio=decrease[cat_fg]"
            )
            filter_chains.append(
                f"[cat_blur_bg][cat_fg]overlay="
                f"x=(W-w)/2:y=(H-h)/2[cat_zone]"
            )

            # Meme zone: blurred background + fitted foreground
            filter_chains.append(f"{meme_v_tag}split=2[meme_bg_src][meme_fg_src]")
            filter_chains.append(
                f"[meme_bg_src]scale={CANVAS_W}:{meme_zone_h}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{meme_zone_h},"
                f"boxblur=45:5[meme_blur_bg]"
            )
            filter_chains.append(
                f"[meme_fg_src]scale={meme_fit_w}:{meme_fit_h}:"
                f"force_original_aspect_ratio=decrease[meme_fg]"
            )
            filter_chains.append(
                f"[meme_blur_bg][meme_fg]overlay="
                f"x=(W-w)/2:y='(H-h)/2+6*sin(2*PI*t/3.5)'[meme_zone]"
            )

            # Stack Caption + Meme + Cat
            filter_chains.append(
                "[caption_bar][meme_zone][cat_zone]vstack=inputs=3[v_layout]"
            )

            last_v_tag = "v_layout"

    else:
        # ── Standard gameplay background (no cat clip) ─────────────────────
        inputs = [
            "-stream_loop", "-1",
            "-i", str(clip_path),
            "-i", str(audio_path),
        ]

        if config.OVERLAY_BACKGROUND_BLUR:
            filter_chains.append(
                f"[0:v]scale={CANVAS_W}:{CANVAS_H}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},boxblur=25:5[bg]"
            )
            filter_chains.append(f"[0:v]scale={CANVAS_W}:-1[fg_scaled]")
            filter_chains.append(
                f"[bg][fg_scaled]overlay=x=0:y=({CANVAS_H}-h)/2[v_base]"
            )
        else:
            filter_chains.append(
                f"[0:v]scale={CANVAS_W}:{CANVAS_H}:flags=lanczos:"
                f"force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},"
                f"unsharp=5:5:1.0:5:5:0.0[v_base]"
            )

        last_v_tag = "v_base"

        # Meme centred in the lower content area [320 → 1920]
        if overlay_card_path and overlay_card_path.exists():
            inputs.extend(["-i", str(overlay_card_path)])
            fit_w = CANVAS_W - 2 * MARGIN
            fit_h = CONTENT_H - 2 * MARGIN
            filter_chains.append(
                f"{meme_v_tag}scale={fit_w}:{fit_h}:"
                f"force_original_aspect_ratio=decrease[meme_scaled]"
            )
            content_centre_y = CAPTION_H + CONTENT_H // 2   # 1120
            filter_chains.append(
                f"[{last_v_tag}][meme_scaled]"
                f"overlay=x=(W-w)/2:y='{content_centre_y}-h/2+6*sin(2*PI*t/3.5)'[v_meme]"
            )
            last_v_tag = "v_meme"

            
    # 4. Add captions overlay
    filter_chains.append(
        f"[{last_v_tag}]subtitles={ass_safe_path}[v_sub]"
    )
    last_v_tag = "v_sub"
    
    # 5. Draw Progress Bar (synced to exact render duration)
    if config.OVERLAY_PROGRESS_BAR:
        progress_color = "0xFF5500"  # Orange
        bar_y = 1880
        bar_height = 12
        filter_chains.append(
            f"[{last_v_tag}]drawbox=x=0:y={bar_y}:w='1080*t/{render_duration:.2f}':h={bar_height}:color={progress_color}@0.9:t=fill[v_final]"
        )
        last_v_tag = "v_final"
        
    # 6. Delay audio by 2000ms
    filter_chains.append(
        "[1:a]adelay=2000|2000[delayed_audio]"
    )
    
    audio_map_tag = "[delayed_audio]"
    if is_meme_video and overlay_card_path and overlay_card_path.exists() and _has_audio(overlay_card_path):
        filter_chains.append(
            "[2:a]volume=0.15[meme_a]"
        )
        filter_chains.append(
            "[delayed_audio][meme_a]amix=inputs=2:duration=longest:dropout_transition=0,volume=1.5[mixed_audio]"
        )
        audio_map_tag = "[mixed_audio]"
        
    filter_complex_str = ";".join(filter_chains)
    
    # Build complete FFmpeg command
    cmd = [
        "ffmpeg", "-y"
    ]
    cmd.extend(inputs)
    cmd.extend([
        "-filter_complex", filter_complex_str,
        "-map", f"[{last_v_tag}]",
        "-map", audio_map_tag,
        "-c:v", "libx264",
        "-r", str(config.RENDER_FPS),
        "-preset", "medium",
        "-crf", "18",
        "-profile:v", "high",
        "-level", "4.2",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ar", "48000",
        "-t", f"{render_duration:.2f}",
        "-movflags", "+faststart",
        str(output_path)
    ])
    
    logger.info(f"Running FFmpeg render (60 FPS, preset: medium, output: {output_path.name})")
    
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=RENDER_TIMEOUT)
        if r.returncode != 0:
            logger.error(f"FFmpeg failed with exit code {r.returncode}")
            logger.error(f"FFmpeg stderr: {r.stderr}")
            raise RuntimeError(f"FFmpeg render failed with exit code {r.returncode}")
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg render timed out")
        raise TimeoutError("FFmpeg rendering timed out")
        
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"✔ Short rendered successfully: {output_path.name} ({size_mb:.2f}MB, 60fps)")
        return output_path
    else:
        raise FileNotFoundError("Rendered video file not found after FFmpeg completion")


def render_curator_short(
    meme_video_path: Path,
    output_path: Path | None = None,
    title: str = "",
    branding_text: str = "",
    add_intro_outro: bool = True
) -> Path:
    """
    Renders a curator-style 9:16 vertical Short from a horizontal/square/portrait Reddit meme video.
    No voiceover or reaction clips are layered. The original audio of the meme video is preserved.
    """
    if output_path is None:
        output_path = config.OUTPUT_DIR / "final_short.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_title_path = config.OUTPUT_DIR / "temp_title.txt"
    
    is_image = meme_video_path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff')
    if is_image:
        meme_dur = 10.0
        mw, mh = _get_image_dimensions(meme_video_path)
    else:
        try:
            meme_dur = _get_audio_duration(meme_video_path)
        except Exception as e:
            logger.warning(f"Could not read meme video duration: {e}. Defaulting to 10 seconds.")
            meme_dur = 10.0
        mw, mh = _get_video_dimensions(meme_video_path)
        
    meme_ar = mw / mh
    
    # Target duration (maximum of 59s)
    render_duration = min(59.0, meme_dur)
    logger.info(f"Rendering Curator Short: duration={render_duration:.2f}s | aspect_ratio={meme_ar:.2f}")
    
    # 2. Build inputs
    is_image = meme_video_path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff')
    if is_image:
        inputs = ["-loop", "1", "-i", str(meme_video_path)]
    else:
        inputs = ["-i", str(meme_video_path)]
    
    # 3. Build filter chains
    filter_chains = []
    
    # A. Scale/Crop base canvas (1080x1920)
    # If the video is landscape/square, use blurred background + centered foreground.
    # If it is portrait, scale to fit.
    # A. Scale/Crop base canvas (1080x1920)
    # If the video is landscape/square, use blurred background + centered foreground.
    # If it is portrait, scale to fit the full screen.
    if meme_ar < 0.6:
        # Portrait (vertical) meme: scale to fit and pad to exactly 1080x1920
        filter_chains.append(
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black[base_v]"
        )
    else:
        # Landscape/Square meme: split into blurred bg and scaled fg
        filter_chains.append(
            f"[0:v]split=2[bg_src][fg_src]"
        )
        # Background: blur and scale to 1080x1920
        filter_chains.append(
            f"[bg_src]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur=30:5[bg_blurred]"
        )
        # Foreground: scale to width 1032 (preserving aspect ratio)
        fg_w = 1032
        fg_h = make_even(1032 / meme_ar)
        filter_chains.append(
            f"[fg_src]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease[fg_scaled]"
        )
        # Overlay foreground onto blurred background
        filter_chains.append(
            f"[bg_blurred][fg_scaled]overlay=x=(W-w)/2:y=(H-h)/2[base_v]"
        )
        
    last_v_tag = "base_v"
        
    # C. Add Branding Overlay
    if branding_text:
        # Subtle channel branding/watermark at the bottom
        filter_chains.append(
            f"[{last_v_tag}]drawtext=text='{branding_text}':"
            f"fontcolor=white@0.5:fontsize=36:font='Arial':x=(w-text_w)/2:y=h-180:"
            f"borderw=2:bordercolor=black@0.5[v_brand]"
        )
        last_v_tag = "v_brand"
        
    # D. Add Smooth Fade-In and Fade-Out Transitions
    if add_intro_outro:
        fade_dur = 0.5
        out_start = render_duration - fade_dur
        filter_chains.append(
            f"[{last_v_tag}]fade=type=in:start_time=0:duration={fade_dur},"
            f"fade=type=out:start_time={out_start:.2f}:duration={fade_dur}[v_faded]"
        )
        last_v_tag = "v_faded"
        
    # E. Draw Progress Bar (synced to exact render duration)
    if config.OVERLAY_PROGRESS_BAR:
        progress_color = "0xFF5500"  # Orange
        bar_y = 1880
        bar_height = 12
        filter_chains.append(
            f"[{last_v_tag}]drawbox=x=0:y={bar_y}:w='1080*t/{render_duration:.2f}':h={bar_height}:color={progress_color}@0.9:t=fill[v_final]"
        )
        last_v_tag = "v_final"
        
    # F. Audio Processing: Fade-in/out the original audio
    has_audio = _has_audio(meme_video_path)
    audio_map_args = []
    if has_audio:
        if add_intro_outro:
            fade_dur = 0.5
            out_start = render_duration - fade_dur
            filter_chains.append(
                f"[0:a]afade=type=in:start_time=0:duration={fade_dur},"
                f"afade=type=out:start_time={out_start:.2f}:duration={fade_dur}[a_faded]"
            )
            audio_map_args = ["-map", "[a_faded]"]
        else:
            audio_map_args = ["-map", "0:a"]
    
    # 4. Build and execute complete FFmpeg command
    filter_complex_str = ";".join(filter_chains)
    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)
    cmd.extend([
        "-filter_complex", filter_complex_str,
        "-map", f"[{last_v_tag}]"
    ])
    if has_audio:
        cmd.extend(audio_map_args)
        
    cmd.extend([
        "-c:v", "libx264",
        "-r", str(config.RENDER_FPS),
        "-preset", "medium",
        "-crf", "18",
        "-profile:v", "high",
        "-level", "4.2"
    ])
    if has_audio:
        cmd.extend([
            "-c:a", "aac",
            "-b:a", "256k",
            "-ar", "48000"
        ])
    cmd.extend([
        "-t", f"{render_duration:.2f}",
        "-movflags", "+faststart",
        str(output_path)
    ])
    
    logger.info(f"Running FFmpeg Curator Render (60 FPS, preset: medium, output: {output_path.name})")
    
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=RENDER_TIMEOUT)
        if r.returncode != 0:
            logger.error(f"FFmpeg failed with exit code {r.returncode}")
            logger.error(f"FFmpeg stderr: {r.stderr}")
            raise RuntimeError(f"FFmpeg render failed with exit code {r.returncode}")
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg render timed out")
        raise TimeoutError("FFmpeg rendering timed out")
    finally:
        # Clean up temp title file
        if temp_title_path.exists():
            try:
                temp_title_path.unlink()
            except Exception:
                pass
                
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"✔ Curator Short rendered successfully: {output_path.name} ({size_mb:.2f}MB, 60fps)")
        return output_path
    else:
        raise FileNotFoundError("Rendered video file not found after FFmpeg completion")


def clean_title_for_ffmpeg(text: str) -> str:
    """Cleans a title string to be safe for FFmpeg drawtext (stripping emojis, cleaning smart quotes)."""
    # Replace curly quotes and apostrophes
    replacements = {
        '“': '"',
        '”': '"',
        '‘': "'",
        '’': "'",
        '–': "-",
        '—': "-",
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)
        
    # Remove emojis and other non-ASCII characters
    # We can keep basic printable ASCII characters (32 to 126)
    clean_chars = []
    for char in text:
        code = ord(char)
        if 32 <= code <= 126:
            clean_chars.append(char)
        elif char == '\n':
            clean_chars.append(char)
            
    # Clean up double spaces or spaces before punctuation
    cleaned = "".join(clean_chars)
    # Replace multiple spaces with a single space
    cleaned = re.sub(r' +', ' ', cleaned)
    return cleaned.strip()


