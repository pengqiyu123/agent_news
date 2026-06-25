"""Default information sources bundled with agent-news.

These are not random bootstrap examples. They are the project-maintained source
pool: RSS/WordPress feeds, hotlists, community sources, and YouTube monitors.
Seeding is additive so existing installs can pull in newly-added defaults
without clearing their local source table.
"""

from __future__ import annotations

from typing import Any

from ..models.intel import Source


def _source(
    key: str,
    name: str,
    url: str = "",
    *,
    kind: str = "rss",
    driver: str = "rss_feed",
    platform: str = "",
    tags: list[str] | None = None,
    priority: int = 50,
    weight: float = 1.0,
    schedule: str = "",
    auth: dict[str, Any] | None = None,
) -> Source:
    return Source(
        key=key,
        name=name,
        kind=kind,
        url=url,
        enabled=True,
        priority=priority,
        weight=weight,
        tags=tags or [],
        schedule=schedule,
        capabilities=["pull", "dedupe", "score"],
        config={
            "driver": driver,
            "platform": platform or kind,
            "auth": auth or {},
            "validated_default": True,
        },
    )


def _rss(
    key: str,
    name: str,
    url: str,
    *,
    priority: int,
    tags: list[str] | None = None,
    weight: float = 0.7,
    schedule: str = "*/30 * * * *",
) -> Source:
    return _source(
        key,
        name,
        url,
        kind="rss",
        driver="rss_feed",
        platform="rss",
        priority=priority,
        weight=weight,
        schedule=schedule,
        tags=tags,
    )


def _wp(
    key: str,
    name: str,
    site_url: str,
    *,
    priority: int,
    tags: list[str] | None = None,
    weight: float = 0.75,
    schedule: str = "*/60 * * * *",
) -> Source:
    return _source(
        key,
        name,
        site_url,
        kind="rss",
        driver="wordpress_rest",
        platform="wordpress",
        priority=priority,
        weight=weight,
        schedule=schedule,
        tags=tags,
    )


def _api(
    key: str,
    name: str,
    driver: str,
    *,
    priority: int,
    tags: list[str] | None = None,
    platform: str | None = None,
    weight: float = 0.6,
    schedule: str = "*/20 * * * *",
    auth: dict[str, Any] | None = None,
) -> Source:
    return _source(
        key,
        name,
        "",
        kind="api",
        driver=driver,
        platform=platform or driver,
        priority=priority,
        weight=weight,
        schedule=schedule,
        tags=tags,
        auth=auth,
    )


def _yt(
    key: str,
    name: str,
    handle: str,
    *,
    priority: int,
    tags: list[str] | None = None,
    weight: float = 0.8,
    schedule: str = "*/60 * * * *",
) -> Source:
    return _source(
        key,
        name,
        f"https://www.youtube.com/@{handle}",
        kind="monitor",
        driver="youtube_channel",
        platform="youtube",
        priority=priority,
        weight=weight,
        schedule=schedule,
        tags=tags,
        auth={"handle": handle},
    )


