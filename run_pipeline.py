#!/usr/bin/env python3
"""
Meme Shorts Automation Pipeline — Master Orchestrator.
Ingests image memes from Reddit, generates short commentary, synthesizes voiceover,
renders 12-second vertical shorts with centred meme overlay, and uploads to YouTube.
Robust error handling ensures the pipeline completes even if APIs fail.
"""
from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from pathlib import Path

# Force UTF-8 encoding on Windows standard output/error to prevent Unicode encoding crashes
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import config
from src.logger import (
    logger,
    log_stage_start,
    log_stage_finish,
    log_stage_error,
)
from src.reddit.client import get_random_reddit_post, save_processed_id
from src.narration import generate_script_with_fallback
from src.voice import synthesize_voiceover_with_fallback
from src.video import (
    get_background_clip,
    increment_background_usage,
    render_short,
    get_cat_reaction_clip,
)
from src.upload import upload_short, check_scheduler_run

# Global timeout for safety (40 minutes)
PIPELINE_TIMEOUT = 40 * 60


def clean_temp_files() -> None:
    """Clean intermediate temp files in output directory to save space."""
    logger.info("Cleaning up temporary render files...")
    for ext in ["*.ass", "*.mp3", "*.png"]:
        for f in config.OUTPUT_DIR.glob(ext):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Could not delete temp file {f.name}: {e}")


