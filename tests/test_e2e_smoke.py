"""End-to-end smoke test: the full agent workflow through HTTP.

Verifies the complete chain an agent would drive:
  seed → sync(stubbed) → build_events → pick event → deep_dive(stubbed)
  → create article → create workflow → transition through states

Network is stubbed (RSS + deep-dive fetches). Wechat browser ops are out of
scope here (covered by test_wechat_operations.py + test_publish_settings.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app

    return TestClient(app)


def test_full_agent_workflow_e2e(client):
    """The agent's day, end to end."""
    # ── 1. Seed sources ─────────────────────────────────────────────────────
    client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})
    sources = client.get("/api/intel/sources").json()
    assert sources["total"] > 0

    # ── 2. Inject raw items (simulating sync_sources — stubbed to avoid network) ──
    from agent_news.db.intel_repository import get_intel_repository
    from agent_news.models.intel import RawItem

    repo = get_intel_repository()
    repo.clear_raw_items()
    now = datetime.now(timezone.utc).isoformat()
    repo.add_raw_items([
        RawItem(id="e2e-1", source_key="openai-blog", source_name="OpenAI",
                title="OpenAI launches GPT-6 with multimodal reasoning",
                link="https://openai.com/blog/gpt6", summary="Flagship launch",
                published_at=now, tags=["ai", "openai"]),
        RawItem(id="e2e-2", source_key="anthropic-news", source_name="Anthropic",
                title="OpenAI GPT-6 launch reaction from Anthropic",
                link="https://anthropic.com/news/gpt6", summary="Competitor response",
                published_at=now, tags=["ai"]),
        RawItem(id="e2e-3", source_key="techcrunch-ai", source_name="TechCrunch",
                title="Local farmers market opens this Saturday",
                link="https://techcrunch.com/farmers", summary="Weekend event",
                published_at=now, tags=["news"]),
    ])

    # ── 3. Build events ─────────────────────────────────────────────────────
    resp = client.post("/api/operations/radar.build_events/execute", json={
        "params": {"watchlist": "openai,gpt,ai"}
    })
    assert resp.json()["item"]["ok"]
    event_count = resp.json()["item"]["state"]["event_count"]
    assert event_count >= 2  # GPT-6 cluster + farmers market

    # ── 4. Pick the hottest event ───────────────────────────────────────────
    events = client.get("/api/intel/events").json()["items"]
    gpt_events = [event for event in events if "GPT" in event["title"]]
    top_event = max(gpt_events, key=lambda e: e["composite_score"])
    assert "GPT" in top_event["title"]
    event_id = top_event["id"]

    # ── 5. Deep dive (network stubbed) ──────────────────────────────────────
    with patch("agent_news.intel.deep_dive.fetch_and_extract_link") as mock_fetch:
        from agent_news.models.intel import DeepDiveSourceItem
        mock_fetch.return_value = DeepDiveSourceItem(
            link="stub", fetch_status="success", extract_status="success",
            cleaned_full_text=(
                "OpenAI announced GPT-6 on Tuesday. The model costs $20/month. "
                "Sam Altman said \"this changes everything\". Revenue hit $5 billion."
            ),
            word_count=20, excerpt="stub",
        )
        resp = client.post("/api/operations/radar.deep_dive_event/execute", json={
            "params": {"event_id": event_id}
        })
    assert resp.json()["item"]["ok"]
    dive = client.get(f"/api/intel/events/{event_id}/deep-dive").json()["item"]
    assert dive["facts"]  # extracted at least one fact
    assert "公众号" in dive["article_writing_guide"]  # writing guide attached

    # ── 6. Agent writes the article (simulated) → create article ────────────
    article = client.post("/api/articles", json={
        "title": "GPT-6 发布：多模态推理新突破",
        "digest": "OpenAI 发布 GPT-6，支持多模态推理",
        "body_markdown": "# GPT-6 发布\n\nOpenAI 今日发布 GPT-6...",
        "author": "AI新闻",
    }).json()["item"]
    article_id = article["id"]

    # ── 7. Create workflow + walk the state machine ────────────────────────
    wf = client.post(f"/api/workflows?article_id={article_id}").json()["item"]
    wf_id = wf["id"]
    assert wf["state"] == "init"

    # legal transitions
    for target in ["editor_open", "content_filled", "settings_applied", "cover_ready", "saved"]:
        resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": target})
        assert resp.status_code == 200, f"transition to {target} failed: {resp.json()}"

    # SAVED → PUBLISHED is now blocked; must go through pending_confirmation.
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "published"})
    assert resp.status_code == 422  # blocked by pending_confirmation gate

    # legal path: saved → pending_confirmation → published
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "pending_confirmation"})
    assert resp.status_code == 200
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "published"})
    assert resp.status_code == 200
    assert resp.json()["item"]["state"] == "published"
    assert resp.json()["item"]["finished_at"]  # terminal timestamp set

    # ── 8. Illegal transition from terminal rejected ───────────────────────
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "editor_open"})
    assert resp.status_code == 422

    print("\n=== E2E SMOKE PASSED ===")
    print(f"events built: {event_count}")
    print(f"top event: {top_event['title']} (score={top_event['composite_score']})")
    print(f"deep dive facts: {len(dive['facts'])}")
    print(f"article: {article_id}")
    print(f"workflow: {wf_id} → published")
