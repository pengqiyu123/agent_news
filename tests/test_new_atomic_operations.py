from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient


def _client():
    from agent_news.main import app

    return TestClient(app)


def test_new_operations_registered():
    client = _client()
    names = {op["name"] for op in client.get("/api/operations").json()["items"]}
    expected = {
        "radar.status",
        "radar.review_sources",
        "radar.review_events",
        "radar.review_deep_dive",
        "radar.discover_sources",
        "radar.validate_source",
        "radar.propose_source",
        "radar.add_validated_source",
        "radar.source_health_report",
        "radar.disable_stale_sources",
        "article.create",
        "article.get",
        "article.list",
        "article.update",
        "article.review_quality",
        "article.prepare_wechat_payload",
        "audit.review_tasks",
        "workflow.status",
        "wechat.inspect_tabs",
        "wechat.focus_editor_tab",
        "wechat.close_blank_tabs",
        "wechat.upload_cover_file",
        "wechat.review_content_strategy",
    }
    assert expected <= names


def test_radar_status_and_review_events_empty():
    client = _client()
    resp = client.post("/api/operations/radar.status/execute", json={"params": {}})
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["ok"]
    assert "source_count" in item["state"]
    assert "suggested_next_operation" in item["state"]

    resp = client.post("/api/operations/radar.review_events/execute", json={"params": {"limit": 3}})
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["ok"]
    assert "events" in item["state"]


def test_review_deep_dive_missing_skips():
    client = _client()
    resp = client.post("/api/operations/radar.review_deep_dive/execute", json={
        "params": {"event_id": "evt-missing-new-ops"}
    })
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["status"] == "skipped"
    assert item["state"]["suggested_next_operation"] == "radar.deep_dive_event"


def test_review_sources_probe_false_does_not_fetch(monkeypatch):
    from agent_news.db.intel_repository import get_intel_repository
    from agent_news.models.intel import Source
    import agent_news.operations.radar as radar_ops

    repo = get_intel_repository()
    repo.upsert_source(Source(key="probe-false-source", name="Probe False", url="https://example.com/rss"))

    def boom(*args, **kwargs):
        raise AssertionError("probe_source should not be called")

    monkeypatch.setattr(radar_ops, "probe_source", boom)
    client = _client()
    resp = client.post("/api/operations/radar.review_sources/execute", json={
        "params": {"probe": False, "source_key": "probe-false-source"}
    })
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["ok"]
    assert item["state"]["sources"][0]["probe_status"] == "not_run"


def test_validate_and_add_validated_source(monkeypatch):
    from agent_news.models.intel import RawItem
    import agent_news.intel.source_probe as source_probe

    def fake_probe(source, *, limit_per_source=3, include_items=False):
        return source_probe.ProbeResult(
            source_key=source.key,
            source_name=source.name,
            status="ok",
            item_count=5,
            sample_items=[
                {"title": f"Item {i}", "link": f"https://valid.example/{i}", "published_at": datetime.now(timezone.utc).isoformat()}
                for i in range(5)
            ],
            items=[
                RawItem(id=f"valid-{i}", source_key=source.key, source_name=source.name,
                        title=f"Item {i}", link=f"https://valid.example/{i}")
                for i in range(5)
            ],
        )

    monkeypatch.setattr("agent_news.intel.source_discovery.probe_source", fake_probe)
    client = _client()
    url = f"https://valid-source-{uuid4().hex[:8]}.example/rss.xml"
    resp = client.post("/api/operations/radar.validate_source/execute", json={
        "params": {"url": url, "kind": "rss", "topic": "ai"}
    })
    assert resp.status_code == 200
    validated = resp.json()["item"]["state"]
    assert validated["decision"] == "auto_add"
    assert validated["score"] >= 80

    resp = client.post("/api/operations/radar.add_validated_source/execute", json={
        "params": {"validated_source": validated}
    })
    assert resp.status_code == 200
    added = resp.json()["item"]
    assert added["ok"], added["message"]
    assert added["state"]["suggested_next_operation"] == "radar.sync_one_source"

    resp = client.post("/api/operations/radar.add_validated_source/execute", json={
        "params": {"validated_source": validated}
    })
    assert resp.json()["item"]["status"] == "failed"


def test_add_validated_source_rejects_unverified():
    client = _client()
    resp = client.post("/api/operations/radar.add_validated_source/execute", json={
        "params": {"validated_source": {"valid": False, "decision": "reject", "suggested_source": {"key": "bad", "url": "https://bad"}}}
    })
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "failed"


