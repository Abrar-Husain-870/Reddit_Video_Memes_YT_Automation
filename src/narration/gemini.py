from typing import Tuple, List

import config
from src.logger import logger
from src.narration.base import BaseLLMProvider
from src.narration.helpers import parse_structured_response
from src.narration.prompts import SYSTEM_PROMPT_COMMENTARY, SYSTEM_PROMPT_NATURAL, get_user_prompt
from src.reddit.models import RedditPost


class GeminiProvider(BaseLLMProvider):
    """Gemini LLM provider for script generation."""

    def __init__(self) -> None:
        self.api_key = config.GEMINI_API_KEY
        if not self.api_key:
            logger.warning("GEMINI_API_KEY is not configured. GeminiProvider may fail.")

    def generate_narration(
        self, 
        post: RedditPost, 
        mode: str = "commentary", 
        style: str = "chaotic",
        video_duration: float = None
    ) -> dict:
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "The 'google-generativeai' package is required for the Gemini provider. "
                "Please run: pip install google-generativeai"
            )

        if not self.api_key:
            raise ValueError("Gemini client not initialized (missing API key)")

        genai.configure(api_key=self.api_key)
        system_prompt = SYSTEM_PROMPT_COMMENTARY if mode == "commentary" else SYSTEM_PROMPT_NATURAL
        
        if video_duration:
            target_words = int(video_duration * 2)
            min_w = max(5, target_words - 3)
            max_w = target_words + 3
            word_instruction = (
                f"- Write a short, natural, conversational reaction/commentary as the narration. "
                f"Your narration MUST be EXACTLY {min_w} to {max_w} words. This is a strict limit because the video playback duration is exactly {video_duration:.1f} seconds, and the speaker speed is ~2 words per second. Keep it within this word range."
            )
            system_prompt = system_prompt.replace(
                "- Write a short, natural, conversational reaction/commentary as the narration (EXACTLY 10 to 15 words. This is a strict limit. It should take 3 to 5 seconds to speak).",
                word_instruction
            )
            
        user_prompt = get_user_prompt(post.subreddit, post.title, post.selftext)

        logger.info(f"Sending request to Gemini model: {config.LLM_MODEL}")
        try:
            # Gemini 1.5 system instructions are configured in GenerationConfig
            model = genai.GenerativeModel(
                model_name=config.LLM_MODEL,
                system_instruction=system_prompt
            )
            response = model.generate_content(
                user_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.8,
                    max_output_tokens=600,
                )
            )
            
            response_text = response.text
            if not response_text:
                raise ValueError("Received empty response from Gemini")

            return parse_structured_response(response_text, default_title=post.title)

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise e
