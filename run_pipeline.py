#!/usr/bin/env python3
"""
GTA / GTA V Brainrot Shorts — Full Automation Pipeline.
Automatically rotates between styles. Supports YouTube + Instagram uploads.
All subprocess/API calls have timeouts to prevent indefinite hangs on CI.
"""
from __future__ import annotations

import argparse
import random
import signal
import subprocess
import sys
from pathlib import Path

import config
from download_clips import download_fresh_clips, list_existing_raw
from process_clips import process_all_raw, get_random_clip
from generate_script import generate_brainrot_script
from generate_voiceover import synthesize_brainrot_voiceover
from render_short import render

STYLES = ["chaotic", "meme", "story", "npc"]

# Viral hashtag pools
YT_HASHTAGS = "#gaming #gamingclips #gamingvideos #funnygaming #gamingmoments #viralshorts #funnymoments #gamer #clip #gamingcommunity #explore #fyp"
IG_HASHTAGS = "#gaming #gamingclips #viralreels #reels #funnygaming #gamingvideos #explorepage #viralclips #gamer #funnymoments #trending #fyp"

# Global timeout for the entire pipeline (40 min — CI has 45 min limit)
import threading
PIPELINE_TIMEOUT = 40 * 60  # 40 minutes in seconds


def _pick_style(force: str | None) -> str:
    """Pick a random style, or use the forced one."""
    if force and force != "random":
        return force
    return random.choice(STYLES)


def _build_description(style: str, title: str, platform: str = "youtube") -> str:
    """Build catchy description with relevant hashtags."""
    hooks = {
        "chaotic": [
            "Absolute CHAOS in GTA V 🤯 Watch till the end!",
            "This is why GTA V is the BEST game ever made 💀",
            "GTA V physics are BROKEN and I love it 😂",
        ],
        "meme": [
            "GTA V memes never get old 😂 Watch this!",
            "Only in GTA V would this happen 💀",
            "This is PEAK GTA V content right here 🏆",
        ],
        "story": [
            "Every NPC in GTA V has a story 📖 This one is CRAZY",
            "The lore behind GTA V NPCs is DEEP 😱",
            "This NPC has SEEN things in GTA V 👀",
        ],
        "npc": [
            "POV: You're an NPC in GTA V watching the player 💀",
            "The NPC experience in GTA V is UNDERRATED 😂",
            "NPCs in GTA V have enough trauma for a lifetime 💀",
        ],
    }
    hook = random.choice(hooks.get(style, hooks["chaotic"]))
    hashtags = YT_HASHTAGS if platform == "youtube" else IG_HASHTAGS
    return f"{hook}\n\n{title}\n.\n.\n{hashtags}"


def main() -> None:
    # Set an overall pipeline alarm timeout (Unix) or thread timer (cross-platform)
    timer = threading.Timer(PIPELINE_TIMEOUT, lambda: (
        print(f"\n❌ PIPELINE TIMEOUT after {PIPELINE_TIMEOUT//60} minutes — aborting"),
        sys.exit(1)
    ))
    timer.daemon = True
    timer.start()

    try:
        ap = argparse.ArgumentParser(
            description="GTA V Brainrot Shorts — Full Automation Pipeline"
        )
        ap.add_argument("--no-upload", action="store_true", help="Skip YouTube/IG upload")
        ap.add_argument("--skip-download", action="store_true", help="Skip downloading new clips")
        ap.add_argument("--style", default="random",
                        choices=["random", "chaotic", "meme", "story", "npc"],
                        help="Brainrot style (default: random rotation)")
        ap.add_argument("--privacy", default=config.YT_PRIVACY,
                        choices=["private", "unlisted", "public"],
                        help="YouTube privacy setting")
        args = ap.parse_args()

        style = _pick_style(args.style)
        print("=" * 60)
        print(f"🎮 GTA V BRAINROT SHORTS PIPELINE  |  Style: {style.upper()}")
        print("=" * 60)

        # ── Step 1: Download ──
        if not args.skip_download:
            print(f"\n📥 Step 1/6: Downloading fresh GTA V gameplay clips…")
            new_clips = download_fresh_clips()
            if new_clips:
                print(f"   Downloaded {len(new_clips)} new video(s)")
        else:
            print(f"\n📥 Step 1/6: Skipping download")

        # ── Step 2: Process or use existing clips ──
        if args.skip_download:
            # On CI with --skip-download: use pre-committed clips directly
            print(f"\n✂️  Step 2/6: Using pre-committed clips from data/clips/…")
            clips = sorted(config.CLIPS_DIR.glob("*.mp4"))
            if clips:
                print(f"   Found {len(clips)} committed clip(s)")
        if not clips:
            print(f"\n✂️  Step 2/6: Processing raw videos into short clips…")
            clips = process_all_raw()
        if not clips:
            print("⚠ No clips available from downloads.")
            print("   Generating a fallback test clip (solid color + text)…")
            fallback = config.CLIPS_DIR / "fallback.mp4"
            try:
                subprocess.run([
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", f"color=c=#1a1a2e:s=1920x1080:d=40:r=30",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-vf", "drawtext=text='GTA V Gameplay':fontsize=60:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-shortest",
                    str(fallback),
                ], capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                print("   ⚠ Fallback clip generation timed out")
                print("❌ Cannot proceed without any clip")
                sys.exit(1)
            if fallback.exists():
                clips = [fallback]
                print(f"   ✅ Created fallback clip: {fallback.name}")
            else:
                print("❌ Could not create fallback clip either")
                sys.exit(1)

        # ── Step 3: Pick clip ──
        print(f"\n🎲 Step 3/6: Selecting a random clip…")
        clip = get_random_clip()
        if not clip:
            print("❌ No clips in data/clips/")
            sys.exit(1)
        print(f"   Selected: {clip.name}")

        # ── Step 4: Generate script ──
        print(f"\n🧠 Step 4/6: Generating {style} brainrot script…")
        narration, title = generate_brainrot_script(style=style)

        # ── Step 5: TTS ──
        print(f"\n🔊 Step 5/6: Synthesizing voiceover…")
        audio_path = config.OUTPUT_DIR / "voiceover.mp3"
        audio_dur, sentence_timings = synthesize_brainrot_voiceover(
            narration, output_path=audio_path,
        )

        # ── Step 6: Render ──
        print(f"\n🎬 Step 6/6: Rendering final 9:16 short…")
        video_path = config.OUTPUT_DIR / "final_short.mp4"
        render(clip, audio_path, narration, output_path=video_path,
               sentence_timings=sentence_timings)

        # ── Step 7: Upload ──
        if not args.no_upload:
            print(f"\n📤 Upload phase…")

            yt_desc = _build_description(style, title, "youtube")
            ig_desc = _build_description(style, title, "instagram")

            from upload_youtube import upload_short
            upload_short(video_path, title=title, description=yt_desc,
                         privacy=args.privacy)

            from upload_instagram import upload_reel
            upload_reel(video_path, caption=ig_desc)
        else:
            print(f"\n⏭ Skipping upload (--no-upload)")

        print(f"\n{'=' * 60}")
        print(f"✅ PIPELINE COMPLETE!  ({style.upper()} style)")
        print(f"   Video: {video_path}")
        print(f"{'=' * 60}")

    finally:
        timer.cancel()


if __name__ == "__main__":
    main()