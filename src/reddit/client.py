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

# User Agent pool for rotation to bypass CDN bot protection
USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Reddit/2023.23.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S906B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
]

def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
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


def load_subreddit_history() -> List[str]:
    """Load the list of recently processed subreddits."""
    history_file = config.DB_DIR / "subreddit_history.json"
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning(f"Failed to read subreddit history: {e}")
    return []


def save_subreddit_history(subreddit: str) -> None:
    """Save the selected subreddit to history for diversity tracking."""
    history_file = config.DB_DIR / "subreddit_history.json"
    history = load_subreddit_history()
    history.append(subreddit)
    # Keep only the last 5 entries
    history = history[-5:]
    try:
        config.DB_DIR.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save subreddit history: {e}")


def save_processed_id(post_id: str, subreddit: Optional[str] = None) -> None:
    """Save a processed Reddit post ID to prevent duplicates."""
    processed = load_processed_ids()
    processed.add(post_id)
    try:
        config.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(config.HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed)), f, indent=2)
        logger.info(f"Saved Reddit ID {post_id} to history database")
        if subreddit:
            save_subreddit_history(subreddit)
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
        response = session_client.get(url, params=params, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        children = data.get("data", {}).get("children", [])
        return [child.get("data", {}) for child in children]
    except Exception as e:
        logger.warning(f"Public Reddit API fetch failed for r/{subreddit}: {e}")
        return []


# Module-level cache so we only build the Reddit client once per run
_praw_reddit_instance = None


def _get_praw_reddit():
    """Return a cached PRAW Reddit instance (app-only OAuth)."""
    global _praw_reddit_instance
    if _praw_reddit_instance is not None:
        return _praw_reddit_instance
    try:
        import praw
    except ImportError:
        return None
    if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
        return None
    _praw_reddit_instance = praw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET,
        user_agent=config.REDDIT_USER_AGENT,
    )
    return _praw_reddit_instance


def _get_reddit_bearer_token() -> str:
    """Return the current OAuth Bearer token from PRAW (triggers token refresh if needed)."""
    reddit = _get_praw_reddit()
    if reddit is None:
        return ""
    try:
        # Accessing .me() on an app-only instance forces token generation without user login
        # We just need the token; ignore the response
        _ = reddit.auth.limits  # lightweight property access that triggers token fetch
        token = reddit._core._authorizer.access_token
        return token or ""
    except Exception:
        return ""


def _fetch_with_praw(subreddit: str, sort: str, time_filter: str) -> List[dict]:
    """Fetch posts using PRAW (Python Reddit API Wrapper) if credentials are provided."""
    reddit = _get_praw_reddit()
    if reddit is None:
        logger.debug("PRAW unavailable or credentials not set. Falling back to public feeds.")
        return []

    logger.info(f"Fetching posts via PRAW for r/{subreddit} (sort: {sort}, time: {time_filter})")
    try:
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
            # Extract real v.redd.it fallback_url when available — this is the key
            # that lets us download the video with OAuth auth instead of a browser.
            media_url = getattr(post, "url", "")
            reddit_video = None
            raw_media = getattr(post, "media", None) or {}
            if isinstance(raw_media, dict) and "reddit_video" in raw_media:
                reddit_video = raw_media["reddit_video"]
                # Prefer fallback_url (plain MP4) over dash_url (requires DASH muxing)
                fallback = reddit_video.get("fallback_url", "")
                if fallback:
                    media_url = fallback
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
                "url": media_url,
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
        response = session_client.get(url, headers=get_headers(), timeout=15)
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
            
            # Extract video URL from RSS HTML content.
            # We ONLY want direct video URLs (i.redd.it or i.imgur.com) to guarantee high-quality memes
            # and avoid 403 blocks from external preview URLs.
            image_url = ""
            valid_video_extensions = ('.mp4', '.webm', '.gif')
            
            # 1) Check <a> href tags first — these contain the real direct URLs
            for href in extractor.link_hrefs:
                href_lower = href.lower().split("?")[0]
                if ("i.redd.it" in href_lower or "i.imgur.com" in href_lower):
                    if any(href_lower.endswith(ext) for ext in valid_video_extensions):
                        image_url = href.split("?")[0]
                        break
            
            # 2) Fallback: regex scan for direct links
            if not image_url:
                direct_patterns = _re.findall(
                    r'https?://(?:i\.redd\.it|i\.imgur\.com)/[^\s"<>?]+?(?:\.mp4|\.webm|\.gif)',
                    html_content,
                    _re.IGNORECASE
                )
                if direct_patterns:
                    image_url = direct_patterns[0]
            
            # If we couldn't find a direct video link, skip this post
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


