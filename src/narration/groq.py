from typing import Tuple, List

from groq import Groq
import config
from src.logger import logger
from src.narration.base import BaseLLMProvider
from src.narration.helpers import parse_structured_response
from src.narration.prompts import SYSTEM_PROMPT_COMMENTARY, SYSTEM_PROMPT_NATURAL, get_user_prompt
from src.reddit.models import RedditPost


class GroqProvider(BaseLLMProvider):
    """Groq LLM provider for script generation."""

    def __init__(self) -> None:
        self.api_key = config.GROQ_API_KEY
        if not self.api_key:
            logger.warning("GROQ_API_KEY is not configured. GroqProvider may fail.")
        self.client = Groq(api_key=self.api_key) if self.api_key else None

    def generate_narration(
        self, 
        post: RedditPost, 
        mode: str = "commentary", 
        style: str = "chaotic",
        video_duration: float = None
    ) -> dict:
        if not self.client:
            raise ValueError("Groq client not initialized (missing API key)")

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

        logger.info(f"Sending request to Groq model: {config.LLM_MODEL}")
        try:
            completion = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.8,
                max_tokens=600,
                timeout=30,
            )
            response = completion.choices[0].message.content
            if not response:
                raise ValueError("Received empty response from Groq")
                
            return parse_structured_response(response, default_title=post.title)

        except Exception as e:
            logger.error(f"Groq API error: {e}")
            raise e
