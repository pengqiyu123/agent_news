from __future__ import annotations

from collections import Counter


def test_default_sources_include_legacy_production_pool():
    from agent_news.intel.defaults import default_sources

    sources = default_sources()
    keys = {source.key for source in sources}
    drivers = Counter(source.config.get("driver") for source in sources)
    kinds = Counter(source.kind for source in sources)

    assert len(sources) >= 90
    assert len(keys) == len(sources)
    assert kinds["rss"] >= 50
    assert kinds["api"] >= 8
    assert kinds["monitor"] >= 35
    assert drivers["rss_feed"] >= 48
    assert drivers["wordpress_rest"] == 4
    assert drivers["reddit_hot"] == 5
    assert drivers["hackernews_frontpage"] == 1
    assert drivers["github_trending"] == 1
    assert drivers["vvhan_hotlist"] == 1
    assert drivers["youtube_channel"] >= 35

    for required in {
        "rss-openai",
        "rss-qbitai",
        "reddit-chatgpt",
        "hn-frontpage",
        "github-trending",
        "vvhan-hotlist",
        "yt-apple",
        "yt-qualcomm",
        "wp-techcrunch",
    }:
        assert required in keys


def test_seed_defaults_adds_missing_defaults_to_existing_repo():
    from agent_news.intel.defaults import default_sources, seed_default_sources
    from agent_news.models.intel import Source

    class MemoryRepo:
        def __init__(self):
            self.sources = {"custom-only": Source(key="custom-only", name="Custom")}

        def list_sources(self):
            return list(self.sources.values())

        def upsert_source(self, source):
            self.sources[source.key] = source
            return source

    repo = MemoryRepo()
    added = seed_default_sources(repo)

    assert added == len(default_sources())
    assert "custom-only" in repo.sources
    assert "rss-openai" in repo.sources
    assert seed_default_sources(repo) == 0


def test_seed_defaults_refreshes_existing_default_source_config():
    from agent_news.intel.defaults import seed_default_sources
    from agent_news.models.intel import Source

    class MemoryRepo:
        def __init__(self):
            self.sources = {"openai-blog": Source(key="openai-blog", name="OpenAI Blog", url="https://openai.com/blog")}

        def list_sources(self):
            return list(self.sources.values())

        def upsert_source(self, source):
            self.sources[source.key] = source
            return source

    repo = MemoryRepo()
    changed = seed_default_sources(repo)

    assert changed >= 1
    assert repo.sources["openai-blog"].config["driver"] == "rss_feed"
    assert repo.sources["openai-blog"].config["validated_default"] is True


def test_driver_fetcher_registry_covers_legacy_drivers():
    from agent_news.intel.connectors import DRIVER_FETCHERS, FETCHERS

    for driver in {
        "rss_feed",
        "wordpress_rest",
        "reddit_hot",
        "hackernews_frontpage",
        "github_trending",
        "vvhan_hotlist",
        "youtube_channel",
    }:
        assert driver in DRIVER_FETCHERS

    for kind in {"rss", "api", "monitor"}:
        assert kind in FETCHERS


def test_probe_allows_driver_sources_without_url(monkeypatch):
    from agent_news.intel import source_probe
    from agent_news.models.intel import RawItem, Source

    def fake_fetcher(source):
        return [
            RawItem(
                id="raw-driver",
                source_key=source.key,
                source_name=source.name,
                title="Driver source item",
                link="https://example.com/item",
            )
        ]

    monkeypatch.setitem(source_probe.FETCHERS, "api", fake_fetcher)
    result = source_probe.probe_source(
        Source(
            key="reddit-test",
            name="Reddit Test",
            kind="api",
            url="",
            config={"driver": "reddit_hot", "auth": {"subreddit": "ChatGPT"}},
        )
    )

    assert result.status == "ok"
    assert result.item_count == 1