def main() -> None:
    # Safe fallback timer to kill hung subprocesses or API hangs
    timer = threading.Timer(
        PIPELINE_TIMEOUT, 
        lambda: (
            log_stage_error("Global Pipeline", "Execution timed out", fatal=True),
            sys.exit(1)
        )
    )
    timer.daemon = True
    timer.start()

    try:
        # ── Argument Parsing ─────────────────────────────────
        ap = argparse.ArgumentParser(
            description="Reddit to Shorts Automation Pipeline"
        )
        ap.add_argument("--no-upload", action="store_true", help="Skip YouTube upload phase")
        ap.add_argument("--force", action="store_true", help="Ignore scheduler slots, run immediately")
        ap.add_argument("--style", default="random", 
                        choices=["random", "chaotic", "meme", "story", "npc"],
                        help="Video/caption styling (default: random)")
        ap.add_argument("--mode", default=None, choices=["natural", "commentary"],
                        help="Narration generation mode (default: from config)")
        ap.add_argument("--subreddit", default=None,
                        help="Override subreddits to fetch from (comma-separated)")
        ap.add_argument("--privacy", default=config.YT_PRIVACY,
                        choices=["public", "unlisted", "private"],
                        help="YouTube video visibility")
        ap.add_argument("--skip-download", action="store_true",
                        help="Skip downloading new background clips from YouTube")
        args = ap.parse_args()

        print("=" * 70)
        print("=== MEME SHORTS AUTOMATION PIPELINE ===")
        print("=" * 70)

        # ── Step 0: Scheduler Gatekeeping ────────────────────
        log_stage_start("Scheduler Check")
        if args.no_upload:
            should_run = True
            logger.info("Scheduler Check: Bypassed (--no-upload specified)")
        else:
            should_run = check_scheduler_run(force=args.force)
        log_stage_finish("Scheduler Check", {"should_run": should_run})
        
        if not should_run:
            print("\n[INFO] Pipeline run skipped by Scheduler. (Not inside an active time slot).")
            print("   Use '--force' to bypass this check.")
            sys.exit(0)

        # Style & Voice Mapping
        style = args.style
        if style == "random":
            style = random.choice(["chaotic", "meme", "story", "npc"])
            
        style_voices = {
            "chaotic": "en-US-AndrewNeural",
            "meme": "en-US-AndrewNeural",
            "story": "en-US-BrianNeural",
            "npc": "en-US-GuyNeural",
        }
        tts_voice = style_voices.get(style, "en-US-AndrewNeural")

        if args.subreddit:
            config.SUBREDDITS = [s.strip() for s in args.subreddit.split(",") if s.strip()]

        # ── Step 1 & 2: Ingestion & Safety Verification Loop ──
        from src.safety import ContentSafetyAnalyzer, log_rejected_post
        safety_analyzer = ContentSafetyAnalyzer()

        post = None
        script_data = None
        optimized_meta = None
        narration = ""
        caption_title = ""
        emphasis = []
        meme_video_path = None
        meme_duration = 10.0

        max_attempts = 15
        attempted_ids = set()
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Attempting post safety verification (Try {attempt}/{max_attempts})...")

            # Step 1: Reddit Ingestion
            log_stage_start("Reddit Ingestion")
            post = get_random_reddit_post(exclude_ids=attempted_ids)
            if not post:
                error_msg = "No suitable Reddit posts found matching criteria"
                log_stage_error("Reddit Ingestion", error_msg, fatal=True)
                sys.exit(1)

            attempted_ids.add(post.id)

            log_stage_finish("Reddit Ingestion", {
                "id": post.id,
                "subreddit": post.subreddit,
                "title": post.title[:40] + "..."
            })

            # Step 1.5: Download Meme Video
            from src.reddit.client import download_meme_video
            from src.video.renderer import _get_audio_duration
            try:
                meme_video_path = download_meme_video(post.media_url, post.id)
                is_image = meme_video_path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff')
                if is_image:
                    if getattr(config, "ONLY_VIDEOS", True):
                        logger.warning(f"Post {post.id} rejected: Post is an image, but ONLY_VIDEOS is enabled.")
                        continue
                    meme_duration = 10.0
                else:
                    try:
                        meme_duration = _get_audio_duration(meme_video_path)
                    except Exception as ex:
                        logger.warning(f"Could not read meme video duration: {ex}. Defaulting to 10 seconds.")
                        meme_duration = 10.0
                
                # Enforce duration filters in Curator Mode
                if config.CURATOR_MODE:
                    if meme_duration > config.MAX_MEME_DURATION:
                        logger.warning(f"Post {post.id} rejected: Meme duration {meme_duration:.1f}s > {config.MAX_MEME_DURATION:.1f}s")
                        continue
                    if meme_duration < config.MIN_MEME_DURATION:
                        logger.warning(f"Post {post.id} rejected: Meme duration {meme_duration:.1f}s < {config.MIN_MEME_DURATION:.1f}s")
                        continue
            except Exception as e:
                log_stage_error("Reddit Ingestion", f"Failed to download meme video: {e}. Trying another post.")
                continue

            # Stage 1 Safety Check: Immediately after Reddit Ingestion and Video Download
            safety_res = safety_analyzer.check_safety(
                title=post.title,
                body=post.selftext,
                image_path=meme_video_path,
                stage="After Ingestion"
            )

            if not safety_res["passed"]:
                logger.warning(f"Post {post.id} rejected at Stage 1 Ingestion safety check. Reason: {safety_res['reason']}")
                log_rejected_post(
                    post_id=post.id,
                    subreddit=post.subreddit,
                    risk_score=safety_res["risk_score"],
                    category=", ".join(safety_res["categories_detected"]) or "safety_policy_violation",
                    reason=safety_res["reason"]
                )
                continue  # Skip and fetch another Reddit post automatically

            # Stage 1.2: Meme Suitability & Universal Appeal check
            suitability_res = safety_analyzer.check_meme_suitability(
                title=post.title,
                image_path=meme_video_path
            )
            if not suitability_res["passed"]:
                reason_str = suitability_res["ratings"].get("reason", "Failed appeal/suitability criteria")
                logger.warning(f"Post {post.id} rejected for low appeal/suitability. Reason: {reason_str}")
                log_rejected_post(
                    post_id=post.id,
                    subreddit=post.subreddit,
                    risk_score="Low Appeal",
                    category="meme_suitability_filter",
                    reason=reason_str
                )
                continue  # Skip and fetch another Reddit post automatically

            # Stage 1.3: Female human presence check (if configured)
            if config.REJECT_FEMALE_HUMANS:
                if safety_analyzer.check_female_presence(meme_video_path):
                    logger.warning(f"Post {post.id} rejected: Video contains a female human.")
                    log_rejected_post(
                        post_id=post.id,
                        subreddit=post.subreddit,
                        risk_score="Reject",
                        category="female_human_detected",
                        reason="Video contains a female human."
                    )
                    continue

            # Step 2: Narration Scripting
            log_stage_start("Script Generation")
            narration_mode = args.mode or config.NARRATION_MODE
            try:
                if config.CURATOR_MODE:
                    script_data = {
                        "narration": "",
                        "title": "",
                        "emphasis": [],
                        "yt_title": post.title,
                        "yt_hook": post.title,
                        "yt_summary": post.title,
                        "yt_category": "Comedy",
                        "yt_content_tags": []
                    }
                    narration = ""
                    caption_title = ""
                    emphasis = []
                else:
                    script_data = generate_script_with_fallback(post, narration_mode, style, video_duration=meme_duration)
                    narration = script_data["narration"]
                    caption_title = script_data["title"]
                    emphasis = script_data["emphasis"]
            except Exception as e:
                log_stage_error("Script Generation", f"Script generation failed: {e}. Trying another post.")
                continue

            log_stage_finish("Script Generation", {
                "title": caption_title,
                "word_count": len(narration.split()) if narration else 0,
                "emphasis_count": len(emphasis)
            })
            logger.info(f"Generated Script Text:\n{narration}\nEmphasis: {emphasis}")

            # Step 2.5: Optimize Metadata for Engagement
            from src.upload.metadata import generate_optimized_metadata
            optimized_meta = generate_optimized_metadata(
                post=post,
                narration=narration,
                llm_title=script_data.get("yt_title", ""),
                llm_hook=script_data.get("yt_hook", ""),
                llm_summary=script_data.get("yt_summary", ""),
                llm_category=script_data.get("yt_category", ""),
                llm_content_tags=script_data.get("yt_content_tags", [])
            )

            # Stage 2 Safety Check: After narration generation
            safety_res = safety_analyzer.check_safety(
                title=post.title,
                body=post.selftext,
                narration=narration,
                yt_title=optimized_meta["title"],
                description=optimized_meta["description"],
                tags=optimized_meta["tags"],
                captions=caption_title,
                image_path=meme_video_path,
                stage="After Narration"
            )

            if not safety_res["passed"]:
                logger.warning(f"Post {post.id} rejected at Stage 2 Narration safety check. Reason: {safety_res['reason']}")
                log_rejected_post(
                    post_id=post.id,
                    subreddit=post.subreddit,
                    risk_score=safety_res["risk_score"],
                    category=", ".join(safety_res["categories_detected"]) or "safety_policy_violation",
                    reason=safety_res["reason"]
                )
                continue  # Skip and fetch another Reddit post automatically

            # Stage 3 Safety Check: Before rendering
            safety_res = safety_analyzer.check_safety(
                title=post.title,
                body=post.selftext,
                narration=narration,
                yt_title=optimized_meta["title"],
                description=optimized_meta["description"],
                tags=optimized_meta["tags"],
                captions=caption_title,
                image_path=meme_video_path,
                stage="Before Rendering"
            )

            if not safety_res["passed"]:
                logger.warning(f"Post {post.id} rejected at Stage 3 Pre-Rendering safety check. Reason: {safety_res['reason']}")
                log_rejected_post(
                    post_id=post.id,
                    subreddit=post.subreddit,
                    risk_score=safety_res["risk_score"],
                    category=", ".join(safety_res["categories_detected"]) or "safety_policy_violation",
                    reason=safety_res["reason"]
                )
                continue  # Skip and fetch another Reddit post automatically

            # If we reach this point, the post is safe and validated
            logger.info(f"🎉 Post {post.id} passed all pre-rendering safety gates! Proceeding to rendering.")
            break
        else:
            error_msg = f"Failed to ingest a safe Reddit post after {max_attempts} attempts."
            log_stage_error("Reddit Ingestion", error_msg, fatal=True)
            sys.exit(1)

        if config.CURATOR_MODE:
            # Curator Mode Video Rendering
            log_stage_start("Video Rendering")
            video_path = config.OUTPUT_DIR / "final_short.mp4"
            from src.video.renderer import render_curator_short
            try:
                render_curator_short(
                    meme_video_path=meme_video_path,
                    output_path=video_path,
                    title=post.title,
                    branding_text=config.BRANDING_TEXT,
                    add_intro_outro=config.ADD_INTRO_OUTRO
                )
                log_stage_finish("Video Rendering", {"output_video": str(video_path)})
            except Exception as e:
                log_stage_error("Video Rendering", e, fatal=True)
                sys.exit(1)

            # ── Step 7: Update Databases ─────────────────────────
            log_stage_start("Database Update")
            save_processed_id(post.id, post.subreddit)
            log_stage_finish("Database Update")
        else:
            # ── Step 3: Background / Cat Clip Selection ──────────
            log_stage_start("Background Selection")
            bg_clip = None
            is_cat_clip = False
            is_greenscreen = False
            
            if config.ENABLE_CAT_REACTIONS:
                bg_clip, is_greenscreen = get_cat_reaction_clip()
                if bg_clip:
                    is_cat_clip = True
                    logger.info(f"Using cat reaction clip: {bg_clip.name} (greenscreen={is_greenscreen})")
                else:
                    logger.warning("ENABLE_CAT_REACTIONS is True but no cat clips found. Falling back to gameplay background.")
                    
            if not bg_clip:
                bg_clip = get_background_clip(skip_download=args.skip_download)
                is_greenscreen = False
                
            if not bg_clip or not bg_clip.exists():
                error_msg = "Could not retrieve background / cat video clip"
                log_stage_error("Background Selection", error_msg, fatal=True)
                sys.exit(1)

            log_stage_finish("Background Selection", {"filename": bg_clip.name, "is_cat": is_cat_clip, "is_greenscreen": is_greenscreen})

            # ── Step 4: Overlay Selection ────────────────────────
            log_stage_start("Overlay Selection")
            overlay_path = meme_video_path
            log_stage_finish("Overlay Selection", {"path": str(overlay_path)})

            # ── Step 5: Voice Synthesis (TTS) ───────────────────
            log_stage_start("Voice Synthesis")
            audio_path = config.OUTPUT_DIR / "voiceover.mp3"
            try:
                audio_dur, sentence_timings = synthesize_voiceover_with_fallback(
                    narration, output_path=audio_path, voice=tts_voice
                )
                log_stage_finish("Voice Synthesis", {"duration_s": audio_dur, "sentences": len(sentence_timings)})
            except Exception as e:
                log_stage_error("Voice Synthesis", e, fatal=True)
                sys.exit(1)

            # ── Step 6: FFmpeg Render ────────────────────────────
            log_stage_start("Video Rendering")
            video_path = config.OUTPUT_DIR / "final_short.mp4"
            try:
                render_short(
                    clip_path=bg_clip,
                    audio_path=audio_path,
                    narration=narration,
                    overlay_card_path=overlay_path,
                    output_path=video_path,
                    sentence_timings=sentence_timings,
                    style=style,
                    emphasis_words=emphasis,
                    is_cat_clip=is_cat_clip,
                    is_greenscreen=is_greenscreen
                )
                log_stage_finish("Video Rendering", {"output_video": str(video_path)})
            except Exception as e:
                log_stage_error("Video Rendering", e, fatal=True)
                sys.exit(1)

            # ── Step 7: Update Databases ─────────────────────────
            log_stage_start("Database Update")
            save_processed_id(post.id, post.subreddit)
            if not is_cat_clip:
                increment_background_usage(bg_clip.name)
            log_stage_finish("Database Update")

        # ── Step 8: Upload YouTube Shorts ────────────────────
        video_id = ""
        if not args.no_upload:
            log_stage_start("YouTube Upload")

            # Stage 4 Safety Check: Immediately before upload
            logger.info("Content Safety Stage 4: Performing final pre-upload validation...")
            safety_res = safety_analyzer.check_safety(
                yt_title=optimized_meta["title"],
                description=optimized_meta["description"],
                tags=optimized_meta["tags"],
                metadata={"category_id": optimized_meta["category_id"]},
                image_path=meme_video_path,
                stage="Before Upload"
            )

            if not safety_res["passed"]:
                logger.error(f"Post {post.id} REJECTED at final Stage 4 Pre-Upload check! Aborting upload. Reason: {safety_res['reason']}")
                log_rejected_post(
                    post_id=post.id,
                    subreddit=post.subreddit,
                    risk_score=safety_res["risk_score"],
                    category=", ".join(safety_res["categories_detected"]) or "safety_policy_violation",
                    reason=safety_res["reason"]
                )
                log_stage_error("YouTube Upload", f"Upload aborted due to safety policy violation: {safety_res['reason']}")
            else:
                video_id = upload_short(
                    video_path=video_path,
                    title=optimized_meta["title"],
                    description=optimized_meta["description"],
                    tags=optimized_meta["tags"],
                    category_id=optimized_meta["category_id"],
                    privacy=args.privacy
                )

                if video_id:
                    log_stage_finish("YouTube Upload", {"video_id": video_id})
                else:
                    log_stage_error("YouTube Upload", "Upload failed")
        else:
            log_stage_start("YouTube Upload")
            log_stage_finish("YouTube Upload", {"status": "skipped"})

        # ── Step 9: Instagram Reels Guide ────────────────────
        print()
        print("=" * 70)
        print("=== INSTAGRAM MANUAL UPLOAD GUIDE ===")
        print("=" * 70)
        print(f"Post Description:\n{optimized_meta['description']}\n")
        print(f"Video File: {video_path}")
        print("=" * 70)

        # Cleanup intermediate files
        clean_temp_files()

        print("\n[SUCCESS] PIPELINE SUCCESSFUL!")
        if video_id:
            print(f"   Published YouTube Short: https://www.youtube.com/shorts/{video_id}")
        print(f"   Video file saved to: {video_path}\n")

    finally:
        timer.cancel()


if __name__ == "__main__":
    main()