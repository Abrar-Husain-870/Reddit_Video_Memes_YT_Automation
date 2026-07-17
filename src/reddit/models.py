from dataclasses import dataclass
from typing import Optional

@dataclass
class RedditPost:
    """Represents a Reddit post with all metadata required for filtering and narration."""
    id: str
    subreddit: str
    title: str
    selftext: str
    score: int
    num_comments: int
    over_18: bool
    is_self: bool
    permalink: str
    author: str
    pinned: bool = False
    crosspost_parent: Optional[str] = None
    media_url: Optional[str] = None
    is_rss: bool = False

    @property
    def url(self) -> str:
        """Returns the full URL of the Reddit post."""
        # If it's RSS, permalink could be a full URL
        if self.permalink.startswith("http"):
            return self.permalink
        return f"https://www.reddit.com{self.permalink}"

    @property
    def media_type(self) -> str:
        """Detect whether the post contains: image, reddit_video, gif, or external_media."""
        if not self.media_url:
            return "unknown"
            
        url_lower = self.media_url.lower().split("?")[0]
        
        # Reddit-hosted video detection
        is_reddit_video = (
            "v.redd.it" in url_lower or 
            "reddit.com" in url_lower or 
            "/vid/" in url_lower or 
            "safereddit.com" in url_lower or 
            "redlib" in url_lower
        )
        if is_reddit_video:
            return "reddit_video"
            
        # GIF detection
        if url_lower.endswith(".gif") or "giphy.com" in url_lower or ("imgur.com" in url_lower and "/a/" not in url_lower and url_lower.endswith(".gif")):
            return "gif"
            
        # Image detection
        image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff")
        if url_lower.endswith(image_extensions):
            return "image"
            
        # Default to external media (other video or image host)
        return "external_media"
