import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Force config variables to mock API keys and set Curator Mode to True
import os
os.environ["CURATOR_MODE"] = "True"
os.environ["REJECT_WATERMARKS"] = "True"
os.environ["MAX_MEME_DURATION"] = "30"
os.environ["MIN_MEME_DURATION"] = "3"
os.environ["ONLY_REDDIT_HOSTED"] = "False"  # set to False to allow our sample URL

import config
from src.reddit.models import RedditPost
from src.reddit.client import filter_post, download_meme_video
from src.video.renderer import render_curator_short, _get_audio_duration
from src.logger import logger

def run_test():
    logger.info("Starting Curator Mode pipeline test (with GitHub hosted sample MP4)...")
    
    # 1. Construct a mock RedditPost with a stable MP4 video
    test_url = "https://github.com/intel-iot-devkit/sample-videos/raw/master/classroom.mp4"
    
    selected_post = RedditPost(
        id="test_curator_post_123",
        subreddit="funny",
        title="When the code finally runs successfully on the first try",
        selftext="",
        score=5000,
        num_comments=350,
        over_18=False,
        is_self=False,
        permalink="/r/funny/comments/test_curator_post_123",
        author="test_developer",
        pinned=False,
        crosspost_parent=None,
        media_url=test_url
    )
    
    logger.info(f"Mocked post: ID={selected_post.id}, Title='{selected_post.title}', URL={selected_post.media_url}")
    
    # Run filters
    processed_ids = set()
    reason = filter_post(selected_post, processed_ids)
    if reason is not None:
        logger.error(f"Post was filtered out: {reason}")
        sys.exit(1)
    logger.info("✔ Post successfully passed filter_post constraints.")

    # 2. Download candidate post
    logger.info(f"Downloading video from: {selected_post.media_url}")
    try:
        video_path = download_meme_video(selected_post.media_url, selected_post.id)
        logger.info(f"Video downloaded to: {video_path}")
    except Exception as e:
        logger.error(f"Failed to download video: {e}")
        sys.exit(1)
        
    # 3. Check duration filter
    try:
        dur = _get_audio_duration(video_path)
        logger.info(f"Meme video duration: {dur:.2f} seconds")
        if dur > config.MAX_MEME_DURATION:
            logger.warning(f"Meme is too long ({dur:.2f}s > {config.MAX_MEME_DURATION}s). Clamping for rendering test.")
        elif dur < config.MIN_MEME_DURATION:
            logger.warning(f"Meme is too short ({dur:.2f}s < {config.MIN_MEME_DURATION}s). Continuing rendering test anyway.")
    except Exception as e:
        logger.error(f"Could not read duration: {e}")
        sys.exit(1)
        
    # 4. Render Short via Curator Mode
    output_video = config.OUTPUT_DIR / "test_curator_final.mp4"
    output_video.unlink(missing_ok=True)
    
    logger.info("Rendering curator Short...")
    try:
        render_curator_short(
            meme_video_path=video_path,
            output_path=output_video,
            title=selected_post.title,
            branding_text="@CuratorTestBot",
            add_intro_outro=True
        )
        if output_video.exists():
            size_mb = output_video.stat().st_size / (1024 * 1024)
            logger.info(f"✔ SUCCESS: Curator Short rendered at: {output_video} ({size_mb:.2f}MB)")
        else:
            logger.error("❌ FAILED: Output file not created.")
            sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Rendering failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_test()
