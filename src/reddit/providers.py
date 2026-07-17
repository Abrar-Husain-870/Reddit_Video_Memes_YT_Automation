import logging
import requests
import xml.etree.ElementTree as ET
import re
import urllib.parse
from typing import List, Optional, Set
from pathlib import Path
import html

import config
from src.logger import logger
from src.reddit.models import RedditPost

# Reuse the requests session client from client if available, or create one
session_client = requests.Session()

class BaseProvider:
    """Abstract base class for data ingestion providers."""
    def name(self) -> str:
        raise NotImplementedError
        
    def fetch_posts(self, subreddits: List[str]) -> List[RedditPost]:
        """Fetch posts from the provider."""
        raise NotImplementedError


class RedditPrawProvider(BaseProvider):
    """Ingest posts using official Reddit API (PRAW wrapper)."""
    def name(self) -> str:
        return "Reddit PRAW API"
        
    def fetch_posts(self, subreddits: List[str]) -> List[RedditPost]:
        # PRAW import is done inside to avoid global dependency import if not used
        try:
            import praw
        except ImportError:
            logger.debug("PRAW is not installed. PRAW provider unavailable.")
            return []
            
        if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
            logger.debug("Reddit API credentials not set. PRAW provider unavailable.")
            return []
            
        try:
            reddit = praw.Reddit(
                client_id=config.REDDIT_CLIENT_ID,
                client_secret=config.REDDIT_CLIENT_SECRET,
                user_agent=config.REDDIT_USER_AGENT,
            )
            # Access Limits to trigger token refresh and check credentials
            _ = reddit.auth.limits
        except Exception as e:
            logger.warning(f"Failed to authenticate Reddit PRAW client: {e}")
            return []
            
        posts = []
        combined_subs = "+".join(subreddits)
        logger.info(f"Fetching posts via PRAW for r/{combined_subs} (sort: {config.REDDIT_SORT})")
        
        try:
            sub = reddit.subreddit(combined_subs)
            if config.REDDIT_SORT == "top":
                feed = sub.top(time_filter=config.REDDIT_TIME_FILTER, limit=50)
            elif config.REDDIT_SORT == "new":
                feed = sub.new(limit=50)
            elif config.REDDIT_SORT == "rising":
                feed = sub.rising(limit=50)
            else:
                feed = sub.hot(limit=50)
                
            for post in feed:
                media_url = getattr(post, "url", "")
                raw_media = getattr(post, "media", None) or {}
                if isinstance(raw_media, dict) and "reddit_video" in raw_media:
                    reddit_video = raw_media["reddit_video"]
                    fallback = reddit_video.get("fallback_url", "")
                    if fallback:
                        media_url = fallback
                        
                posts.append(
                    RedditPost(
                        id=post.id,
                        subreddit=post.subreddit.display_name,
                        title=post.title,
                        selftext=post.selftext,
                        score=post.score,
                        num_comments=post.num_comments,
                        over_18=post.over_18,
                        is_self=post.is_self,
                        permalink=post.permalink,
                        author=post.author.name if post.author else "[deleted]",
                        pinned=getattr(post, "pinned", False),
                        crosspost_parent=getattr(post, "crosspost_parent", None),
                        media_url=media_url
                    )
                )
            return posts
        except Exception as e:
            logger.error(f"PRAW fetch failed: {e}")
            return []


