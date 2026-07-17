from typing import Tuple, List

import config
from src.logger import logger
from src.narration.base import BaseLLMProvider
from src.narration.helpers import strip_markdown, strip_emojis, extract_emphasis_from_text
from src.reddit.models import RedditPost


def get_llm_provider() -> BaseLLMProvider:
    """Factory function to instantiate the configured LLM provider."""
    provider_name = config.LLM_PROVIDER.lower()
    
    if provider_name == "groq":
        from src.narration.groq import GroqProvider
        return GroqProvider()
    elif provider_name in ("openai", "deepseek", "openrouter", "ollama"):
        from src.narration.openai_like import OpenAILikeProvider
        return OpenAILikeProvider()
    elif provider_name == "gemini":
        from src.narration.gemini import GeminiProvider
        return GeminiProvider()
    else:
        logger.warning(f"Unknown LLM provider '{config.LLM_PROVIDER}'. Defaulting to Groq.")
        from src.narration.groq import GroqProvider
        return GroqProvider()


def generate_script_with_fallback(
    post: RedditPost, 
    mode: str = None, 
    style: str = None,
    video_duration: float = None
) -> dict:
    """
    Generate narration script and metadata from a Reddit post.
    Falls back to a clean reading of the post if the LLM provider fails.
    
    Returns:
        Dict containing script and metadata fields.
    """
    mode = mode or config.NARRATION_MODE
    style = style or config.CAPTION_STYLE
    
    try:
        provider = get_llm_provider()
        parsed = provider.generate_narration(post, mode, style, video_duration=video_duration)
        
        # Verify result is valid
        if parsed and parsed.get("narration") and len(parsed["narration"].split()) >= 5:
            return parsed
        else:
            raise ValueError("Generated script is too short or empty")
            
    except Exception as e:
        logger.warning(f"LLM script generation failed ({e}). Using local regex cleanup fallback.")
        
        # Local fallback: read the post naturally by cleaning it up
        clean_title = strip_markdown(strip_emojis(post.title))
        clean_body = strip_markdown(strip_emojis(post.selftext))
        
        # Truncate content to fit the video duration (or default range: 45-90 seconds)
        words = f"{clean_title}. {clean_body}".split()
        
        if video_duration:
            target_words = max(5, int(video_duration * 2))
            if len(words) > target_words:
                narration = " ".join(words[:target_words]) + "..."
            else:
                narration = " ".join(words)
        else:
            if len(words) > 180:
                narration = " ".join(words[:180]) + "..."
            else:
                narration = " ".join(words)
            
        title = clean_title[:55]
        emphasis = extract_emphasis_from_text(narration, limit=4)
        
        logger.info("Local fallback narration generated successfully")
        return {
            "title": title,
            "narration": narration,
            "emphasis": emphasis,
            "yt_title": "",
            "yt_hook": "",
            "yt_summary": "",
            "yt_category": "",
            "yt_content_tags": []
        }
