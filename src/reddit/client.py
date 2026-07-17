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


_cached_redlib_instances = []

def _get_redlib_instances(exclude_anubis: bool = False) -> List[str]:
    """Get list of active public Redlib instances, cached at module level."""
    global _cached_redlib_instances
    if not _cached_redlib_instances:
        logger.info("Fetching public Redlib instances list...")
        instances = []
        try:
            r = requests.get("https://raw.githubusercontent.com/redlib-org/redlib-instances/main/instances.json", timeout=8)
            data = r.json()
            for inst in data.get("instances", []):
                url = inst.get("url")
                if url:
                    # Filter out Cloudflare instances (more likely to have bot challenges)
                    if inst.get("cloudflare", False):
                        continue
                    instances.append(url.rstrip("/"))
        except Exception as e:
            logger.warning(f"Failed to fetch public Redlib instances JSON: {e}")

        # Curated fallbacks that are historically reliable
        fallbacks = [
            "https://safereddit.com",
            "https://redlib.catsarch.com",
            "https://redlib.privacyredirect.com",
            "https://redlib.slipfox.xyz",
            "https://redlib.reallyaweso.me",
            "https://redlib.private.coffee",
            "https://redlib.perennialte.ch",
        ]
        for fb in fallbacks:
            if fb not in instances:
                instances.append(fb)

        _cached_redlib_instances = instances
        logger.info(f"Loaded {len(_cached_redlib_instances)} Redlib proxy instances.")
    
    if exclude_anubis:
        return [inst for inst in _cached_redlib_instances if "safereddit.com" not in inst]
    return _cached_redlib_instances



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
    """Fetch posts via public RSS feeds using public RSS-to-JSON API proxies as a fallback."""
    import html.parser
    import re as _re
    import urllib.parse
    
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

    rss_url = f"https://www.reddit.com/r/{subreddit}/.rss"
    encoded_url = urllib.parse.quote_plus(rss_url)
    
    proxies = [
        ("feed2json", f"https://feed2json.org/convert?url={encoded_url}"),
        ("rss2json", f"https://api.rss2json.com/v1/api.json?rss_url={encoded_url}")
    ]
    
    for name, proxy_url in proxies:
        logger.info(f"Fetching posts from RSS proxy ({name}): {proxy_url}")
        try:
            response = session_client.get(proxy_url, headers=get_headers(), timeout=15)
            response.raise_for_status()
            data = response.json()
            
            posts = []
            
            if name == "feed2json":
                items = data.get("items", [])
                for item in items:
                    post_id_val = item.get("guid", "")
                    if post_id_val.startswith("t3_"):
                        post_id_val = post_id_val[3:]
                        
                    title = item.get("title", "")
                    permalink = item.get("url", "")
                    
                    author_data = item.get("author", {})
                    author = author_data.get("name", "[deleted]") if isinstance(author_data, dict) else str(author_data)
                    if author.startswith("/u/"):
                        author = author[3:]
                        
                    html_content = item.get("content_html", "")
                    
                    extractor = HTMLTextExtractor()
                    extractor.feed(html_content)
                    selftext = extractor.get_text().strip()
                    
                    image_url = ""
                    valid_media_extensions = ('.mp4', '.webm', '.gif', '.png', '.jpg', '.jpeg', '.webp')
                    
                    for href in extractor.link_hrefs:
                        href_lower = href.lower().split("?")[0]
                        if "v.redd.it" in href_lower:
                            image_url = href
                            break
                        if ("i.redd.it" in href_lower or "i.imgur.com" in href_lower or "preview.redd.it" in href_lower):
                            if any(href_lower.endswith(ext) for ext in valid_media_extensions):
                                image_url = href.split("?")[0]
                                break
                                
                    if not image_url:
                        vredd_patterns = _re.findall(
                            r'https?://v\.redd\.it/[a-zA-Z0-9]+',
                            html_content
                        )
                        if vredd_patterns:
                            image_url = vredd_patterns[0]
                        else:
                            direct_patterns = _re.findall(
                                r'https?://(?:i\.redd\.it|i\.imgur\.com|preview\.redd\.it)/[^\s"<>?]+?(?:\.mp4|\.webm|\.gif|\.png|\.jpg|\.jpeg|\.webp)',
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
                        "url": image_url,
                        "is_rss": True
                    })
            elif name == "rss2json":
                items = data.get("items", [])
                for item in items:
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
                    
                    image_url = ""
                    valid_media_extensions = ('.mp4', '.webm', '.gif', '.png', '.jpg', '.jpeg', '.webp')
                    
                    for href in extractor.link_hrefs:
                        href_lower = href.lower().split("?")[0]
                        if "v.redd.it" in href_lower:
                            image_url = href
                            break
                        if ("i.redd.it" in href_lower or "i.imgur.com" in href_lower or "preview.redd.it" in href_lower):
                            if any(href_lower.endswith(ext) for ext in valid_media_extensions):
                                image_url = href.split("?")[0]
                                break
                                
                    if not image_url:
                        vredd_patterns = _re.findall(
                            r'https?://v\.redd\.it/[a-zA-Z0-9]+',
                            html_content
                        )
                        if vredd_patterns:
                            image_url = vredd_patterns[0]
                        else:
                            direct_patterns = _re.findall(
                                r'https?://(?:i\.redd\.it|i\.imgur\.com|preview\.redd\.it)/[^\s"<>?]+?(?:\.mp4|\.webm|\.gif|\.png|\.jpg|\.jpeg|\.webp)',
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
                        "url": image_url,
                        "is_rss": True
                    })
                    
            if posts:
                logger.info(f"Successfully fetched {len(posts)} fallback posts via RSS proxy ({name})")
                return posts
        except Exception as e:
            logger.warning(f"RSS proxy ({name}) failed for r/{subreddit}: {e}")
            
    return []


