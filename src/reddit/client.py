import json
import random
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Set, Optional
import requests

# Initialize global session to reuse connections and support cookie persistence
session_client = requests.Session()

import config
from src.logger import logger
from src.reddit.models import RedditPost

# Global headers mimicking the official Reddit iOS application to bypass CDN bot protection
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Reddit/2023.23.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}


def load_processed_ids() -> Set[str]:
    """Load the set of already processed and rejected Reddit post IDs."""
    ids = set()
    if config.HISTORY_FILE.exists():
        try:
            with open(config.HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    ids.update(data)
        except Exception as e:
            logger.warning(f"Failed to read Reddit post history: {e}.")
            
    # Load rejected posts to prevent them from ever being retried
    rejected_file = config.DB_DIR / "rejected_posts.json"
    if rejected_file.exists():
        try:
            with open(rejected_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "reddit_id" in item:
                            ids.add(item["reddit_id"])
        except Exception as e:
            logger.warning(f"Failed to read rejected posts history: {e}.")
            
    return ids


def save_processed_id(post_id: str) -> None:
    """Save a processed Reddit post ID to prevent duplicates."""
    processed = load_processed_ids()
    processed.add(post_id)
    try:
        config.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(config.HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed)), f, indent=2)
        logger.info(f"Saved Reddit ID {post_id} to history database")
    except Exception as e:
        logger.error(f"Failed to save Reddit ID to history: {e}")


def _fetch_anonymous_json(subreddit: str, sort: str, time_filter: str) -> List[dict]:
    """Fetch subreddit posts using the public JSON API."""
    url = f"https://old.reddit.com/r/{subreddit}/{sort}.json"
    params = {}
    if sort == "top" and time_filter:
        params["t"] = time_filter
    
    logger.info(f"Fetching posts from anonymous Reddit feed: {url}")
    try:
        response = session_client.get(url, params=params, headers=DEFAULT_HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
        children = data.get("data", {}).get("children", [])
        return [child.get("data", {}) for child in children]
    except Exception as e:
        logger.warning(f"Public Reddit API fetch failed for r/{subreddit}: {e}")
        return []


def _fetch_with_praw(subreddit: str, sort: str, time_filter: str) -> List[dict]:
    """Fetch posts using PRAW (Python Reddit API Wrapper) if credentials are provided."""
    try:
        import praw
    except ImportError:
        logger.debug("PRAW is not installed. Falling back to public JSON feeds.")
        return []

    if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
        logger.debug("Reddit API credentials not fully set. Falling back to public JSON feeds.")
        return []

    logger.info(f"Fetching posts via PRAW for r/{subreddit} (sort: {sort}, time: {time_filter})")
    try:
        reddit = praw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            user_agent=config.REDDIT_USER_AGENT
        )
        sub = reddit.subreddit(subreddit)
        
        # Resolve sorting
        if sort == "top":
            feed = sub.top(time_filter=time_filter, limit=50)
        elif sort == "new":
            feed = sub.new(limit=50)
        elif sort == "rising":
            feed = sub.rising(limit=50)
        else:
            feed = sub.hot(limit=50)
            
        posts = []
        for post in feed:
            posts.append({
                "id": post.id,
                "subreddit": post.subreddit.display_name,
                "title": post.title,
                "selftext": post.selftext,
                "score": post.score,
                "num_comments": post.num_comments,
                "over_18": post.over_18,
                "is_self": post.is_self,
                "permalink": post.permalink,
                "author": post.author.name if post.author else "[deleted]",
                "pinned": getattr(post, "pinned", False),
                "crosspost_parent": getattr(post, "crosspost_parent", None),
                "url": getattr(post, "url", "")
            })
        return posts
    except Exception as e:
        logger.error(f"PRAW fetch failed for r/{subreddit}: {e}. Falling back to public JSON.")
        return []