def default_sources() -> list[Source]:
    """Return the bundled production source pool."""
    return [
        _rss("rss-openai", "OpenAI Blog", "https://openai.com/blog/rss.xml", priority=9, tags=["ai", "official"], weight=0.9),
        _rss("openai-blog", "OpenAI News", "https://openai.com/news/rss.xml", priority=90, tags=["ai", "openai", "official"], weight=0.9),
        _rss("rss-anthropic", "Anthropic News", "https://www.anthropic.com/news/rss.xml", priority=9, tags=["ai", "official"], weight=0.9),
        _rss("anthropic-news", "Anthropic News", "https://www.anthropic.com/news/rss.xml", priority=90, tags=["ai", "anthropic", "claude"], weight=0.9),
        _rss("rss-google-ai", "Google AI Blog", "https://blog.google/technology/ai/rss/", priority=9, tags=["ai", "official"], weight=0.9),
        _rss("google-ai-blog", "Google AI Blog", "https://blog.google/technology/ai/rss/", priority=85, tags=["ai", "google", "gemini"], weight=0.9),
        _rss("rss-deepmind", "DeepMind Blog", "https://deepmind.google/blog/rss.xml", priority=9, tags=["ai", "official"], weight=0.9),
        _rss("deepmind-blog", "Google DeepMind Blog", "https://deepmind.google/blog/rss.xml", priority=85, tags=["ai", "deepmind", "google"], weight=0.9),
        _rss("rss-huggingface", "Hugging Face Blog", "https://huggingface.co/blog/feed.xml", priority=8, tags=["ai", "oss"], weight=0.85),
        _rss("huggingface-blog", "Hugging Face Blog", "https://huggingface.co/blog/feed.xml", priority=80, tags=["ai", "huggingface", "opensource"], weight=0.85),
        _rss("rss-openai-cookbook", "OpenAI Cookbook", "https://cookbook.openai.com/rss.xml", priority=8, tags=["ai", "dev"], weight=0.85),
        _rss("rss-meta-ai", "Meta AI Blog", "https://ai.meta.com/blog/rss/", priority=8, tags=["ai", "official"], weight=0.9),
        _rss("meta-ai-blog", "Meta AI Blog", "https://ai.meta.com/blog/rss/", priority=75, tags=["ai", "meta", "llama"], weight=0.9),
        _rss("rss-nvidia-ai", "NVIDIA AI Blog", "https://blogs.nvidia.com/feed/", priority=8, tags=["ai", "chip"], weight=0.85),
        _rss("rss-mistral", "Mistral AI Blog", "https://mistral.ai/news/feed.xml", priority=8, tags=["ai", "official"], weight=0.85),
        _rss("mistral-news", "Mistral AI News", "https://mistral.ai/news/rss.xml", priority=75, tags=["ai", "mistral"], weight=0.85),
        _rss("rss-techcrunch", "TechCrunch", "https://techcrunch.com/feed/", priority=8, schedule="*/20 * * * *", tags=["media", "tech"], weight=0.85),
        _rss("techcrunch-ai", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", priority=70, tags=["ai", "tech", "news"], weight=0.85),
        _rss("rss-theverge", "The Verge", "https://www.theverge.com/rss/index.xml", priority=8, tags=["media", "tech"], weight=0.85),
        _rss("the-verge", "The Verge", "https://www.theverge.com/rss/index.xml", priority=65, tags=["tech", "news"], weight=0.85),
        _rss("rss-arstechnica", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/features", priority=8, tags=["media", "tech"], weight=0.85),
        _rss("arstechnica", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", priority=65, tags=["tech", "news"], weight=0.85),
        _rss("rss-wired", "Wired", "https://www.wired.com/feed/rss", priority=7, tags=["media", "tech"], weight=0.85),
        _rss("rss-mit-tech", "MIT Technology Review", "https://www.technologyreview.com/feed/", priority=8, tags=["media", "research"], weight=0.85),
        _rss("rss-github-blog", "GitHub Blog", "https://github.blog/feed/", priority=7, tags=["github", "oss"], weight=0.8),
        _rss("github-blog", "GitHub Blog", "https://github.blog/feed/", priority=70, tags=["dev", "github", "opensource"], weight=0.8),
        _rss("vercel-blog", "Vercel Blog", "https://vercel.com/atom", priority=55, tags=["dev", "vercel", "frontend"], weight=0.7),
        _rss("rss-hn-front", "Hacker News (RSS)", "https://hnrss.org/frontpage", priority=8, tags=["hn", "community"], weight=0.8),
        _rss("rss-36kr", "36氪", "https://36kr.com/feed", priority=8, tags=["cn", "startup"], weight=0.85),
        _rss("36kr", "36氪", "https://36kr.com/feed", priority=60, tags=["tech", "china", "news"], weight=0.85),
        _rss("rss-sspai", "少数派", "https://sspai.com/feed", priority=7, tags=["cn", "digital"], weight=0.7),
        _rss("rss-jiqizhixin", "机器之心", "https://www.jiqizhixin.com/rss", priority=8, tags=["cn", "ai"], weight=0.85),
        _rss("jiqizhixin", "机器之心", "https://www.jiqizhixin.com/rss", priority=80, tags=["ai", "china", "research"], weight=0.85),
        _rss("rss-ithome", "IT之家", "https://www.ithome.com/rss/", priority=7, tags=["cn", "tech"], weight=0.85),
        _rss("rss-ifanr", "爱范儿", "https://www.ifanr.com/feed", priority=7, tags=["cn", "digital"], weight=0.7),
        _rss("rss-zhidx", "智东西", "https://zhidx.com/rss", priority=8, tags=["cn", "ai", "media"], weight=0.85),
        _rss("rss-tmtpost", "钛媒体", "https://www.tmtpost.com/rss.xml", priority=7, tags=["cn", "tech", "media"], weight=0.8),
        _rss("rss-qbitai", "量子位", "https://www.qbitai.com/feed", priority=8, tags=["cn", "ai", "media"], weight=0.85),
        _rss("rss-ruanyifeng", "阮一峰的网络日志", "https://www.ruanyifeng.com/blog/atom.xml", priority=6, tags=["cn", "dev"], weight=0.7),
        _rss("rss-arxiv-cs-ai", "arXiv CS.AI", "http://export.arxiv.org/rss/cs.AI", priority=7, tags=["research", "arxiv"], weight=0.7),
        _rss("rss-arxiv-cs-cl", "arXiv CS.CL (NLP)", "http://export.arxiv.org/rss/cs.CL", priority=7, tags=["research", "nlp"], weight=0.7),
        _rss("rss-arxiv-cs-cv", "arXiv CS.CV", "http://export.arxiv.org/rss/cs.CV", priority=7, tags=["research", "vision"], weight=0.7),
        _rss("rss-distill", "Distill.pub", "https://distill.pub/feed.xml", priority=7, tags=["research", "viz"], weight=0.7),
        _rss("rsshub-weibo-hot", "微博热搜 (RSSHub)", "https://rsshub.app/weibo/hot", priority=7, schedule="*/15 * * * *", tags=["cn", "weibo"], weight=0.6),
        _rss("rsshub-zhihu-hot", "知乎热榜 (RSSHub)", "https://rsshub.app/zhihu/hotlist", priority=7, schedule="*/15 * * * *", tags=["cn", "zhihu"], weight=0.6),
        _rss("rsshub-juejin-trend", "掘金前端趋势 (RSSHub)", "https://rsshub.app/juejin/trending/frontend/monthly", priority=6, schedule="0 */4 * * *", tags=["cn", "dev"], weight=0.7),
        _rss("rsshub-github-trending", "GitHub Trending (RSSHub)", "https://rsshub.app/github/trending/daily", priority=7, tags=["github", "oss"], weight=0.8),
        _rss("rsshub-producthunt", "Product Hunt (RSSHub)", "https://rsshub.app/producthunt/daily", priority=6, tags=["startup", "product"], weight=0.7),
        _wp("wp-techcrunch", "TechCrunch (WP)", "https://techcrunch.com", priority=7, tags=["media", "tech"], weight=0.8),
        _wp("wp-verge", "The Verge (WP)", "https://www.theverge.com", priority=7, tags=["media", "tech"], weight=0.8),
        _wp("wp-wired", "Wired (WP)", "https://www.wired.com", priority=6, tags=["media", "tech"], weight=0.75),
        _wp("wp-arstechnica", "Ars Technica (WP)", "https://arstechnica.com", priority=7, tags=["media", "tech"], weight=0.8),
        _api("reddit-chatgpt", "Reddit r/ChatGPT", "reddit_hot", priority=7, tags=["community", "ai"], auth={"subreddit": "ChatGPT"}, platform="reddit", weight=0.8),
        _api("reddit-claudeai", "Reddit r/ClaudeAI", "reddit_hot", priority=7, tags=["community", "ai"], auth={"subreddit": "ClaudeAI"}, platform="reddit", weight=0.8),
        _api("reddit-local-llama", "Reddit r/LocalLLaMA", "reddit_hot", priority=6, tags=["community", "oss"], auth={"subreddit": "LocalLLaMA"}, platform="reddit", weight=0.8),
        _api("reddit-machinelearning", "Reddit r/MachineLearning", "reddit_hot", priority=7, tags=["community", "research"], auth={"subreddit": "MachineLearning"}, platform="reddit", weight=0.8),
        _api("reddit-singularity", "Reddit r/singularity", "reddit_hot", priority=6, tags=["community", "future"], auth={"subreddit": "singularity"}, platform="reddit", weight=0.8),
        _api("hn-frontpage", "Hacker News Front Page", "hackernews_frontpage", priority=8, tags=["community", "hn"], platform="hackernews", weight=0.8),
        _api("github-trending", "GitHub Trending", "github_trending", priority=7, tags=["oss", "github"], platform="github", weight=0.8),
        _api("vvhan-hotlist", "VVhan 热榜聚合", "vvhan_hotlist", priority=7, schedule="*/15 * * * *", tags=["cn", "hot"], platform="vvhan", weight=0.6),
        _yt("yt-openai", "OpenAI", "OpenAI", priority=9, tags=["ai", "official"], weight=0.9),
        _yt("yt-anthropic", "Anthropic (Claude)", "AnthropicAI", priority=9, tags=["ai", "official"], weight=0.9),
        _yt("yt-google", "Google", "google", priority=9, tags=["tech", "official"], weight=0.9),
        _yt("yt-deepmind", "Google DeepMind", "GoogleDeepMind", priority=9, tags=["ai", "research"], weight=0.9),
        _yt("yt-deepseek", "DeepSeek", "deepseek_ai", priority=8, tags=["ai", "cn"], weight=0.85),
        _yt("yt-meta", "Meta AI", "meta", priority=8, tags=["ai", "official"], weight=0.85),
        _yt("yt-mistral", "Mistral AI", "MistralAI", priority=8, tags=["ai", "official"], weight=0.85),
        _yt("yt-huggingface", "Hugging Face", "huggingface", priority=8, tags=["ai", "oss"], weight=0.85),
        _yt("yt-xai", "xAI (Grok)", "xaboratory", priority=8, tags=["ai", "official"], weight=0.85),
        _yt("yt-nvidia", "NVIDIA", "NVIDIA", priority=8, tags=["ai", "chip"], weight=0.85),
        _yt("yt-perplexity", "Perplexity", "perplexityai", priority=7, tags=["ai", "search"], weight=0.8),
        _yt("yt-cohere", "Cohere", "cohereai", priority=7, tags=["ai", "nlp"], weight=0.75),
        _yt("yt-stability", "Stability AI", "StabilityAI", priority=7, tags=["ai", "gen"], weight=0.75),
        _yt("yt-ibm", "IBM (watsonx)", "IBMTechnology", priority=7, tags=["ai", "enterprise"], weight=0.75),
        _yt("yt-apple", "Apple", "Apple", priority=9, tags=["phone", "official"], weight=0.9),
        _yt("yt-samsung", "Samsung", "Samsung", priority=8, tags=["phone", "official"], weight=0.85),
        _yt("yt-xiaomi", "Xiaomi", "Xiaomi", priority=8, tags=["phone", "cn"], weight=0.85),
        _yt("yt-oppo", "OPPO", "OPPO", priority=7, tags=["phone", "cn"], weight=0.8),
        _yt("yt-oneplus", "OnePlus", "oneplus", priority=7, tags=["phone", "cn"], weight=0.8),
        _yt("yt-vivo", "vivo", "vivo", priority=7, tags=["phone", "cn"], weight=0.8),
        _yt("yt-huawei", "Huawei", "Huawei", priority=8, tags=["phone", "cn", "chip"], weight=0.85),
        _yt("yt-honor", "Honor", "HonorOfficial", priority=7, tags=["phone", "cn"], weight=0.8),
        _yt("yt-nothing", "Nothing", "Nothing", priority=7, tags=["phone", "design"], weight=0.75),
        _yt("yt-googlepixel", "Google Pixel", "GooglePixel", priority=7, tags=["phone", "official"], weight=0.8),
        _yt("yt-motorola", "Motorola", "motorola", priority=6, tags=["phone"], weight=0.75),
        _yt("yt-realme", "Realme", "realmemobile", priority=6, tags=["phone", "cn"], weight=0.75),
        _yt("yt-qualcomm", "Qualcomm", "Qualcomm", priority=8, tags=["chip", "mobile"], weight=0.85),
        _yt("yt-mediatek", "MediaTek", "MediaTekInc", priority=8, tags=["chip", "mobile"], weight=0.85),
        _yt("yt-intel", "Intel", "Intel", priority=8, tags=["chip", "official"], weight=0.85),
        _yt("yt-amd", "AMD", "AMD", priority=8, tags=["chip", "official"], weight=0.85),
        _yt("yt-arm", "ARM", "ARM", priority=7, tags=["chip", "ip"], weight=0.8),
        _yt("yt-applesilicon", "Apple Silicon", "apple", priority=7, tags=["chip", "official"], weight=0.8),
        _yt("yt-mkbhd", "MKBHD", "mkbhd", priority=8, tags=["tech", "review"], weight=0.85),
        _yt("yt-googletech", "Google Tech", "GoogleTechDevelopers", priority=7, tags=["tech", "dev"], weight=0.8),
        _yt("yt-linus", "Linus Tech Tips", "LinusTechTips", priority=7, tags=["tech", "review"], weight=0.8),
    ]


def _default_source_needs_refresh(existing: Source, wanted: Source) -> bool:
    if not existing.config.get("driver"):
        return True
    if existing.config.get("driver") != wanted.config.get("driver"):
        return True
    if existing.kind != wanted.kind:
        return True
    if not existing.url and wanted.url:
        return True
    return False


def seed_default_sources(repo) -> int:
    """Persist missing defaults and refresh old default rows missing driver config."""
    existing_by_key = {source.key: source for source in repo.list_sources()}
    count = 0
    refreshed = 0
    for source in default_sources():
        existing = existing_by_key.get(source.key)
        if existing is None:
            repo.upsert_source(source)
            existing_by_key[source.key] = source
            count += 1
            continue
        if _default_source_needs_refresh(existing, source):
            repo.upsert_source(source)
            existing_by_key[source.key] = source
            refreshed += 1
    return count + refreshed
