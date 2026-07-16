import os
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
    is_cat_clip: bool = False
) -> Path:
    """Render a fully customized 9:16 vertical Short at 60 FPS."""
    if output_path is None:
        output_path = config.OUTPUT_DIR / "final_short.mp4"
        
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Get audio duration to sync video cut length
    audio_dur = _get_audio_duration(audio_path)
    
    # Calculate freeze duration and total render duration
    if is_cat_clip:
        freeze_dur = 2.0 + audio_dur + 3.0
        cat_dur = 5.0
        try:
            cat_dur = _get_audio_duration(clip_path)
        except Exception as e:
            logger.warning(f"Failed to get cat clip duration: {e}. Using 5.0s default.")
        render_duration = freeze_dur + cat_dur
        logger.info(f"Rendering pipeline (Cat Reaction): Static freeze for {freeze_dur:.2f}s, then motion for {cat_dur:.2f}s | Total duration: {render_duration:.2f}s")
    else:
        freeze_dur = 0.0
        render_duration = min(59.0, audio_dur + 2.0 + 3.0)
        logger.info(f"Rendering pipeline (Standard Background): Target Short duration: {render_duration:.2f}s")
    
    # Clean narration string
    clean_narration = narration.replace("**", "").replace("__", "").replace("*", "")
    clean_narration = re.sub(r'[^\w\s\'",.!?;:\-]', "", clean_narration).strip()
    words = [w for w in clean_narration.split() if w.strip()]
    # ── Layout zones (pixels, 1080×1920 canvas) ──────────────────────────────
    # ┌────────────────────┐  y=0
    # │  Caption Bar       │  320px  → Zone 1  (subtitles only, black bg)
    # ├────────────────────┤  y=320
    # │                    │
    # │    Meme Image      │  900px  → Zone 2
    # │                    │
    # ├────────────────────┤  y=1220
    # │                    │
    # │   Cat Clip         │  700px  → Zone 3
    # │  (frozen→motion)   │
    # └────────────────────┘  y=1920
    # ─────────────────────────────────────────────────────────────────────────
    CAPTION_H = 320
    MEME_H    = 900
    CAT_H     = 700
    MEME_TOP  = CAPTION_H            # 320
    CAT_TOP   = CAPTION_H + MEME_H  # 1220

    # Meme image — fit inside Zone 2 (1080 × MEME_H), keeping aspect ratio
    scale_w_meme = 1080
    scale_h_meme = MEME_H
    if overlay_card_path and overlay_card_path.exists():
        try:
            mw, mh = _get_image_dimensions(overlay_card_path)
        except Exception as e:
            logger.warning(f"Failed to read image dimensions for {overlay_card_path}: {e}. Using defaults.")
            mw, mh = 1080, 1080
        mar = mw / mh
        max_w = 1060   # slight padding from edges
        max_h = MEME_H - 20
        scale_w_meme = max_w
        scale_h_meme = int(max_w / mar)
        if scale_h_meme > max_h:
            scale_h_meme = max_h
            scale_w_meme = int(max_h * mar)
        scale_w_meme = make_even(scale_w_meme)
        scale_h_meme = make_even(scale_h_meme)

    # Subtitles confined to the top Caption Bar (0 → 320px).
    # With \an8 (top-center), MarginV = pixel distance from the TOP edge.
    # Centre of 320px bar ≈ 160px from top; vary ±20px for visual rhythm.
    y_list = [140, 160, 180, 150, 170]

    # Cat clip aspect ratio
    car = 16 / 9
    if is_cat_clip:
        try:
            cw, ch = _get_video_dimensions(clip_path)
            car = cw / ch
        except Exception as e:
            logger.warning(f"Failed to read video dimensions for {clip_path}: {e}. Using 16/9 default.")

    # Build word timings & ASS subtitle file
    timings = _build_word_timings(words, audio_dur, sentence_timings)
    ass_content = _build_ass_subtitles(timings, style, emphasis_words, alternate_y=y_list)
    ass_path = config.OUTPUT_DIR / "captions.ass"
    ass_path.write_text(ass_content, encoding="utf-8")
    ass_safe_path = "data/output/captions.ass"

    # ── Filter Complex ──────────────────────────────────────────────────────
    filter_chains = []

    if is_cat_clip and overlay_card_path and overlay_card_path.exists():
        # 3 inputs: cat video, audio, meme image
        inputs = [
            "-i", str(clip_path),
            "-i", str(audio_path),
            "-i", str(overlay_card_path),
        ]

        # 0. Black 1080×1920 canvas
        filter_chains.append("color=c=black:s=1080x1920:r=60[canvas]")

        # 1. Frozen cat thumbnail — loop the very first frame
        filter_chains.append(
            "[0:v]trim=duration=0.1,loop=loop=-1:size=1:start=0,setpts=PTS-STARTPTS[cat_frozen]"
        )

        # 2. Live cat stream — delayed so playback starts after freeze_dur
        filter_chains.append(
            f"[0:v]setpts=PTS+{freeze_dur}/TB[cat_live]"
        )

        # 3. Switch from frozen → live at freeze_dur (overlay swap)
        filter_chains.append(
            f"[cat_frozen][cat_live]overlay=enable='gt(t,{freeze_dur})':eof_action=pass[cat_combined]"
        )

        # 4. Letterbox/pillarbox cat into Zone 3 (1080 × CAT_H) — no cropping ever,
        #    any aspect ratio is supported; black bars fill unused space.
        filter_chains.append(
            f"[cat_combined]"
            f"scale={1080}:{CAT_H}:force_original_aspect_ratio=decrease,"
            f"pad={1080}:{CAT_H}:(ow-iw)/2:(oh-ih)/2:black"
            f"[cat_zone]"
        )

        # 5. Scale meme to fit Zone 2 (preserve aspect ratio, no crop)
        filter_chains.append(
            f"[2:v]scale={scale_w_meme}:{scale_h_meme}:force_original_aspect_ratio=decrease[meme_zone]"
        )

        # 6. Stamp cat into Zone 3 on the canvas
        filter_chains.append(
            f"[canvas][cat_zone]overlay=x=0:y={CAT_TOP}[canvas_cat]"
        )

        # 7. Stamp meme centred vertically inside Zone 2 (with gentle float bob)
        meme_centre_y = MEME_TOP + MEME_H // 2  # = 770
        filter_chains.append(
            f"[canvas_cat][meme_zone]"
            f"overlay=x=(1080-w)/2:y='{meme_centre_y}-h/2+8*sin(2*PI*t/3.0)'"
            f"[v_layout]"
        )

        last_v_tag = "v_layout"

    else:
        # Standard gameplay background (no cat)
        inputs = [
            "-stream_loop", "-1",
            "-i", str(clip_path),
            "-i", str(audio_path),
        ]

        if config.OVERLAY_BACKGROUND_BLUR:
            filter_chains.append(
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=25:5[bg]"
            )
            filter_chains.append("[0:v]scale=1080:-1[fg_scaled]")
            filter_chains.append("[bg][fg_scaled]overlay=x=0:y=(1920-h)/2[v_base]")
        else:
            filter_chains.append(
                "[0:v]scale=1080:1920:flags=lanczos:"
                "force_original_aspect_ratio=increase,crop=1080:1920,"
                "unsharp=5:5:1.0:5:5:0.0[v_base]"
            )

        last_v_tag = "v_base"

        # Meme centred in the lower space [320 → 1920]
        if overlay_card_path and overlay_card_path.exists():
            inputs.extend(["-i", str(overlay_card_path)])
            filter_chains.append(
                f"[2:v]scale={scale_w_meme}:{scale_h_meme}[meme_scaled]"
            )
            filter_chains.append(
                f"[{last_v_tag}][meme_scaled]"
                f"overlay=x=(1080-w)/2:y='1120-h/2+8*sin(2*PI*t/3.0)'[v_meme]"
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
        
    filter_complex_str = ";".join(filter_chains)
    
    # Build complete FFmpeg command
    cmd = [
        "ffmpeg", "-y"
    ]
    cmd.extend(inputs)
    cmd.extend([
        "-filter_complex", filter_complex_str,
        "-map", f"[{last_v_tag}]",
        "-map", "[delayed_audio]",
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