def _fetch_with_rss2json(subreddit: str) -> List[dict]:
    """Fetch posts using the free public rss2json.com API as a third-party proxy fallback."""
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

    # Encode the rss_url parameter properly
    rss_url = f"https://www.reddit.com/r/{subreddit}/.rss"
    encoded_url = urllib.parse.quote_plus(rss_url)
    url = f"https://api.rss2json.com/v1/api.json?rss_url={encoded_url}"
    logger.info(f"Fetching posts from rss2json proxy for r/{subreddit}")
    try:
        response = session_client.get(url, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        
        posts = []
        for item in data.get("items", []):
            post_id_val = item.get("guid", "")
            if post_id_val.startswith("t3_"):
                post_id_val = post_id_val[3:]
                
            title = item.get("title", "")
            permalink = item.get("link", "")
            
            author = item.get("author", "[deleted]")
            if author.startswith("/u/"):
                author = author[3:]
                
            html_content = item.get("description", "") or item.get("content", "")
            
            extractor = HTMLTextExtractor()
            extractor.feed(html_content)
            selftext = extractor.get_text().strip()
            
            # Extract video URL
            image_url = ""
            valid_video_extensions = ('.mp4', '.webm', '.gif')
            
            # 1) Check <a> href tags first
            for href in extractor.link_hrefs:
                href_lower = href.lower().split("?")[0]
                if ("i.redd.it" in href_lower or "i.imgur.com" in href_lower):
                    if any(href_lower.endswith(ext) for ext in valid_video_extensions):
                        image_url = href.split("?")[0]
                        break
            
            # 2) Fallback: regex scan
            if not image_url:
                direct_patterns = _re.findall(
                    r'https?://(?:i\.redd\.it|i\.imgur\.com)/[^\s"<>?]+?(?:\.mp4|\.webm|\.gif)',
                    html_content,
                    _re.IGNORECASE
                )
                if direct_patterns:
                    image_url = direct_patterns[0]
            
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
        logger.warning(f"rss2json proxy fetch failed for r/{subreddit}: {e}")
        return []


def _fetch_with_redlib(subreddit: str) -> List[dict]:
    """Fetch posts using a public Redlib proxy instance (safereddit.com) without API keys."""
    url = f"https://safereddit.com/r/{subreddit}"
    logger.info(f"Fetching posts from Redlib instance: {url}")
    try:
        headers = {"User-Agent": "RedditShortsCuratorBot/1.0.0 (by /u/husai)"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
        
        html_content = response.text
        # Split using a regex to only match class="post" or class="post stickied"
        # This avoids splitting on post_media_content, post_score, post_body, etc.
        import re
        post_blocks = re.split(r'<div class="post(?: stickied)?"', html_content)[1:]
        
        posts = []
        for block in post_blocks:
            # Extract ID
            id_match = re.search(r'id="([^"]+)"', block)
            if not id_match:
                continue
            post_id = id_match.group(1)
            
            # Extract Title (ignoring flairs)
            title = "FAILED"
            h2_match = re.search(r'<h2 class="post_title">(.*?)</h2>', block, re.DOTALL)
            if h2_match:
                h2_content = h2_match.group(1)
                # Find <a> tags that are not flairs
                for a_match in re.finditer(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', h2_content, re.DOTALL):
                    tag_html = a_match.group(0)
                    tag_text = a_match.group(2)
                    if "class=\"post_flair\"" not in tag_html and "class='post_flair'" not in tag_html:
                        import html
                        title = html.unescape(re.sub(r'<[^>]*>', '', tag_text).strip())
                        break
            if title == "FAILED":
                continue
            
            # Extract Author
            author_match = re.search(r'<a class="post_author[^"]*" href="/u/([^"]+)">', block)
            author = author_match.group(1) if author_match else "[deleted]"
            
            # Extract Score
            score_match = re.search(r'<div class="post_score"[^>]*>\s*(.*?)\s*<span class="label">', block, re.DOTALL)
            score = 0
            if score_match:
                score_str = score_match.group(1).strip().lower()
                try:
                    if 'k' in score_str:
                        score = int(float(score_str.replace('k', '')) * 1000)
                    else:
                        score = int(score_str)
                except ValueError:
                    score = 1000
                    
            # Extract Comments Count
            comments_match = re.search(r'class="post_comments"[^>]*>\s*(.*?)\s*comments\s*</a>', block, re.DOTALL)
            num_comments = 0
            if comments_match:
                comments_str = comments_match.group(1).strip().lower()
                try:
                    if 'k' in comments_str:
                        num_comments = int(float(comments_str.replace('k', '')) * 1000)
                    else:
                        num_comments = int(comments_str)
                except ValueError:
                    num_comments = 100
            
            # Extract Media Link (Video sources)
            media_url = ""
            video_match = re.search(r'<source src="(/vid/[^"]+)" type="video/mp4"', block)
            if video_match:
                media_url = "https://safereddit.com" + video_match.group(1)
            else:
                image_match = re.search(r'<a class="post_media_lightbox" href="([^"]+)"', block)
                if image_match:
                    media_url = image_match.group(1)
                    if media_url.startswith("/"):
                        media_url = "https://safereddit.com" + media_url
                        
            if not media_url:
                continue
                
            posts.append({
                "id": post_id,
                "subreddit": subreddit,
                "title": title,
                "selftext": "",
                "score": score,
                "num_comments": num_comments,
                "over_18": False,
                "is_self": False,
                "permalink": f"/r/{subreddit}/comments/{post_id}",
                "author": author,
                "pinned": False,
                "crosspost_parent": None,
                "url": media_url
            })
        logger.info(f"Successfully scraped {len(posts)} posts from Redlib.")
        return posts
    except Exception as e:
        logger.warning(f"Redlib fetch failed for r/{subreddit}: {e}")
        return []


def fetch_posts(subreddit: str, sort: str = "top", time_filter: str = "week") -> List[RedditPost]:
    """Fetch posts from a subreddit, mapping them to RedditPost dataclasses."""
    raw_posts = _fetch_with_praw(subreddit, sort, time_filter)
    
    if not raw_posts:
        raw_posts = _fetch_with_redlib(subreddit)
        
    if not raw_posts:
        raw_posts = _fetch_anonymous_json(subreddit, sort, time_filter)
        
    if not raw_posts:
        raw_posts = _fetch_with_rss(subreddit)
        
    if not raw_posts:
        raw_posts = _fetch_with_rss2json(subreddit)
        
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
        return "Self/text post (memes must be videos)"

    # Ensure the post URL ends with a valid video extension or is Reddit-hosted.
    if not post.media_url:
        return "No media URL found"
        
    from urllib.parse import urlparse
    parsed = urlparse(post.media_url.lower())
    is_reddit_hosted = "v.redd.it" in post.media_url.lower() or "reddit.com" in post.media_url.lower() or "safereddit.com" in post.media_url.lower()
    
    if config.ONLY_REDDIT_HOSTED and not is_reddit_hosted:
        return "Not a Reddit-hosted video"
        
    valid_extensions = ('.mp4', '.webm', '.gif')
    if not is_reddit_hosted and not parsed.path.endswith(valid_extensions):
        return f"Media URL does not point to a valid video: {post.media_url}"

    # For video posts, we just need a title
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
def download_meme_video(url: str, post_id: Optional[str] = None) -> Path:
    """Downloads the meme video from the given URL and saves it to raw directory.

    Download strategy (in order of preference):
    1. yt-dlp with --impersonate chrome via the official reddit.com/comments URL.
       Uses curl-cffi TLS fingerprinting to bypass Reddit's bot detection — the
       official yt-dlp solution. No API keys or cookies needed.
    2. Authenticated download via Reddit OAuth Bearer token (if credentials set).
    3. Direct HTTP download from Redlib proxy URL as last resort.
    """
    logger.info(f"Downloading meme video from URL: {url}")

    # Ensure RAW_DIR exists
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.RAW_DIR / "meme_video.mp4"

    # Clean up any pre-existing files to prevent collisions
    out_path.unlink(missing_ok=True)
    for old in config.RAW_DIR.glob("meme_video.*"):
        old.unlink(missing_ok=True)

    is_vredd = "v.redd.it" in url.lower()
    is_proxy = "safereddit.com" in url.lower() or "redlib." in url.lower()

    # ── STRATEGY 1: yt-dlp with --impersonate chrome (no credentials needed) ──
    # curl-cffi spoofs the TLS fingerprint so Reddit can't tell it's a bot.
    # We always prefer the canonical reddit.com/comments URL for best quality.
    # If post_id is available, use it; otherwise try the URL as-is.
    if post_id or is_vredd or is_proxy:
        reddit_url = (
            f"https://www.reddit.com/comments/{post_id}"
            if post_id
            else url  # v.redd.it or proxy URL passed directly
        )
        logger.info(f"Reddit-hosted video detected. Downloading via yt-dlp --impersonate: {reddit_url}")
        try:
            _ytdlp_impersonate_download(reddit_url, out_path)
            logger.info(f"Meme video successfully downloaded (impersonation) to {out_path}")
            return out_path
        except Exception as e:
            logger.warning(f"yt-dlp impersonation download failed ({e}). Trying OAuth token...")

    # ── STRATEGY 2: Reddit OAuth Bearer token (requires REDDIT_CLIENT_ID set) ──
    if is_vredd:
        bearer = _get_reddit_bearer_token()
        if bearer:
            logger.info(f"Trying authenticated v.redd.it download: {url}")
            try:
                _direct_http_download(
                    url, out_path,
                    extra_headers={
                        "Authorization": f"Bearer {bearer}",
                        "User-Agent": config.REDDIT_USER_AGENT,
                    }
                )
                logger.info(f"Meme video successfully downloaded (authenticated) to {out_path}")
                return out_path
            except Exception as e:
                logger.warning(f"Authenticated download failed ({e}). Trying proxy fallback...")

    # ── STRATEGY 3: Direct HTTP from proxy URL ───────────────────────────────
    if is_proxy:
        logger.info(f"Trying direct HTTP download from proxy: {url}")
        try:
            _direct_http_download(url, out_path)
            logger.info(f"Meme video downloaded via direct HTTP to {out_path}")
            return out_path
        except Exception as e:
            raise Exception(f"All download strategies failed for Reddit video (post_id={post_id}): {e}") from e

    if is_vredd:
        raise Exception(
            f"v.redd.it download failed. Install curl-cffi (`pip install curl-cffi`) "
            f"or set REDDIT_CLIENT_ID/SECRET in .env to enable authenticated downloads."
        )

    # ── STRATEGY 4: External (non-Reddit) URLs — plain yt-dlp ───────────────
    logger.info(f"External video URL detected. Downloading via yt-dlp: {url}")
    _ytdlp_generic_download(url, out_path)
    logger.info(f"Meme video successfully downloaded via yt-dlp to {out_path}")
    return out_path


def _ytdlp_impersonate_download(url: str, out_path: Path, timeout: int = 120) -> None:
    """Download a Reddit video via yt-dlp using TLS browser impersonation.

    Requires the curl-cffi package (listed in requirements.txt).
    yt-dlp automatically uses it when --impersonate is passed, which makes the
    TLS handshake indistinguishable from a real Chrome browser — bypassing
    Reddit's bot detection without any API keys or cookies.

    Reddit serves video and audio as separate DASH streams; --merge-output-format
    ensures ffmpeg muxes them into a single MP4 file.
    """
    import subprocess
    cmd = [
        "yt-dlp",
        "--impersonate", "chrome",       # curl-cffi TLS fingerprint spoofing
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/fallback",
        "--merge-output-format", "mp4",  # mux DASH video+audio into one MP4
        "--output", str(out_path),
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise Exception(f"yt-dlp --impersonate failed: {result.stderr.strip()}")

    if not out_path.exists():
        candidates = list(out_path.parent.glob("meme_video.*"))
        if candidates:
            candidates[0].rename(out_path)
        else:
            raise FileNotFoundError("yt-dlp did not produce the expected output file.")



def _direct_http_download(
    url: str,
    out_path: Path,
    timeout: int = 90,
    extra_headers: Optional[dict] = None,
) -> None:
    """Stream-download a video file over plain HTTP."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.reddit.com/",
    }
    if extra_headers:
        headers.update(extra_headers)

    response = requests.get(url, headers=headers, timeout=timeout, stream=True)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        raise Exception(f"Server returned HTML instead of video (Content-Type: {content_type})")

    total = 0
    with open(out_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                total += len(chunk)

    if total < 50_000:
        out_path.unlink(missing_ok=True)
        raise Exception(f"Downloaded file is too small ({total} bytes) – probably not a valid video")


def _ytdlp_generic_download(url: str, out_path: Path, timeout: int = 120) -> None:
    """Download an external (non-Reddit) video URL via yt-dlp."""
    import subprocess
    cmd = [
        "yt-dlp",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "--output", str(out_path),
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise Exception(f"yt-dlp failed: {result.stderr.strip()}")

    if not out_path.exists():
        candidates = list(out_path.parent.glob("meme_video.*"))
        if candidates:
            candidates[0].rename(out_path)
        else:
            raise FileNotFoundError("yt-dlp did not produce the expected output file.")


def get_random_reddit_post(exclude_ids: Optional[Set[str]] = None) -> Optional[RedditPost]:
    """
    Fetch posts across configurable subreddits, apply filters,
    and pick an eligible post using weighted random selection
    to prioritize preferred subreddits while enforcing diversity.
    """
    subreddits = config.SUBREDDITS
    if not subreddits:
        logger.error("No subreddits configured in config.SUBREDDITS")
        return None
        
    processed_ids = load_processed_ids()
    if exclude_ids:
        processed_ids = set(processed_ids).union(exclude_ids)
    
    # Combine subreddits using "+" to fetch all listings in a single HTTP request.
    # This prevents triggering 429 Too Many Requests rate-limiting on cloud runners like GitHub Actions.
    combined_subs = "+".join(subreddits)
    logger.info(f"Searching subreddits for posts: r/{combined_subs}")
    posts = fetch_posts(combined_subs, config.REDDIT_SORT, config.REDDIT_TIME_FILTER)
    
    # Filter out ineligible posts first
    valid_posts = []
    if posts:
        for post in posts:
            filter_reason = filter_post(post, processed_ids)
            if filter_reason is None:
                valid_posts.append(post)
            else:
                logger.debug(f"Filtered out r/{post.subreddit} post {post.id}: {filter_reason}")
                
    # Fallback to individual fetching if no valid posts were found from the combined feed
    if not valid_posts:
        logger.warning("Combined feed failed/empty or contained no fresh valid posts. Falling back to individual subreddit fetching.")
        shuffled_subs = list(subreddits)
        random.shuffle(shuffled_subs)
        for sub in shuffled_subs:
            logger.info(f"Attempting fallback fetch for individual subreddit: r/{sub}")
            sub_posts = fetch_posts(sub, config.REDDIT_SORT, config.REDDIT_TIME_FILTER)
            if sub_posts:
                sub_valid_count = 0
                for post in sub_posts:
                    filter_reason = filter_post(post, processed_ids)
                    if filter_reason is None:
                        valid_posts.append(post)
                        sub_valid_count += 1
                    else:
                        logger.debug(f"Filtered out r/{post.subreddit} post {post.id}: {filter_reason}")
                if sub_valid_count > 0:
                    logger.info(f"Found {sub_valid_count} valid posts in r/{sub}")
                if len(valid_posts) >= 5:
                    break
            # Pause briefly to respect Reddit rate limits
            time.sleep(1.5)

    if not valid_posts:
        logger.error("❌ No eligible Reddit posts found matching all filters across all subreddits.")
        return None

    # Load subreddit history to implement a soft diversity penalty
    recent_subreddits = load_subreddit_history()
    
    # Calculate selection weights for each valid post
    weights = []
    for post in valid_posts:
        # Get base configured weight
        base_w = config.SUBREDDIT_WEIGHTS.get(post.subreddit, 1.0)
        
        # Apply a penalty if the subreddit was used recently to mix different styles naturally
        if recent_subreddits:
            if post.subreddit == recent_subreddits[-1]:
                base_w *= 0.1  # Heavy penalty for immediate repeat
            elif post.subreddit in recent_subreddits[-2:]:
                base_w *= 0.3  # Medium penalty
            elif post.subreddit in recent_subreddits:
                base_w *= 0.6  # Light penalty
                
        weights.append(max(0.01, base_w))

    # Perform weighted random choice
    selected_post = random.choices(valid_posts, weights=weights, k=1)[0]
    weight_val = config.SUBREDDIT_WEIGHTS.get(selected_post.subreddit, 1.0)
    logger.info(
        f"🎉 Weighted Selected Reddit Post: r/{selected_post.subreddit} (base weight: {weight_val}) - "
        f"ID: {selected_post.id} - Title: {selected_post.title[:50]}..."
    )
    return selected_post