def test_article_operations_and_wechat_payload():
    client = _client()
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "测试文章",
            "author": "作者",
            "digest": "摘要",
            "body_markdown": "## 小标题\n\n正文内容",
        }
    })
    assert resp.status_code == 200
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id, "override_quality_gate": True}
    })
    assert resp.status_code == 200
    state = resp.json()["item"]["state"]
    assert state["title"] == "测试文章"
    assert state["ready_for_wechat_fill"] is True
    assert state["missing_required"] == []
    assert state["suggested_steps"][0]["op"] == "wechat.fill_editor_required"
    assert "文字" in state["cover_prompt"]
    assert "海报" in state["cover_prompt"]


def test_publish_task_snapshot_lookup_by_analysis_key():
    from agent_news.content.publish_performance import build_publish_metrics_analysis
    from agent_news.db import get_repository

    repo = get_repository()
    analysis = build_publish_metrics_analysis(
        [
            {
                "title": "快照测试文章",
                "url": "https://mp.weixin.qq.com/s/snapshot",
                "appmsg_id": "snapshot-1",
                "published_at": "2026-06-25 09:00",
                "read_count": 12,
                "like_count": 1,
                "share_count": 0,
                "recommend_count": 0,
                "comment_count": 0,
                "highlight_count": 0,
                "tip_amount": "0",
                "reprint_count": 0,
            }
        ],
        url="https://mp.weixin.qq.com/s/snapshot",
        snapshot_at="2026-06-26T00:00:00+00:00",
    )
    repo.record_publish_task(
        operation_name="wechat.analyze_publish_metrics",
        status="success",
        message="snapshot",
        params={"state": analysis},
    )

    items, total = repo.list_publish_task_snapshots(
        operation_name="wechat.analyze_publish_metrics",
        analysis_key=analysis["analysis_key"],
        limit=5,
    )
    assert total >= 1
    assert items
    assert items[0].operation_name == "wechat.analyze_publish_metrics"


def test_review_content_performance_operation_registered():
    client = _client()
    names = {op["name"] for op in client.get("/api/operations").json()["items"]}
    assert "wechat.review_content_performance" in names


def test_review_content_strategy_uses_latest_metrics_snapshot():
    from agent_news.content.publish_performance import build_publish_metrics_analysis
    from agent_news.db import get_repository

    repo = get_repository()
    analysis = build_publish_metrics_analysis(
        [
            {
                "title": "三星2nm翻车、苹果涨价、美国缺电：AI正在掏空你的钱包和电网",
                "url": "https://mp.weixin.qq.com/s/strategy",
                "published_at": "2026-06-26 20:00",
                "read_count": 134,
                "share_count": 3,
            }
        ],
        snapshot_at="2026-06-26T12:00:00+00:00",
    )
    repo.record_publish_task(
        operation_name="wechat.analyze_publish_metrics",
        status="success",
        message="snapshot",
        params={"state": {"analysis": analysis}},
    )

    client = _client()
    resp = client.post("/api/operations/wechat.review_content_strategy/execute", json={"params": {}})
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["ok"]
    profile = item["state"]["content_strategy_profile"]
    assert profile["available"] is True
    assert "涨价" in profile["impact_keywords"]
    assert profile["winning_titles"][0]["read_count"] == 134


def test_prepare_wechat_payload_requires_author():
    client = _client()
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "缺作者文章",
            "digest": "摘要",
            "body_markdown": "正文内容",
        }
    })
    assert resp.status_code == 200
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id}
    })
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["status"] == "failed"
    assert item["state"]["missing_required"] == ["author"]
    assert item["state"]["ready_for_wechat_fill"] is False
    assert item["state"]["suggested_steps"] == []
    assert item["state"]["suggested_next_operation"] == "article.update"


def test_audit_and_workflow_status():
    client = _client()
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {"title": "工作流文章", "body_markdown": "正文", "author": "作者"}
    })
    article_id = resp.json()["item"]["state"]["article_id"]
    wf_resp = client.post(f"/api/workflows?article_id={article_id}")
    assert wf_resp.status_code == 200
    workflow_id = wf_resp.json()["item"]["id"]

    resp = client.post("/api/operations/workflow.status/execute", json={
        "params": {"workflow_session_id": workflow_id}
    })
    assert resp.status_code == 200
    state = resp.json()["item"]["state"]
    assert state["workflow_session_id"] == workflow_id
    assert "editor_open" in state["allowed_next_states"]

    resp = client.post("/api/operations/audit.review_tasks/execute", json={"params": {"limit": 10}})
    assert resp.status_code == 200
    assert "items" in resp.json()["item"]["state"]
