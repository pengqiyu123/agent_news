"""End-to-end radar operations test.

Verifies the full agent-controllable chain works through the operation registry
+ HTTP, WITHOUT touching the network:
  seed_defaults → (inject fake raw items) → build_events → deep_dive

Network (RSS fetch + deep-dive URL fetch) is stubbed so the test is hermetic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app

    return TestClient(app)


# ── Registry discovery ──────────────────────────────────────────────────────
def test_radar_operations_registered(client):
    """All radar operations must appear in the registry listing."""
    resp = client.get("/api/operations")
    assert resp.status_code == 200
    names = {op["name"] for op in resp.json()["items"]}
    expected = {
        "radar.seed_defaults",
        "radar.sync_sources",
        "radar.sync_one_source",
        "radar.build_events",
        "radar.deep_dive_event",
        "radar.add_source",
        "radar.remove_source",
    }
    missing = expected - names
    assert not missing, f"missing operations: {missing}"


# ── Seed + add/remove source ────────────────────────────────────────────────
def test_seed_defaults_idempotent(client):
    # first call: either seeds (success) or skips if a prior test already seeded.
    # Both are valid idempotent outcomes in a shared-DB test session.
    resp = client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})
    assert resp.status_code == 200
    first = resp.json()["item"]
    assert first["ok"]
    assert first["status"] in ("ok", "skipped")

    # second call: must skip (sources now exist) — true idempotency.
    resp = client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_add_and_remove_source(client):
    resp = client.post("/api/operations/radar.add_source/execute", json={
        "params": {"key": "test-feed", "source_name": "Test", "url": "https://example.com/rss",
                   "tags": "ai,test", "priority": 40}
    })
    assert resp.status_code == 200
    assert resp.json()["item"]["ok"]

    # verify it appears in listing
    resp = client.get("/api/intel/sources")
    keys = {s["key"] for s in resp.json()["items"]}
    assert "test-feed" in keys

    # remove it
    resp = client.post("/api/operations/radar.remove_source/execute", json={
        "params": {"source_key": "test-feed"}
    })
    assert resp.status_code == 200
    assert resp.json()["item"]["ok"]


def test_add_duplicate_source_fails_gracefully(client):
    client.post("/api/operations/radar.add_source/execute", json={
        "params": {"key": "dup-feed", "url": "https://x.com/rss"}})
    resp = client.post("/api/operations/radar.add_source/execute", json={
        "params": {"key": "dup-feed", "url": "https://x.com/rss"}})
    assert resp.json()["item"]["status"] == "failed"
    client.post("/api/operations/radar.remove_source/execute", json={"params": {"source_key": "dup-feed"}})


# ── build_events without raw items ──────────────────────────────────────────
def test_build_events_nothing_to_cluster(client):
    # ensure no raw items
    resp = client.post("/api/operations/radar.build_events/execute", json={"params": {}})
    # may skip (no raw items) or succeed — both acceptable when empty
    assert resp.status_code == 200
    assert resp.json()["item"]["ok"]


# ── Full chain with stubbed network ─────────────────────────────────────────
def test_full_radar_chain_end_to_end(client):
    """seed → inject raw items → build_events → deep_dive, network stubbed."""
    from agent_news.db.intel_repository import get_intel_repository
    from agent_news.models.intel import RawItem

    repo = get_intel_repository()
    # ensure defaults seeded
    client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})

    # Inject fake raw items directly (simulating what sync_sources would produce).
    # Two items about the same story (should cluster), one unrelated.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    repo.add_raw_items([
        RawItem(id="t1", source_key="openai-blog", source_name="OpenAI",
                title="OpenAI releases GPT-5 with 1M token context",
                link="https://openai.com/blog/gpt5", summary="New flagship model",
                published_at=now, tags=["ai", "openai"]),
        RawItem(id="t2", source_key="anthropic-news", source_name="Anthropic",
                title="OpenAI launches GPT-5 model context window",
                link="https://anthropic.com/news/gpt5-reaction", summary="Reaction to GPT-5",
                published_at=now, tags=["ai"]),
        RawItem(id="t3", source_key="the-verge", source_name="The Verge",
                title="Local weather forecast shows rain this weekend",
                link="https://theverge.com/weather", summary="Weekend weather",
                published_at=now, tags=["news"]),
    ])

    # build_events: should cluster GPT-5 pair together, weather separate.
    resp = client.post("/api/operations/radar.build_events/execute", json={
        "params": {"watchlist": "openai,gpt"}
    })
    assert resp.status_code == 200
    result = resp.json()["item"]
    assert result["ok"], result["message"]
    assert result["state"]["event_count"] >= 2  # at least GPT-5 + weather

    # find the GPT-5 event (highest score, watchlist hit)
    resp = client.get("/api/intel/events")
    events = resp.json()["items"]
    assert len(events) >= 2
    gpt5 = max(events, key=lambda e: e["composite_score"])
    assert "GPT" in gpt5["title"] or "gpt" in gpt5["title"].lower()
    assert gpt5["audience_fit_score"] >= 60  # watchlist hit

    # deep_dive with network stubbed (fetch_and_extract_link returns minimal success)
    with patch("agent_news.intel.deep_dive.fetch_and_extract_link") as mock_fetch:
        from agent_news.models.intel import DeepDiveSourceItem
        mock_fetch.return_value = DeepDiveSourceItem(
            link="stub", fetch_status="success", extract_status="success",
            cleaned_full_text="OpenAI announced GPT-5 on Monday. The model has 1M token context. "
                              "CEO said \"this is our biggest leap\". Revenue reached $5 billion.",
            word_count=25, excerpt="stub excerpt",
        )
        resp = client.post("/api/operations/radar.deep_dive_event/execute", json={
            "params": {"event_id": gpt5["id"]}
        })
    assert resp.status_code == 200
    dd_result = resp.json()["item"]
    assert dd_result["ok"], dd_result["message"]
    assert dd_result["state"]["fact_count"] >= 1  # extracted at least one fact
    assert dd_result["state"]["source_count"] >= 1

    # verify deep dive is retrievable and has the writing guide
    resp = client.get(f"/api/intel/events/{gpt5['id']}/deep-dive")
    assert resp.status_code == 200
    dive = resp.json()["item"]
    assert dive["article_writing_guide"]  # non-empty house style guide
    assert "公众号" in dive["article_writing_guide"]


# ── Batch execution of the radar chain ──────────────────────────────────────
def test_radar_batch_chain(client):
    """Run multiple radar operations as one batch — the agent's typical pattern."""
    from agent_news.db.intel_repository import get_intel_repository
    from agent_news.models.intel import RawItem
    from datetime import datetime, timezone

    repo = get_intel_repository()
    repo.clear_raw_items()
    now = datetime.now(timezone.utc).isoformat()
    repo.add_raw_items([
        RawItem(id="b1", source_key="openai-blog", source_name="OpenAI",
                title="Batch test OpenAI announcement", link="https://a.com/1",
                published_at=now, tags=["ai"]),
    ])

    resp = client.post("/api/operations/batch", json={
        "steps": [
            {"op": "radar.build_events", "params": {"watchlist": "openai"}},
        ],
        "on_error": "stop"
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_ok"]
    assert len(body["results"]) == 1
    assert body["results"][0]["result"]["ok"]


# ── deep_dive on missing event ──────────────────────────────────────────────
def test_deep_dive_missing_event_fails_gracefully(client):
    resp = client.post("/api/operations/radar.deep_dive_event/execute", json={
        "params": {"event_id": "evt-doesnotexist"}
    })
    assert resp.status_code == 200  # operation ran, just failed
    assert resp.json()["item"]["status"] == "failed"
