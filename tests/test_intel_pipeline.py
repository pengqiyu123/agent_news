"""Tests for the intel radar pure-function pipeline.

No DB, no network — just verify normalize → cluster → score logic is sound
before wrapping it in atomic operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_news.intel import (
    build_events_from_clusters,
    cluster_discovery_items,
    event_id_for_cluster,
    normalize_raw_items,
    score_event,
    tokenize,
)
from agent_news.intel.score import (
    audience_fit_score,
    classify_alert_state,
    composite,
    coverage_score,
    freshness_score,
    materialize_alerts,
    velocity_score,
)
from agent_news.models.intel import DiscoveryItem, IntelEvent, RawItem, Source


def test_tokenize_mixed_cjk_latin():
    tokens = tokenize("OpenAI 发布 GPT-5 模型")
    # latin words kept, cjk bigrams extracted
    assert "openai" in tokens
    assert "gpt-5" in tokens
    assert any("发布" == t or "模型" in t for t in tokens)


def test_tokenize_strips_stopwords():
    tokens = tokenize("the new AI model from OpenAI")
    assert "the" not in tokens
    assert "ai" in tokens
    assert "openai" in tokens


def test_normalize_adds_tokens_and_dedupe_key():
    raw = RawItem(
        id="r1",
        source_key="openai",
        title="OpenAI launches GPT-5",
        link="https://openai.com/blog/gpt-5?utm_source=twitter",
        summary="New model announced",
    )
    items = normalize_raw_items([raw], {"openai": Source(key="openai", tags=["ai", "openai"])})
    assert len(items) == 1
    di = items[0]
    assert di.title_tokens  # non-empty
    assert di.dedupe_key.startswith("url:")
    # tracking params stripped from canonical link
    assert "utm_source" not in di.canonical_link
    assert "openai.com" in di.canonical_link
    # source tags merged in
    assert "ai" in di.tags and "openai" in di.tags


def test_normalize_drops_empty_titles():
    raw = RawItem(id="r1", source_key="s", title="   ", link="x")
    assert normalize_raw_items([raw]) == []


def test_cluster_merges_same_story_different_sources():
    now = datetime.now(timezone.utc).isoformat()
    items = normalize_raw_items([
        RawItem(id="a", source_key="src1", title="OpenAI releases GPT-5 model", link="https://a.com/1", published_at=now),
        RawItem(id="b", source_key="src2", title="OpenAI launches new GPT-5", link="https://b.com/2", published_at=now),
        RawItem(id="c", source_key="src3", title="Completely unrelated weather news", link="https://c.com/3", published_at=now),
    ])
    clusters = cluster_discovery_items(items)
    assert len(clusters) == 2  # GPT-5 pair merged, weather separate
    gpt5_cluster = max(clusters, key=len)
    assert len(gpt5_cluster) == 2


def test_cluster_merges_same_canonical_url():
    now = datetime.now(timezone.utc).isoformat()
    items = normalize_raw_items([
        RawItem(id="a", source_key="s1", title="Title one", link="https://x.com/article", published_at=now),
        RawItem(id="b", source_key="s2", title="Totally different wording", link="https://x.com/article", published_at=now),
    ])
    clusters = cluster_discovery_items(items)
    assert len(clusters) == 1  # same canonical URL → merged regardless of title


def test_cluster_respects_time_window():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=48)).isoformat()
    items = normalize_raw_items([
        RawItem(id="a", source_key="s1", title="OpenAI GPT-5 release", link="https://a.com/1", published_at=now.isoformat()),
        RawItem(id="b", source_key="s2", title="OpenAI GPT-5 release", link="https://b.com/2", published_at=old),
    ])
    clusters = cluster_discovery_items(items)
    # 48h apart → not clustered even with identical tokens
    assert len(clusters) == 2


def test_event_id_deterministic():
    now = datetime.now(timezone.utc).isoformat()
    items = normalize_raw_items([
        RawItem(id="a", source_key="s1", title="OpenAI GPT-5", link="https://a.com/1", published_at=now),
    ])
    eid1 = event_id_for_cluster(items)
    eid2 = event_id_for_cluster(items)
    assert eid1 == eid2
    assert eid1.startswith("evt-")


def test_score_event_hot_state():
    now = datetime.now(timezone.utc)
    items = normalize_raw_items([
        RawItem(id=f"r{i}", source_key=f"s{i}", title=f"OpenAI GPT-5 released model {i}",
                link=f"https://{i}.com/x", published_at=now.isoformat())
        for i in range(5)
    ])
    clusters = cluster_discovery_items(items)
    gpt5 = max(clusters, key=len)
    eid = event_id_for_cluster(gpt5)
    evt = score_event(gpt5, eid, watchlist=["openai", "gpt"])
    # 5 fresh sources → high coverage + freshness + audience fit
    assert evt.coverage_score >= 60
    assert evt.freshness_score >= 90
    assert evt.audience_fit_score >= 75
    assert evt.composite_score >= 60
    assert evt.member_count == len(gpt5)
    assert evt.alert_state in ("hot", "rising", "new")


def test_velocity_score_with_growth():
    score, details = velocity_score(member_count=5, previous_member_count=2,
                                    platform_count=3, previous_platform_count=1)
    assert score > 0
    assert details["member_delta"] == 3.0
    assert details["platform_delta"] == 2.0


def test_velocity_score_no_growth():
    score, _ = velocity_score(member_count=3, previous_member_count=3,
                              platform_count=2, previous_platform_count=2)
    assert score == 0.0


def test_freshness_decays():
    now = datetime.now(timezone.utc)
    fresh = freshness_score(now.isoformat(), now)
    stale = freshness_score((now - timedelta(hours=80)).isoformat(), now)
    assert fresh == 100.0
    assert stale == 0.0


def test_audience_fit_with_watchlist():
    score = audience_fit_score(tags=["ai"], anchor_tokens=["GPT", "OpenAI"],
                               entity_names=[], watchlist=["openai"])
    assert score >= 75  # hit
    score_miss = audience_fit_score(tags=["weather"], anchor_tokens=["rain"],
                                    entity_names=[], watchlist=["openai"])
    assert score_miss < 50


def test_classify_alert_states():
    assert classify_alert_state(80, 50, 3)[0] == "hot"
    assert classify_alert_state(40, 35, 2)[0] == "rising"
    assert classify_alert_state(10, 5, 0)[0] == "cold"
    cooling = classify_alert_state(30, 5, 0, previous_state="hot")
    assert cooling[0] == "cooling"


def test_build_events_carries_previous_state():
    now = datetime.now(timezone.utc)
    items = normalize_raw_items([
        RawItem(id="a", source_key="s1", title="OpenAI GPT-5 launch", link="https://a.com/1", published_at=now.isoformat()),
    ])
    clusters = cluster_discovery_items(items)
    eid = event_id_for_cluster(clusters[0])
    previous = IntelEvent(id=eid, member_count=1, platform_count=1, alert_state="rising")
    events = build_events_from_clusters(clusters, previous_events={eid: previous})
    evt = events[0]
    # velocity delta should reflect growth if we now have more members
    assert evt.change_state in ("new_event", "growing_event", "stable_event")


def test_materialize_alerts_filters():
    hot = IntelEvent(id="e1", composite_score=85, alert_state="hot", title="big")
    cold = IntelEvent(id="e2", composite_score=15, alert_state="cold", title="small")
    rising = IntelEvent(id="e3", composite_score=45, alert_state="rising", title="rising")
    alerts = materialize_alerts([hot, cold, rising], threshold=50)
    alert_ids = {a.event_id for a in alerts}
    assert "e1" in alert_ids  # above threshold
    assert "e3" in alert_ids  # rising state included even below threshold
    assert "e2" not in alert_ids  # cold + below threshold filtered


def test_composite_weighting():
    val = composite(vel=100, cov=100, fre=100, aud=100)
    assert val == 100.0
    val = composite(vel=0, cov=0, fre=0, aud=0)
    assert val == 0.0
    # audience has highest weight, so it dominates partial scores
    high_aud = composite(vel=0, cov=0, fre=0, aud=100)
    high_vel = composite(vel=100, cov=0, fre=0, aud=0)
    assert high_aud > high_vel
