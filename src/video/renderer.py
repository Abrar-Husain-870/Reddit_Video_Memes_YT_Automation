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

ALTERNATE_Y = [1350, 1500, 1650, 1400, 1550, 1700]


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
        
        ass += f"Dialogue: 0,{_t(ts_shifted)},{_t(te_shifted)},{style_name},,0,0,{y_margin},,{{\\c{color}\\an5{pop_effect}}}{w}\n"

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
    
    # Calculate exact video duration: 2.0s padding at start, 3.0s reading silence at end
    render_duration = min(59.0, audio_dur + 2.0 + 3.0)
    logger.info(f"Rendering pipeline: Background '{clip_path.name}' | Audio duration: {audio_dur:.2f}s | Target Short duration: {render_duration:.2f}s")
    
    # Clean narration string
    clean_narration = narration.replace("**", "").replace("__", "").replace("*", "")
    clean_narration = re.sub(r'[^\w\s\'",.!?;:\-]', "", clean_narration).strip()
    words = [w for w in clean_narration.split() if w.strip()]
    
    # Base layout dimensions
    target_meme_h = 1440
    target_cat_h = 480
    y_list = None
    
    # Adaptive Aspect Ratio calculations for Cat Reactions layout
    if is_cat_clip and overlay_card_path and overlay_card_path.exists():
        # Get Meme Image dimensions
        mw, mh = 1080, 1080
        try:
            mw, mh = _get_image_dimensions(overlay_card_path)
        except Exception as e:
            logger.warning(f"Failed to read image dimensions for {overlay_card_path}: {e}. Using default square.")
        mar = mw / mh
        
        # Get Cat Reaction video dimensions
        cw, ch = 1920, 1080
        try:
            cw, ch = _get_video_dimensions(clip_path)
        except Exception as e:
            logger.warning(f"Failed to read video dimensions for {clip_path}: {e}. Using default landscape.")
        car = cw / ch
        
        # Determine the target proportions dynamically
        target_meme_h_raw = config.MEME_LAYOUT_HEIGHT * 1920
        target_cat_h_raw = config.CAT_LAYOUT_HEIGHT * 1920
        
        if mar > 1.2 and car < 0.9:  # Landscape meme + Portrait cat (make cat taller, meme shorter)
            target_meme_h_raw = 0.65 * 1920
            target_cat_h_raw = 0.35 * 1920
        elif mar < 0.8 and car > 1.2:  # Portrait meme + Landscape cat (make meme taller, cat shorter)
            target_meme_h_raw = 0.80 * 1920
            target_cat_h_raw = 0.20 * 1920
            
        target_meme_h = make_even(target_meme_h_raw)
        target_cat_h = make_even(target_cat_h_raw)
        
        # Calculate optimal scale bounds for meme to fit inside (1080 x target_meme_h)
        scale_w_meme = 1080
        scale_h_meme = int(1080 / mar)
        if scale_h_meme > target_meme_h:
            scale_h_meme = target_meme_h
            scale_w_meme = int(target_meme_h * mar)
        scale_w_meme = make_even(scale_w_meme)
        scale_h_meme = make_even(scale_h_meme)
        
        # Calculate optimal scale bounds for cat video to fit inside (1080 x target_cat_h)
        scale_w_cat = 1080
        scale_h_cat = int(1080 / car)
        if scale_h_cat > target_cat_h:
            scale_h_cat = target_cat_h
            scale_w_cat = int(target_cat_h * car)
        scale_w_cat = make_even(scale_w_cat)
        scale_h_cat = make_even(scale_h_cat)
        
        # Calculate subtitle centers to reside inside the cat section
        cat_center_y = target_meme_h + (target_cat_h / 2)
        y_list = [
            make_even(cat_center_y - 30),
            make_even(cat_center_y),
            make_even(cat_center_y + 30),
            make_even(cat_center_y - 15),
            make_even(cat_center_y + 15)
        ]
        
    # Map timestamps and build subtitle ASS file
    timings = _build_word_timings(words, audio_dur, sentence_timings)
    ass_content = _build_ass_subtitles(timings, style, emphasis_words, alternate_y=y_list)
    
    # Save ASS captions
    ass_path = config.OUTPUT_DIR / "captions.ass"
    ass_path.write_text(ass_content, encoding="utf-8")
    
    # Escape path prefix for FFmpeg subtitles filter
    ass_safe_path = "data/output/captions.ass"
    
    # Filter Complex Building
    filter_chains = []
    
    if is_cat_clip and overlay_card_path and overlay_card_path.exists():
        # Setup 3 inputs: cat video, narration audio, meme image
        inputs = [
            "-stream_loop", "-1",
            "-i", str(clip_path),
            "-i", str(audio_path),
            "-i", str(overlay_card_path)
        ]
        
        # 1. Composite Cat Box (blurred background + centered scaled foreground)
        filter_chains.append(
            f"[0:v]scale=1080:{target_cat_h}:force_original_aspect_ratio=increase,crop=1080:{target_cat_h},boxblur=20:5[cat_bg]"
        )
        filter_chains.append(
            f"[0:v]scale={scale_w_cat}:{scale_h_cat}[cat_fg]"
        )
        filter_chains.append(
            f"[cat_bg][cat_fg]overlay=x=(1080-w)/2:y=({target_cat_h}-h)/2[cat_box]"
        )
        
        # 2. Composite Meme Box (blurred background + centered scaled foreground + float bobbing animation)
        filter_chains.append(
            f"[2:v]scale=1080:{target_meme_h}:force_original_aspect_ratio=increase,crop=1080:{target_meme_h},boxblur=30:5[meme_bg]"
        )
        filter_chains.append(
            f"[2:v]scale={scale_w_meme}:{scale_h_meme}[meme_fg]"
        )
        filter_chains.append(
            f"[meme_bg][meme_fg]overlay=x=(1080-w)/2:y='({target_meme_h}-h)/2 + 10*sin(2*PI*t/3.0)'[meme_box]"
        )
        
        # 3. Stack Meme Box (top) and Cat Box (bottom)
        filter_chains.append(
            f"[meme_box][cat_box]vstack=inputs=2[v_layout]"
        )
        last_v_tag = "v_layout"
        
    else:
        # Fallback to standard gameplay background composition (2 inputs)
        inputs = [
            "-stream_loop", "-1",
            "-i", str(clip_path),
            "-i", str(audio_path)
        ]
        
        if config.OVERLAY_BACKGROUND_BLUR:
            # Create a blurred background layer, scale foreground over it
            filter_chains.append(
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg]"
            )
            filter_chains.append(
                "[0:v]scale=1080:-1[fg_scaled]"
            )
            filter_chains.append(
                "[bg][fg_scaled]overlay=x=0:y=(1920-h)/2[v_base]"
            )
        else:
            # Standard scale and crop to 9:16
            filter_chains.append(
                "[0:v]scale=1080:1920:flags=lanczos:force_original_aspect_ratio=increase,crop=1080:1920,unsharp=5:5:1.0:5:5:0.0[v_base]"
            )
            
        last_v_tag = "v_base"
        
        # Add Meme Image Centered Overlay
        if overlay_card_path and overlay_card_path.exists():
            inputs.extend(["-i", str(overlay_card_path)])
            filter_chains.append(
                "[2:v]scale=w='min(1000,iw)':h='min(1400,ih)':force_original_aspect_ratio=decrease[meme_scaled]"
            )
            filter_chains.append(
                f"[{last_v_tag}][meme_scaled]overlay=x=(1080-w)/2:y=(1920-h)/2[v_meme]"
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
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=RENDER_TIMEOUT)
        if r.returncode != 0:
            logger.error(f"FFmpeg failed with exit code {r.returncode}")
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