class RedditRSSProvider(BaseProvider):
    """Ingest posts from RSS feeds (supporting manually configured feeds and dynamic Redlib RSS)."""
    def name(self) -> str:
        return "Reddit RSS Feeds"
        
    def fetch_posts(self, subreddits: List[str]) -> List[RedditPost]:
        if not getattr(config, "RSS_ENABLED", False):
            logger.debug("RSS feeds are disabled in config.")
            return []
            
        feeds = getattr(config, "RSS_FEEDS", [])
        posts = []
        
        # 1. Fetch from manually configured feeds if present (e.g. RSS.app)
        if feeds:
            for feed_url in feeds:
                logger.info(f"Fetching manually configured RSS feed: {feed_url}")
                try:
                    r = session_client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                    r.raise_for_status()
                    feed_posts = self._parse_rss_content(r.text, feed_url)
                    logger.info(f"Parsed {len(feed_posts)} posts from feed {feed_url}")
                    posts.extend(feed_posts)
                except Exception as e:
                    logger.warning(f"Failed to fetch/parse RSS feed {feed_url}: {e}")
                    
        # 2. Dynamic RSS Fallback: If no manual feeds are configured, or if manual feeds returned no posts,
        # dynamically fetch RSS feeds for all configured subreddits via our public RSS JSON proxies.
        if not posts:
            from src.reddit.client import get_headers
            import urllib.parse
            
            combined_subs = "+".join(subreddits)
            logger.info(f"Fetching RSS feed for r/{combined_subs} dynamically via public RSS-to-JSON proxies")
            
            encoded_url = urllib.parse.quote_plus(f"https://www.reddit.com/r/{combined_subs}/.rss")
            proxies = [
                ("feed2json", f"https://feed2json.org/convert?url={encoded_url}"),
                ("rss2json", f"https://api.rss2json.com/v1/api.json?rss_url={encoded_url}")
            ]
            
            success = False
            for name, proxy_url in proxies:
                logger.info(f"Attempting dynamic RSS fetch from proxy ({name}): {proxy_url}")
                try:
                    r = session_client.get(proxy_url, headers=get_headers(), timeout=15)
                    r.raise_for_status()
                    data = r.json()
                    
                    items = data.get("items", [])
                    feed_posts = self._parse_rss_json_content(items, name)
                    if feed_posts:
                        logger.info(f"🎉 Successfully fetched {len(feed_posts)} posts from dynamic RSS proxy ({name})")
                        posts.extend(feed_posts)
                        success = True
                        break
                    else:
                        logger.warning(f"RSS proxy ({name}) returned empty feed or parsed 0 posts.")
                except Exception as e:
                    logger.warning(f"Failed to fetch dynamic RSS from proxy ({name}): {e}")
                    
            if not success:
                logger.error("All RSS proxies failed for dynamic RSS fetch.")
                
        return posts

    def _parse_rss_json_content(self, items: List[dict], proxy_type: str) -> List[RedditPost]:
        import html.parser
        import re
        
        class HTMLTextExtractor(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.images = []
                self.videos = []
                self.links = []
            def handle_data(self, data):
                self.text.append(data)
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "img":
                    src = attrs_dict.get("src", "")
                    if src:
                        self.images.append(src)
                elif tag == "iframe":
                    src = attrs_dict.get("src", "")
                    if src:
                        self.videos.append(src)
                elif tag == "a":
                    href = attrs_dict.get("href", "")
                    if href:
                        self.links.append(href)
            def get_text(self):
                return "".join(self.text)

        posts = []
        for item in items:
            try:
                # 1. ID
                post_id_val = item.get("guid", "")
                if post_id_val.startswith("t3_"):
                    post_id_val = post_id_val[3:]
                    
                # 2. Title
                title = item.get("title", "")
                
                # 3. Permalink
                permalink = item.get("url", "") if proxy_type == "feed2json" else item.get("link", "")
                
                # 4. Author
                if proxy_type == "feed2json":
                    author_data = item.get("author", {})
                    author = author_data.get("name", "[deleted]") if isinstance(author_data, dict) else str(author_data)
                else:
                    author = item.get("author", "[deleted]")
                if author.startswith("/u/"):
                    author = author[3:]
                    
                # 5. Content HTML
                desc_html = item.get("content_html", "") if proxy_type == "feed2json" else (item.get("description", "") or item.get("content", ""))
                
                # 6. Extract selftext and media URLs
                extractor = HTMLTextExtractor()
                if desc_html:
                    extractor.feed(desc_html)
                selftext = extractor.get_text().strip()
                
                # 7. Media URL extraction
                media_url = ""
                valid_media_extensions = ('.mp4', '.webm', '.gif', '.png', '.jpg', '.jpeg', '.webp')
                
                # Check link hrefs first
                for href in extractor.links:
                    href_lower = href.lower().split("?")[0]
                    if ("i.redd.it" in href_lower or "i.imgur.com" in href_lower or "preview.redd.it" in href_lower):
                        if any(href_lower.endswith(ext) for ext in valid_media_extensions):
                            media_url = href.split("?")[0]
                            break
                            
                # Fallback: regex scan
                if not media_url and desc_html:
                    direct_patterns = re.findall(
                        r'https?://(?:i\.redd\.it|i\.imgur\.com|preview\.redd\.it)/[^\s"<>?]+?(?:\.mp4|\.webm|\.gif|\.png|\.jpg|\.jpeg|\.webp)',
                        desc_html,
                        re.IGNORECASE
                    )
                    if direct_patterns:
                        media_url = direct_patterns[0]
                        
                if not media_url:
                    continue
                    
                # Get subreddit name from permalink if possible
                subreddit = "unknown"
                sub_match = re.search(r'/r/([^/]+)/', permalink)
                if sub_match:
                    subreddit = sub_match.group(1)
                    
                posts.append(
                    RedditPost(
                        id=post_id_val,
                        subreddit=subreddit,
                        title=title,
                        selftext=selftext,
                        score=config.REDDIT_MIN_SCORE + 100,
                        num_comments=config.REDDIT_MIN_COMMENTS + 10,
                        over_18=False,
                        is_self=False,
                        permalink=permalink,
                        author=author,
                        pinned=False,
                        crosspost_parent=None,
                        media_url=media_url
                    )
                )
                posts[-1].is_rss = True
            except Exception as e:
                logger.warning(f"Error parsing RSS JSON item: {e}")
                
        return posts

    def _parse_rss_content(self, xml_content: str, feed_url: str) -> List[RedditPost]:
        import html.parser
        
        class HTMLTextExtractor(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.images = []
                self.videos = []
                self.links = []
            def handle_data(self, data):
                self.text.append(data)
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "img":
                    src = attrs_dict.get("src", "")
                    if src:
                        self.images.append(src)
                elif tag == "video" or tag == "source":
                    src = attrs_dict.get("src", "")
                    if src:
                        self.videos.append(src)
                elif tag == "a":
                    href = attrs_dict.get("href", "")
                    if href:
                        self.links.append(href)
            def get_text(self):
                return "".join(self.text)

        posts = []
        try:
            # Handle unicode parsing/formatting cleanly
            root = ET.fromstring(xml_content.encode('utf-8', errors='replace'))
        except Exception as e:
            logger.error(f"XML parse error for feed {feed_url}: {e}")
            return []
            
        # Parse namespaces if any
        namespaces = {
            'media': 'http://search.yahoo.com/mrss/',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'content': 'http://purl.org/rss/1.0/modules/content/'
        }

        # Check RSS format: usually <channel>/<item>
        items = root.findall(".//item")
        if not items:
            # Try Atom format: <entry>
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            is_atom = True
        else:
            is_atom = False

        for item in items:
            try:
                # 1. Title
                title_elem = item.find("title") if not is_atom else item.find("{http://www.w3.org/2005/Atom}title")
                title = title_elem.text.strip() if title_elem is not None and title_elem.text else "No Title"

                # 2. Link
                link_elem = item.find("link") if not is_atom else item.find("{http://www.w3.org/2005/Atom}link")
                link = ""
                if link_elem is not None:
                    if is_atom:
                        link = link_elem.attrib.get("href", "")
                    else:
                        link = link_elem.text.strip() if link_elem.text else ""

                # 3. Guid/ID
                guid_elem = item.find("guid") if not is_atom else item.find("{http://www.w3.org/2005/Atom}id")
                guid = guid_elem.text.strip() if guid_elem is not None and guid_elem.text else link
                
                # Extract Reddit post ID if possible
                post_id = guid
                if "reddit.com/r/" in link:
                    # e.g., https://www.reddit.com/r/memes/comments/123456/title_here/
                    match = re.search(r'/comments/([a-z0-9]+)', link)
                    if match:
                        post_id = match.group(1)
                else:
                    # fallback: Clean special chars from guid to make a valid local ID
                    post_id = re.sub(r'[^a-zA-Z0-9]', '_', guid)
                    if len(post_id) > 50:
                        import hashlib
                        post_id = hashlib.md5(guid.encode()).hexdigest()

                # 4. Subreddit Detection
                subreddit = "rss_feed"
                # Check link first
                if "reddit.com/r/" in link:
                    sub_match = re.search(r'/r/([^/]+)/', link)
                    if sub_match:
                        subreddit = sub_match.group(1)
                else:
                    # Check categories
                    cat_elems = item.findall("category")
                    if cat_elems:
                        subreddit = cat_elems[0].text.strip()
                    else:
                        # Try to parse from feed URL or channel title
                        feed_title_elem = root.find(".//channel/title")
                        if feed_title_elem is not None and feed_title_elem.text:
                            feed_title = feed_title_elem.text
                            sub_match = re.search(r'r/([^/\s]+)', feed_title)
                            if sub_match:
                                subreddit = sub_match.group(1)

                # 5. Author
                author = "rss_author"
                author_elem = item.find("author") or item.find("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name")
                if author_elem is not None and author_elem.text:
                    author = author_elem.text.strip()
                else:
                    dc_creator = item.find("dc:creator", namespaces)
                    if dc_creator is not None and dc_creator.text:
                        author = dc_creator.text.strip()

                # 6. Description / HTML Content
                desc_elem = item.find("description") or item.find("{http://www.w3.org/2005/Atom}summary") or item.find("{http://www.w3.org/2005/Atom}content")
                desc_html = desc_elem.text if desc_elem is not None and desc_elem.text else ""
                
                extractor = HTMLTextExtractor()
                if desc_html:
                    try:
                        extractor.feed(desc_html)
                    except Exception:
                        pass
                selftext = extractor.get_text().strip()

                # 7. Media URL extraction
                media_url = ""
                
                # Strategy A: Check enclosure
                enc_elem = item.find("enclosure")
                if enc_elem is not None:
                    media_url = enc_elem.attrib.get("url", "")
                    
                # Strategy B: Check media namespace
                if not media_url:
                    m_content = item.find("media:content", namespaces)
                    if m_content is not None:
                        media_url = m_content.attrib.get("url", "")
                    else:
                        m_thumb = item.find("media:thumbnail", namespaces)
                        if m_thumb is not None:
                            media_url = m_thumb.attrib.get("url", "")

                # Strategy C: Check HTML description images/videos
                if not media_url:
                    if extractor.videos:
                        media_url = extractor.videos[0]
                    elif extractor.images:
                        media_url = extractor.images[0]
                    elif extractor.links:
                        # Find direct media links
                        for l in extractor.links:
                            if l.lower().split("?")[0].endswith(('.mp4', '.webm', '.gif', '.png', '.jpg', '.jpeg', '.webp')):
                                media_url = l
                                break

                # Strategy D: Scan raw text/description using regex for direct media links
                if not media_url and desc_html:
                    direct_patterns = re.findall(
                        r'https?://[^\s"<>]+?\.(?:mp4|webm|gif|png|jpg|jpeg|webp)',
                        desc_html,
                        re.IGNORECASE
                    )
                    if direct_patterns:
                        media_url = direct_patterns[0]

                if not media_url:
                    # Skip post if it doesn't contain any media
                    continue

                posts.append(
                    RedditPost(
                        id=post_id,
                        subreddit=subreddit,
                        title=title,
                        selftext=selftext,
                        score=1000,          # Passes minimum score filter
                        num_comments=100,    # Passes comment filter
                        over_18=False,
                        is_self=False,
                        permalink=link,
                        author=author,
                        pinned=False,
                        crosspost_parent=None,
                        media_url=media_url
                    )
                )
                # Set custom flag to identify as RSS feed post
                posts[-1].is_rss = True
            except Exception as item_err:
                logger.warning(f"Error parsing RSS item: {item_err}")
                
        return posts


class RedditAnonymousProvider(BaseProvider):
    """Ingest posts using public/anonymous Reddit scraping fallbacks (Redlib, RSS, rss2json)."""
    def name(self) -> str:
        return "Reddit Anonymous Fallbacks"
        
    def fetch_posts(self, subreddits: List[str]) -> List[RedditPost]:
        from src.reddit.client import (
            _fetch_with_redlib,
            _fetch_anonymous_json,
            _fetch_with_rss
        )
        
        combined_subs = "+".join(subreddits)
        logger.info(f"Fetching posts via Anonymous Fallbacks for r/{combined_subs}")
        
        raw_posts = _fetch_with_redlib(combined_subs)
        
        if not raw_posts:
            raw_posts = _fetch_anonymous_json(combined_subs, config.REDDIT_SORT, config.REDDIT_TIME_FILTER)
            
        if not raw_posts:
            raw_posts = _fetch_with_rss(combined_subs)
            
        posts = []
        for rp in raw_posts:
            post = RedditPost(
                id=rp.get("id", ""),
                subreddit=rp.get("subreddit", combined_subs),
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
            post.is_rss = rp.get("is_rss", False)
            posts.append(post)
        return posts
