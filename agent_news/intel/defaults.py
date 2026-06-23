"""Default sources — a curated AI/tech feed set to bootstrap the radar.

Trimmed from the old project's rss/defaults.py to a reliable core. Users can
add/remove via the sources API; this just seeds a fresh install.

Each helper builds a Source with sensible defaults. Tags drive audience_fit
scoring — keep them aligned with what you'd put on a watchlist.
"""

from __future__ import annotations

from ..models.intel import Source


def _rss(key: str, name: str, url: str, *, tags: list[str], priority: int = 50, weight: float = 1.0) -> Source:
    return Source(
        key=key,
        name=name,
        kind="rss",
        url=url,
        enabled=True,
        priority=priority,
        weight=weight,
        tags=tags,
        schedule="0 */2 * * *",  # every 2h hint (agent may ignore)
        capabilities=["pull", "dedupe", "score"],
    )


def default_sources() -> list[Source]:
    """Seed sources for a fresh install. AI / tech / dev focus."""
    return [
        # ── AI labs & research ─────────────────────────────────────────────
        _rss("openai-blog", "OpenAI Blog", "https://openai.com/news/rss.xml",
             tags=["ai", "openai"], priority=90),
        _rss("anthropic-news", "Anthropic News", "https://www.anthropic.com/news/rss.xml",
             tags=["ai", "anthropic", "claude"], priority=90),
        _rss("google-ai-blog", "Google AI Blog", "https://blog.google/technology/ai/rss/",
             tags=["ai", "google", "gemini"], priority=85),
        _rss("deepmind-blog", "Google DeepMind Blog", "https://deepmind.google/blog/rss.xml",
             tags=["ai", "deepmind", "google"], priority=85),
        _rss("meta-ai-blog", "Meta AI Blog", "https://ai.meta.com/blog/rss/",
             tags=["ai", "meta", "llama"], priority=75),
        _rss("huggingface-blog", "Hugging Face Blog", "https://huggingface.co/blog/feed.xml",
             tags=["ai", "huggingface", "opensource"], priority=80),
        _rss("mistral-news", "Mistral AI News", "https://mistral.ai/news/rss.xml",
             tags=["ai", "mistral"], priority=75),
        # ── Tech news ──────────────────────────────────────────────────────
        _rss("techcrunch-ai", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/",
             tags=["ai", "tech", "news"], priority=70),
        _rss("the-verge", "The Verge", "https://www.theverge.com/rss/index.xml",
             tags=["tech", "news"], priority=65),
        _rss("arstechnica", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index",
             tags=["tech", "news"], priority=65),
        # ── Developer / open source ────────────────────────────────────────
        _rss("github-blog", "GitHub Blog", "https://github.blog/feed/",
             tags=["dev", "github", "opensource"], priority=70),
        _rss("vercel-blog", "Vercel Blog", "https://vercel.com/atom",
             tags=["dev", "vercel", "frontend"], priority=55),
        # ── Chinese tech media ─────────────────────────────────────────────
        _rss("36kr", "36氪", "https://36kr.com/feed",
             tags=["tech", "china", "news"], priority=60),
        _rss("jiqizhixin", "机器之心", "https://www.jiqizhixin.com/rss",
             tags=["ai", "china", "research"], priority=80),
    ]


def seed_default_sources(repo) -> int:
    """Persist default sources if the DB has none. Returns count seeded.

    Idempotent: only seeds when zero sources exist.
    """
    existing = repo.list_sources()
    if existing:
        return 0
    count = 0
    for source in default_sources():
        repo.upsert_source(source)
        count += 1
    return count