def _fetch_with_rss(subreddit: str) -> List[dict]:
    """Fetch posts via public RSS feeds as a third fallback."""
    import xml.etree.ElementTree as ET
    import html.parser
    import re as _re
    
    class HTMLTextExtractor(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
            self.images = []      # <img src="..."> URLs
            self.link_hrefs = []  # <a href="..."> URLs
        def handle_data(self, data):
            self.text.append(data)
        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)
            if tag == "img":
                src = attrs_dict.get("src", "")
                if src:
                    self.images.append(src)
            elif tag == "a":
                href = attrs_dict.get("href", "")
                if href:
                    self.link_hrefs.append(href)
        def get_text(self):
            return "".join(self.text)

    url = f"https://old.reddit.com/r/{subreddit}/.rss"
    logger.info(f"Fetching posts from anonymous RSS feed: {url}")
    try:
        response = session_client.get(url, headers=DEFAULT_HEADERS, timeout=15)
        response.raise_for_status()
        if not response.content.strip():
            logger.warning("Empty response from RSS feed")
            return []
            
        root = ET.fromstring(response.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        
        posts = []
        for entry in root.findall("atom:entry", ns):
            post_id = entry.find("atom:id", ns)
            post_id_val = post_id.text if post_id is not None else ""
            if post_id_val.startswith("t3_"):
                post_id_val = post_id_val[3:]
                
            title_elem = entry.find("atom:title", ns)
            title = title_elem.text if title_elem is not None else ""
            
            link_elem = entry.find("atom:link", ns)
            permalink = link_elem.attrib.get("href", "") if link_elem is not None else ""
            
            author_elem = entry.find("atom:author/atom:name", ns)
            author = author_elem.text if author_elem is not None else "[deleted]"
            if author.startswith("/u/"):
                author = author[3:]
                
            content_elem = entry.find("atom:content", ns)
            html_content = content_elem.text if content_elem is not None else ""
            
            extractor = HTMLTextExtractor()
            extractor.feed(html_content)
            selftext = extractor.get_text().strip()
            
            # Extract image URL from RSS HTML content.
            # We ONLY want direct image URLs (i.redd.it or i.imgur.com) to guarantee high-quality memes
            # and avoid 403 blocks from external preview URLs.
            image_url = ""
            valid_img_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
            
            # 1) Check <a> href tags first — these contain the real direct URLs
            for href in extractor.link_hrefs:
                href_lower = href.lower().split("?")[0]
                if ("i.redd.it" in href_lower or "i.imgur.com" in href_lower):
                    if any(href_lower.endswith(ext) for ext in valid_img_extensions):
                        image_url = href.split("?")[0]
                        break
            
            # 2) Fallback: regex scan for direct links
            if not image_url:
                direct_patterns = _re.findall(
                    r'https?://(?:i\.redd\.it|i\.imgur\.com)/[^\s"<>?]+?(?:\.jpg|\.jpeg|\.png|\.webp|\.gif)',
                    html_content,
                    _re.IGNORECASE
                )
                if direct_patterns:
                    image_url = direct_patterns[0]
            
            # If we couldn't find a direct image link, skip this post (memes must be direct images)
            if not image_url:
                continue
                
            posts.append({
                "id": post_id_val,
                "subreddit": subreddit,
                "title": title,
                "selftext": selftext,
                "score": config.REDDIT_MIN_SCORE + 100,
                "num_comments": config.REDDIT_MIN_COMMENTS + 10,
                "over_18": False,
                "is_self": False,
                "permalink": permalink,
                "author": author,
                "pinned": False,
                "crosspost_parent": None,
                "url": image_url
            })
        return posts
    except Exception as e:
        logger.warning(f"RSS feed fetch failed for r/{subreddit}: {e}")
        return []


def fetch_posts(subreddit: str, sort: str = "top", time_filter: str = "week") -> List[RedditPost]:
    """Fetch posts from a subreddit, mapping them to RedditPost dataclasses."""
    raw_posts = _fetch_with_praw(subreddit, sort, time_filter)
    
    if not raw_posts:
        raw_posts = _fetch_anonymous_json(subreddit, sort, time_filter)
        
    if not raw_posts:
        raw_posts = _fetch_with_rss(subreddit)
        
    posts = []
    for rp in raw_posts:
        posts.append(
            RedditPost(
                id=rp.get("id", ""),
                subreddit=rp.get("subreddit", subreddit),
                title=rp.get("title", ""),
                selftext=rp.get("selftext", ""),
                score=rp.get("score", 0),
                num_comments=rp.get("num_comments", 0),
                over_18=rp.get("over_18", False),
                is_self=rp.get("is_self", True),
                permalink=rp.get("permalink", ""),
                author=rp.get("author", rp.get("author_fullname", "[deleted]")),
                pinned=rp.get("pinned", False),
                crosspost_parent=rp.get("crosspost_parent"),
                media_url=rp.get("url", "")
            )
        )
    return posts


def filter_post(post: RedditPost, processed_ids: Set[str]) -> Optional[str]:
    """
    Validate and filter a Reddit post based on system guidelines.
    Returns None if post is valid, otherwise returns a string describing the filter reason.
    """
    if post.id in processed_ids:
        return "Previously processed ID"
        
    if config.REDDIT_FILTER_NSFW and post.over_18:
        return "NSFW post"
        
    if config.REDDIT_FILTER_PINNED and post.pinned:
        return "Pinned post"
        
    if config.REDDIT_FILTER_CROSSPOSTS and post.crosspost_parent:
        return "Crosspost"
        
    if post.is_self:
        return "Self/text post (memes must be images)"

    # Ensure the post URL ends with a valid image extension: .jpg, .jpeg, .png, .webp, .gif.
    if not post.media_url:
        return "No media URL found"
        
    from urllib.parse import urlparse
    parsed = urlparse(post.media_url.lower())
    valid_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    if not parsed.path.endswith(valid_extensions):
        return f"Media URL does not point to a valid image: {post.media_url}"

    # For image posts, we just need a title
    if not post.title:
        return "No title"
        
    if post.score < config.REDDIT_MIN_SCORE:
        return f"Score too low ({post.score} < {config.REDDIT_MIN_SCORE})"
        
    if post.num_comments < config.REDDIT_MIN_COMMENTS:
        return f"Comment count too low ({post.num_comments} < {config.REDDIT_MIN_COMMENTS})"
        
    return None


def download_meme_image(url: str) -> Path:
    """Downloads the meme image from the given URL and saves it to data/output/."""
    logger.info(f"Downloading meme image from URL: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.reddit.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    
    # Determine file extension from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        ext = '.png'
    
    # Ensure OUTPUT_DIR exists
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.OUTPUT_DIR / f"meme_image{ext}"
    with open(out_path, "wb") as f:
        f.write(response.content)
        
    logger.info(f"Meme image successfully saved to {out_path} ({len(response.content)} bytes)")
    return out_path


def get_random_reddit_post() -> Optional[RedditPost]:
    """
    Fetch posts across configurable subreddits, apply filters, 
    and pick a random eligible post.
    """
    subreddits = config.SUBREDDITS
    if not subreddits:
        logger.error("No subreddits configured in config.SUBREDDITS")
        return None
        
    processed_ids = load_processed_ids()
    
    # Combine subreddits using "+" to fetch all listings in a single HTTP request.
    # This prevents triggering 429 Too Many Requests rate-limiting on cloud runners like GitHub Actions.
    combined_subs = "+".join(subreddits)
    logger.info(f"Searching subreddits for posts: r/{combined_subs}")
    posts = fetch_posts(combined_subs, config.REDDIT_SORT, config.REDDIT_TIME_FILTER)
    
    if posts:
        random.shuffle(posts)
        for post in posts:
            filter_reason = filter_post(post, processed_ids)
            if filter_reason is None:
                logger.info(f"🎉 Selected Reddit Post: r/{post.subreddit} - ID: {post.id} - Title: {post.title[:50]}...")
                return post
            else:
                logger.debug(f"Filtered out r/{post.subreddit} post {post.id}: {filter_reason}")
                
    logger.error("❌ No eligible Reddit posts found matching all filters across all subreddits.")
    return None
