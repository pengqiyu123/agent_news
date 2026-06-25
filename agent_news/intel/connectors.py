"""Connectors for the information radar.

The source pool is maintained in this project. Connectors route by
``source.config["driver"]`` where needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from html import unescape
from http.client import IncompleteRead
from typing import Callable, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..models.intel import RawItem, Source

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AgentNews/0.1"
SOURCE_TIMEOUT_SECONDS = 12


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_raw_id() -> str:
    import uuid

    return f"raw-{uuid.uuid4().hex[:12]}"


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _is_http_url(value: str | None) -> bool:
    compact = str(value or "").strip().lower()
    return compact.startswith("https://") or compact.startswith("http://")


def _fetch_text(url: str, timeout: int = SOURCE_TIMEOUT_SECONDS) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - source URLs are configured by the local app.
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            return response.read().decode(charset, errors="ignore")
        except IncompleteRead as exc:
            return exc.partial.decode(charset, errors="ignore")


def _fetch_json(url: str, timeout: int = SOURCE_TIMEOUT_SECONDS) -> dict[str, Any]:
    return json.loads(_fetch_text(url, timeout=timeout))


def _source_driver(source: Source) -> str:
    return str(source.config.get("driver") or source.kind or "").strip()


def _source_auth(source: Source) -> dict[str, Any]:
    auth = source.config.get("auth")
    return auth if isinstance(auth, dict) else {}


def _raw(
    source: Source,
    *,
    title: str,
    link: str,
    summary: str = "",
    published_at: str | None = None,
    author: str = "",
    engagement_score: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> RawItem | None:
    title = _clean_html(title)
    link = str(link or "").strip()
    if not title or not _is_http_url(link):
        return None
    return RawItem(
        id=_new_raw_id(),
        source_key=source.key,
        source_name=source.name or source.key,
        title=title,
        link=link,
        summary=_clean_html(summary)[:1200] or title,
        published_at=published_at,
        collected_at=_utcnow(),
        tags=list(source.tags),
        engagement_score=float(engagement_score or 0),
        metadata={
            "platform": source.config.get("platform") or source.kind,
            "driver": _source_driver(source),
            "author": author,
            **(metadata or {}),
        },
    )


def _parse_compact_number(value: str | None) -> int:
    compact = str(value or "").strip().lower().replace(",", "")
    if not compact:
        return 0
    multiplier = 1
    if compact.endswith("k"):
        multiplier = 1000
        compact = compact[:-1]
    elif compact.endswith("m"):
        multiplier = 1_000_000
        compact = compact[:-1]
    try:
        return int(float(compact) * multiplier)
    except ValueError:
        return 0


def fetch_rss(source: Source, *, max_items: int = 50) -> list[RawItem]:
    import feedparser

    if not source.url:
        return []
    parsed = feedparser.parse(source.url)
    items: list[RawItem] = []
    for entry in parsed.entries[:max_items]:
        item = _raw(
            source,
            title=entry.get("title") or "",
            link=entry.get("link") or "",
            summary=entry.get("summary") or entry.get("description") or "",
            published_at=entry.get("published") or entry.get("updated"),
            author=entry.get("author") or source.name,
            metadata={"native_id": entry.get("id") or entry.get("guid") or entry.get("link")},
        )
        if item:
            items.append(item)
    return items


def fetch_reddit_hot(source: Source) -> list[RawItem]:
    subreddit = str(_source_auth(source).get("subreddit") or source.config.get("subreddit") or "technology")
    payload = _fetch_json(f"https://www.reddit.com/r/{subreddit}/hot.json?limit=8")
    children = payload.get("data", {}).get("children", [])
    items: list[RawItem] = []
    for child in children[:8]:
        data = child.get("data", {}) if isinstance(child, dict) else {}
        permalink = str(data.get("permalink") or "").strip()
        created = data.get("created_utc")
        published = None
        if created:
            try:
                published = datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat()
            except Exception:
                published = None
        item = _raw(
            source,
            title=str(data.get("title") or ""),
            link=f"https://www.reddit.com{permalink}" if permalink else "",
            summary=str(data.get("selftext") or "")[:1200] or "Reddit community hot post",
            published_at=published,
            author=str(data.get("author") or subreddit),
            engagement_score=float(data.get("score") or 0),
            metadata={
                "native_id": str(data.get("id") or permalink),
                "comments": int(data.get("num_comments") or 0),
                "upvote_ratio": float(data.get("upvote_ratio") or 0),
                "subreddit": subreddit,
            },
        )
        if item:
            items.append(item)
    return items


def fetch_hackernews_frontpage(source: Source) -> list[RawItem]:
    payload = _fetch_json("https://hn.algolia.com/api/v1/search?tags=front_page")
    hits = payload.get("hits", [])
    items: list[RawItem] = []
    for hit in hits[:8]:
        title = str(hit.get("title") or hit.get("story_title") or "").strip()
        link = str(hit.get("url") or "").strip()
        if not link and hit.get("objectID"):
            link = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        item = _raw(
            source,
            title=title,
            link=link,
            summary=str(hit.get("story_text") or hit.get("comment_text") or "Hacker News front-page item"),
            published_at=hit.get("created_at"),
            author=str(hit.get("author") or "hn"),
            engagement_score=float(hit.get("points") or 0),
            metadata={
                "native_id": str(hit.get("objectID") or link),
                "comments": int(hit.get("num_comments") or 0),
            },
        )
        if item:
            items.append(item)
    return items


def fetch_github_trending(source: Source) -> list[RawItem]:
    html = _fetch_text("https://github.com/trending?spoken_language_code=")
    articles = re.findall(r"<article[\s\S]*?</article>", html)
    items: list[RawItem] = []
    for article in articles[:8]:
        repo_match = re.search(r'href="(/[^"]+/[^"]+)"', article)
        if not repo_match:
            continue
        repo_full_name = repo_match.group(1).strip("/")
        desc_match = re.search(r"<p[^>]*>([\s\S]*?)</p>", article)
        stars_match = re.search(r'href="[^"]+/stargazers"[^>]*>([\s\S]*?)</a>', article)
        forks_match = re.search(r'href="[^"]+/forks"[^>]*>([\s\S]*?)</a>', article)
        article_text = _clean_html(article)
        stars_today_match = re.search(r"([\d.,]+[kKmM]?)\s+stars?\s+today", article_text, re.IGNORECASE)
        stars_total = _parse_compact_number(_clean_html(stars_match.group(1)) if stars_match else "")
        forks_total = _parse_compact_number(_clean_html(forks_match.group(1)) if forks_match else "")
        stars_today = _parse_compact_number(stars_today_match.group(1) if stars_today_match else "")
        heat = stars_today * 25 + min(stars_total, 50_000) * 0.04 + min(forks_total, 10_000) * 0.3
        item = _raw(
            source,
            title=re.sub(r"\s+", "", repo_full_name),
            link=f"https://github.com/{repo_full_name}",
            summary=_clean_html(desc_match.group(1) if desc_match else "GitHub Trending project"),
            published_at=_utcnow(),
            author="github",
            engagement_score=heat,
            metadata={
                "native_id": repo_full_name,
                "github_repo": repo_full_name,
                "github_stars_total": stars_total,
                "github_forks_total": forks_total,
                "github_stars_today": stars_today,
                "published_at_inferred": True,
            },
        )
        if item:
            items.append(item)
    return items


def fetch_vvhan_hotlist(source: Source) -> list[RawItem]:
    payload = _fetch_json("https://api.vvhan.com/api/hotlist/all", timeout=15)
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    items: list[RawItem] = []
    for category, entries in data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries[:5]:
            url = str(entry.get("url") or "").strip()
            item = _raw(
                source,
                title=str(entry.get("title") or ""),
                link=url,
                summary=str(entry.get("desc") or "")[:280],
                published_at=_utcnow(),
                author="vvhan",
                engagement_score=float(entry.get("hot") or 0),
                metadata={
                    "native_id": str(entry.get("id") or f"{category}:{url}"),
                    "category": category,
                    "published_at_inferred": True,
                },
            )
            if item:
                item.source_name = f"{source.name or source.key}·{category}"
                item.tags = [*item.tags, str(category)]
                items.append(item)
    items.sort(key=lambda item: item.engagement_score, reverse=True)
    return items[:16]


def fetch_youtube_channel(source: Source) -> list[RawItem]:
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return []
    auth = _source_auth(source)
    channel_id = str(auth.get("channel_id") or source.config.get("channel_id") or "").strip()
    handle = str(auth.get("handle") or source.config.get("handle") or "").strip()
    if not channel_id and not handle:
        return []
    search_param = f"forHandle={handle}" if handle else f"channelId={channel_id}"
    url = (
        "https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&{search_param}&maxResults=5&order=date&type=video&key={api_key}"
    )
    payload = _fetch_json(url, timeout=15)
    items: list[RawItem] = []
    for entry in payload.get("items", [])[:5]:
        snippet = entry.get("snippet", {}) if isinstance(entry, dict) else {}
        video_id = (entry.get("id", {}) or {}).get("videoId", "")
        title = str(snippet.get("title") or "").strip()
        if not title or not video_id:
            continue
        thumbnails = snippet.get("thumbnails", {}) or {}
        item = _raw(
            source,
            title=title,
            link=f"https://www.youtube.com/watch?v={video_id}",
            summary=str(snippet.get("description") or "")[:1200] or title,
            published_at=snippet.get("publishedAt"),
            author=str(snippet.get("channelTitle") or source.name),
            metadata={
                "native_id": video_id,
                "youtube_video_id": video_id,
                "channel_id": channel_id,
                "thumbnail_url": str((thumbnails.get("high") or {}).get("url") or ""),
            },
        )
        if item:
            items.append(item)
    return items


def fetch_wordpress_rest(source: Source) -> list[RawItem]:
    site_url = str(source.url or "").strip().rstrip("/")
    if not _is_http_url(site_url):
        return []
    payload = _fetch_json(f"{site_url}/wp-json/wp/v2/posts?per_page=8&orderby=date", timeout=12)
    if not isinstance(payload, list):
        return []
    items: list[RawItem] = []
    for post in payload[:8]:
        if not isinstance(post, dict):
            continue
        title = _clean_html(str((post.get("title") or {}).get("rendered") or ""))
        link = str(post.get("link") or "").strip()
        content_raw = str((post.get("content") or {}).get("rendered") or "")
        excerpt_raw = str((post.get("excerpt") or {}).get("rendered") or "")
        item = _raw(
            source,
            title=title,
            link=link,
            summary=_clean_html(excerpt_raw)[:320] or title,
            published_at=post.get("date"),
            author=str(post.get("author") or ""),
            metadata={
                "native_id": str(post.get("id") or ""),
                "site_url": site_url,
                "content_excerpt": _clean_html(content_raw)[:1800],
            },
        )
        if item:
            items.append(item)
    return items


DRIVER_FETCHERS: dict[str, Callable[[Source], list[RawItem]]] = {
    "rss_feed": fetch_rss,
    "reddit_hot": fetch_reddit_hot,
    "hackernews_frontpage": fetch_hackernews_frontpage,
    "github_trending": fetch_github_trending,
    "vvhan_hotlist": fetch_vvhan_hotlist,
    "youtube_channel": fetch_youtube_channel,
    "wordpress_rest": fetch_wordpress_rest,
}


def fetch_by_driver(source: Source) -> list[RawItem]:
    driver = _source_driver(source)
    fetcher = DRIVER_FETCHERS.get(driver)
    if fetcher is None:
        logger.warning("No driver fetcher registered for '%s' (%s)", driver, source.key)
        return []
    return fetcher(source)


FETCHERS: dict[str, Callable[[Source], list[RawItem]]] = {
    "rss": fetch_by_driver,
    "rsshub": fetch_by_driver,
    "api": fetch_by_driver,
    "hotlist": fetch_by_driver,
    "monitor": fetch_by_driver,
    "html": fetch_by_driver,
}


def fetch_source(source: Source) -> list[RawItem]:
    fetcher = FETCHERS.get(source.kind)
    if fetcher is None:
        logger.warning("No fetcher registered for source kind '%s' (%s)", source.kind, source.key)
        return []
    try:
        return fetcher(source)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Fetcher for %s (%s) failed: %s", source.key, source.kind, exc)
        return []
    except Exception as exc:  # noqa: BLE001 - isolate per-source failures.
        logger.warning("Fetcher for %s (%s) failed: %s", source.key, source.kind, exc)
        return []


def collect_sources(sources: list[Source], *, max_workers: int = 4) -> list[RawItem]:
    enabled = [s for s in sources if s.enabled]
    if not enabled:
        return []

    all_items: list[RawItem] = []
    if max_workers <= 1:
        for source in enabled:
            all_items.extend(fetch_source(source))
        return all_items

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_source, source): source for source in enabled}
        for future in as_completed(futures):
            try:
                all_items.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Source %s raised: %s", futures[future].key, exc)
    return all_items
