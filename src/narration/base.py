from abc import ABC, abstractmethod
from typing import Tuple, List

from src.reddit.models import RedditPost

class BaseLLMProvider(ABC):
    """Abstract Base Class for LLM Narration Providers."""
    
    @abstractmethod
    def generate_narration(
        self, 
        post: RedditPost, 
        mode: str = "commentary", 
        style: str = "chaotic",
        video_duration: float = None
    ) -> dict:
        """
        Generate narration script and metadata from a Reddit post.
        
        Args:
            post: The RedditPost dataclass instance.
            mode: Narration mode ('natural' or 'commentary').
            style: Presentation style ('chaotic', 'meme', 'story', 'npc').
            video_duration: Duration of the background/meme video in seconds.
            
        Returns:
            Dict containing 'narration', 'title', 'emphasis', and YouTube metadata fields.
        """
        pass