def _fetch_with_redlib(subreddit: str) -> List[dict]:
    """Fetch posts using public Redlib proxy instances without API keys."""
    instances = _get_redlib_instances(exclude_anubis=False)
    # Prioritize safereddit.com as it is historically the most stable for scraping listings
    instances = sorted(instances, key=lambda x: "safereddit.com" not in x)
    
    # Try up to 8 instances
    for instance in instances[:8]:
        url = f"{instance}/r/{subreddit}"
        logger.info(f"Fetching posts from Redlib instance: {url}")
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}
            response = requests.get(url, headers=headers, timeout=12)
            response.raise_for_status()
            response.encoding = "utf-8"
            
            html_content = response.text
            
            # If the instance returned an error or challenge, skip it
            if "anubis" in html_content.lower() or "challenge" in html_content.lower() or "verifying your browser" in html_content.lower():
                logger.warning(f"Redlib instance {instance} returned a verification challenge. Trying next...")
                continue
                
            import re
            post_blocks = re.split(r'<div class="post(?: stickied)?"', html_content)[1:]
            if not post_blocks:
                logger.warning(f"Redlib instance {instance} returned no post blocks. Trying next...")
                continue
            
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
                    media_url = instance + video_match.group(1)
                else:
                    image_match = re.search(r'<a class="post_media_lightbox" href="([^"]+)"', block)
                    if image_match:
                        media_url = image_match.group(1)
                        if media_url.startswith("/"):
                            media_url = instance + media_url
                            
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
            
            if posts:
                logger.info(f"Successfully scraped {len(posts)} posts from Redlib instance {instance}.")
                return posts
        except Exception as e:
            logger.warning(f"Redlib fetch failed for instance {instance}: {e}")
            
    logger.warning(f"All Redlib instances failed to fetch posts for r/{subreddit}.")
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
    
    only_videos = getattr(config, "ONLY_VIDEOS", True)
    if only_videos:
        is_image = parsed.path.endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff')) or "i.redd.it" in post.media_url.lower() or "preview.redd.it" in post.media_url.lower()
        if is_image:
            return "Image post (ONLY_VIDEOS is enabled)"
            
    is_reddit_hosted = "v.redd.it" in post.media_url.lower() or "reddit.com" in post.media_url.lower() or "/vid/" in post.media_url.lower() or "safereddit.com" in post.media_url.lower() or "redlib" in post.media_url.lower()
    
    is_rss = getattr(post, "is_rss", False)
    if not is_rss and config.ONLY_REDDIT_HOSTED and not is_reddit_hosted:
        return "Not a Reddit-hosted video"
        
    valid_extensions = ('.mp4', '.webm', '.gif')
    if is_rss:
        valid_extensions = ('.mp4', '.webm', '.gif', '.png', '.jpg', '.jpeg', '.webp')
        
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
    """Downloads the meme video/image/gif from the given URL and saves it to raw directory.

    Download strategy (in order of preference):
    1. Direct HTTP download for direct video/image/gif files (critical for RSS).
    2. Direct download via rotated Redlib proxy servers (credential-free, bypasses CI/GitHub Actions IP blocks).
    3. yt-dlp with --impersonate chrome (fallback for local runs).
    4. Authenticated download via Reddit OAuth Bearer token (if credentials set).
    """
    logger.info(f"Downloading meme asset from URL: {url}")

    # Determine file extension based on URL
    url_lower = url.lower().split("?")[0]
    ext = ".mp4"
    for e in [".webm", ".gif", ".jpg", ".jpeg", ".png", ".webp"]:
        if url_lower.endswith(e):
            ext = e
            break
    if ext == ".jpeg":
        ext = ".jpg"

    # Ensure RAW_DIR exists
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.RAW_DIR / f"meme_video{ext}"

    # Clean up any pre-existing files of any extension to prevent collisions
    for old in config.RAW_DIR.glob("meme_video.*"):
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass

    is_vredd = "v.redd.it" in url.lower()
    is_proxy = "/vid/" in url.lower() or "safereddit.com" in url.lower() or "redlib" in url.lower()

    # ── STRATEGY 1: Direct HTTP download for direct files (images, gifs, direct mp4/webm) ──
    is_image_or_gif = ext in (".gif", ".png", ".jpg", ".webp")
    is_direct_video = ext in (".mp4", ".webm")
    if (is_image_or_gif or is_direct_video) and not is_vredd and not is_proxy:
        logger.info(f"Direct media file detected. Attempting direct HTTP download: {url}")
        try:
            min_size = 50_000 if is_direct_video else 1000
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.reddit.com/",
            }
            response = requests.get(url, headers=headers, timeout=90, stream=True)
            response.raise_for_status()
            total = 0
            with open(out_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
            if total < min_size:
                raise Exception(f"Downloaded direct file is too small ({total} bytes)")
            logger.info(f"Direct media successfully downloaded to {out_path} ({total} bytes)")
            return out_path
        except Exception as e:
            logger.warning(f"Direct HTTP download failed: {e}. Trying other strategies...")
            if out_path.exists():
                try:
                    out_path.unlink()
                except Exception:
                    pass

    # ── STRATEGY 2: Rotated Proxy Download for Reddit Video ──
    # If it is a Reddit video, we can download the video directly from our rotated list of working Redlib instances.
    # This bypasses the GitHub Actions IP block and doesn't require any API credentials/cookies.
    if is_proxy or is_vredd or post_id:
        video_id = None
        import re
        if "/vid/" in url:
            vid_match = re.search(r'/vid/([^/]+)', url)
            if vid_match:
                video_id = vid_match.group(1)
        elif "v.redd.it/" in url:
            vid_match = re.search(r'v\.redd\.it/([^/?#]+)', url)
            if vid_match:
                video_id = vid_match.group(1)

        if video_id:
            # First: Try Direct Reddit CDN (Fastly) Video + Audio download & merge.
            # Fastly CDN does not block GitHub Actions, so we can download both video and audio directly.
            logger.info(f"Attempting direct CDN (v.redd.it) download for Video ID: {video_id}")
            temp_video_path = config.RAW_DIR / f"temp_video_{video_id}.mp4"
            temp_audio_path = config.RAW_DIR / f"temp_audio_{video_id}.mp4"
            
            for p in (temp_video_path, temp_audio_path):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Referer": "https://www.reddit.com/"
            }

            video_candidates = [
                f"https://v.redd.it/{video_id}/CMAF_1080.mp4",
                f"https://v.redd.it/{video_id}/CMAF_720.mp4",
                f"https://v.redd.it/{video_id}/CMAF_480.mp4",
                f"https://v.redd.it/{video_id}/CMAF_360.mp4",
                f"https://v.redd.it/{video_id}/DASH_1080.mp4",
                f"https://v.redd.it/{video_id}/DASH_720.mp4",
                f"https://v.redd.it/{video_id}/DASH_480.mp4",
                f"https://v.redd.it/{video_id}/DASH_360.mp4"
            ]
            audio_candidates = [
                f"https://v.redd.it/{video_id}/CMAF_AUDIO_128.mp4",
                f"https://v.redd.it/{video_id}/CMAF_AUDIO_64.mp4",
                f"https://v.redd.it/{video_id}/DASH_audio.mp4",
                f"https://v.redd.it/{video_id}/DASH_AUDIO.mp4",
                f"https://v.redd.it/{video_id}/HLS_AUDIO_128.mp4",
                f"https://v.redd.it/{video_id}/DASH_AUDIO_128.mp4"
            ]

            video_downloaded = False
            for v_url in video_candidates:
                try:
                    r = requests.head(v_url, headers=headers, timeout=5)
                    if r.status_code == 200:
                        logger.info(f"Found direct video track: {v_url}. Downloading...")
                        _direct_http_download(v_url, temp_video_path, extra_headers=headers)
                        if temp_video_path.exists() and temp_video_path.stat().st_size > 50000:
                            video_downloaded = True
                            break
                except Exception as e:
                    logger.warning(f"Failed direct video download check for {v_url}: {e}")

            audio_downloaded = False
            if video_downloaded:
                for a_url in audio_candidates:
                    try:
                        r = requests.head(a_url, headers=headers, timeout=5)
                        if r.status_code == 200:
                            logger.info(f"Found direct audio track: {a_url}. Downloading...")
                            _direct_http_download(a_url, temp_audio_path, extra_headers=headers)
                            if temp_audio_path.exists() and temp_audio_path.stat().st_size > 1000:
                                audio_downloaded = True
                                break
                    except Exception as e:
                        logger.warning(f"Failed direct audio download check for {a_url}: {e}")

            if video_downloaded:
                if audio_downloaded:
                    import subprocess
                    logger.info("Muxing video and audio tracks via FFmpeg...")
                    cmd = ["ffmpeg", "-y", "-i", str(temp_video_path), "-i", str(temp_audio_path), "-c", "copy", str(out_path)]
                    try:
                        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
                        if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 50000:
                            logger.info(f"Successfully downloaded and merged video+audio directly from Reddit CDN to {out_path}!")
                            for p in (temp_video_path, temp_audio_path):
                                p.unlink(missing_ok=True)
                            return out_path
                    except Exception as e:
                        logger.error(f"FFmpeg direct merge failed: {e}")
                else:
                    # Video only (silent)
                    try:
                        import shutil
                        shutil.copy2(temp_video_path, out_path)
                        logger.info(f"Successfully downloaded video directly from Reddit CDN to {out_path} (silent video/no audio track found)")
                        temp_video_path.unlink(missing_ok=True)
                        return out_path
                    except Exception as e:
                        logger.error(f"Failed to use silent video path: {e}")

            # Clean up temp files if direct CDN download was incomplete or failed
            for p in (temp_video_path, temp_audio_path):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

            # Fallback to Strategy 2 (Redlib proxy)
            instances = _get_redlib_instances(exclude_anubis=True)
            qualities = ["720", "1080", "480", "360"]
            orig_quality_match = re.search(r'/cmaf/(\d+)\.mp4', url)
            if orig_quality_match:
                orig_quality = orig_quality_match.group(1)
                if orig_quality in qualities:
                    qualities.remove(orig_quality)
                qualities.insert(0, orig_quality)

            logger.info(f"Attempting proxy download pool for Video ID: {video_id} using {len(instances)} instances")
            for instance in instances[:10]:
                for q in qualities:
                    proxy_video_url = f"{instance}/vid/{video_id}/cmaf/{q}.mp4"
                    logger.info(f"Attempting proxy download from: {proxy_video_url}")
                    try:
                        temp_proxy_video = config.RAW_DIR / f"temp_proxy_video_{video_id}.mp4"
                        _direct_http_download(proxy_video_url, temp_proxy_video)
                        if temp_proxy_video.exists() and temp_proxy_video.stat().st_size > 50000:
                            logger.info(f"Proxy video downloaded. Now trying to retrieve audio track directly...")
                            temp_audio_path = config.RAW_DIR / f"temp_audio_{video_id}.mp4"
                            audio_downloaded = False
                            for a_url in audio_candidates:
                                try:
                                    r = requests.head(a_url, headers=headers, timeout=5)
                                    if r.status_code == 200:
                                        _direct_http_download(a_url, temp_audio_path, extra_headers=headers)
                                        if temp_audio_path.exists() and temp_audio_path.stat().st_size > 1000:
                                            audio_downloaded = True
                                            break
                                except Exception:
                                    pass

                            if audio_downloaded:
                                import subprocess
                                logger.info("Muxing proxy video and direct audio via FFmpeg...")
                                cmd = ["ffmpeg", "-y", "-i", str(temp_proxy_video), "-i", str(temp_audio_path), "-c", "copy", str(out_path)]
                                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
                                for p in (temp_proxy_video, temp_audio_path):
                                    p.unlink(missing_ok=True)
                                if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 50000:
                                    logger.info(f"Meme video with audio successfully downloaded and merged via proxy to {out_path}")
                                    return out_path
                            else:
                                import shutil
                                shutil.copy2(temp_proxy_video, out_path)
                                temp_proxy_video.unlink(missing_ok=True)
                                logger.info(f"Meme video successfully downloaded via proxy (no audio found/silent) to {out_path}")
                                return out_path
                    except Exception as e:
                        logger.warning(f"Proxy download failed for {proxy_video_url}: {e}")
                        for p in (config.RAW_DIR / f"temp_proxy_video_{video_id}.mp4", config.RAW_DIR / f"temp_audio_{video_id}.mp4"):
                            try:
                                p.unlink(missing_ok=True)
                            except Exception:
                                pass

    # ── STRATEGY 3: yt-dlp with --impersonate chrome (fallback for local runs) ──
    if post_id or is_vredd or is_proxy:
        reddit_url = (
            f"https://www.reddit.com/comments/{post_id}"
            if post_id
            else url
        )
        logger.info(f"Fallback to yt-dlp impersonation: {reddit_url}")
        try:
            _ytdlp_impersonate_download(reddit_url, out_path)
            logger.info(f"Meme video successfully downloaded (yt-dlp impersonation) to {out_path}")
            return out_path
        except Exception as e:
            logger.warning(f"yt-dlp impersonation download failed ({e}). Trying OAuth token...")

    # ── STRATEGY 4: Reddit OAuth Bearer token (requires REDDIT_CLIENT_ID set) ──
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

    # ── STRATEGY 5: External (non-Reddit) URLs — plain yt-dlp ───────────────
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
    Fetch posts across configurable subreddits or RSS feeds using a prioritized provider chain,
    apply filters, and pick an eligible post using weighted random selection.
    """
    subreddits = config.SUBREDDITS
    if not subreddits:
        logger.error("No subreddits configured in config.SUBREDDITS")
        return None
        
    processed_ids = load_processed_ids()
    if exclude_ids:
        processed_ids = set(processed_ids).union(exclude_ids)

    # 1. Instantiate the active provider list based on config priority
    from src.reddit.providers import RedditPrawProvider, RedditRSSProvider, RedditAnonymousProvider
    
    providers = []
    if config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET:
        providers.append(RedditPrawProvider())
        
    if getattr(config, "RSS_ENABLED", False):
        providers.append(RedditRSSProvider())
        
    providers.append(RedditAnonymousProvider())

    # 2. Iterate through providers in sequence. First one to yield fresh valid posts wins.
    valid_posts = []
    selected_provider_name = ""

    for provider in providers:
        logger.info(f"Attempting ingestion via provider: {provider.name()}")
        try:
            posts = provider.fetch_posts(subreddits)
            if not posts:
                logger.info(f"Provider {provider.name()} returned 0 posts.")
                continue
                
            provider_valid_posts = []
            for post in posts:
                filter_reason = filter_post(post, processed_ids)
                if filter_reason is None:
                    provider_valid_posts.append(post)
                else:
                    logger.debug(f"Filtered out r/{post.subreddit} post {post.id} ({provider.name()}): {filter_reason}")
                    
            if provider_valid_posts:
                logger.info(f"🎉 Provider {provider.name()} succeeded with {len(provider_valid_posts)} valid posts.")
                valid_posts = provider_valid_posts
                selected_provider_name = provider.name()
                break
            else:
                logger.warning(f"Provider {provider.name()} had no fresh valid posts (all filtered/processed). Trying next provider...")
        except Exception as e:
            logger.error(f"Error executing provider {provider.name()}: {e}. Trying next provider...")

    if not valid_posts:
        logger.error("❌ No eligible posts found matching all filters across all subreddits and providers.")
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
        f"🎉 Weighted Selected Post via {selected_provider_name}: r/{selected_post.subreddit} (base weight: {weight_val}) - "
        f"ID: {selected_post.id} - Title: {selected_post.title[:50]}..."
    )
    return selected_post
