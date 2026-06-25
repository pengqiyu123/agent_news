"""WeChat operations tests — registration + parameter validation.

These tests cover pure logic (registration, param validation, skip behavior)
without mocking the browser. Real browser behavior belongs in explicit
integration validation.

A separate integration test (skipped by default) can exercise the real browser
when WECHAT_INTEGRATION=1 is set.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app

    return TestClient(app)


# ── Registration ────────────────────────────────────────────────────────────
def test_all_wechat_operations_registered(client):
    resp = client.get("/api/operations")
    assert resp.status_code == 200
    names = {op["name"] for op in resp.json()["items"]}
    expected = {
        # navigation + login
        "wechat.open_dashboard",
        "wechat.check_login",
        "wechat.session",
        "wechat.open_new_editor",
        "wechat.open_draft_box",
        "wechat.open_publish_history",
        # drafts
        "wechat.open_existing_draft",
        "wechat.list_drafts",
        "wechat.review_draft_box",
        # editor
        "wechat.fill_editor_required",
        "wechat.fill_title",
        "wechat.fill_author",
        "wechat.fill_digest",
        "wechat.paste_body",
        "wechat.inspect_editor",
        # save / publish
        "wechat.save_as_draft",
        "wechat.save_current_editor_as_draft",
        "wechat.inspect_body_word_count",
        "wechat.publish_preflight",
        "wechat.click_publish",
        "wechat.confirm_publish_modal",
        "wechat.continue_publish",
        "wechat.wait_qrcode",
        "wechat.publish_to_qrcode",
        "wechat.publish_current_editor_to_qrcode",
        "wechat.publish_existing_draft_to_qrcode",
        "wechat.check_publish_done",
        # review
        "wechat.review_publish_history",
        "wechat.analyze_publish_metrics",
        # publish settings
        "wechat.set_original",
        "wechat.set_reward",
        "wechat.inspect_publish_settings",
        "wechat.settle_publish_settings",
        "wechat.set_collection",
        "wechat.set_claim_source",
        "wechat.generate_ai_cover",
        "wechat.inspect_cover_picker",
        "wechat.list_collections",
        "wechat.list_claim_sources",
    }
    missing = expected - names
    assert not missing, f"missing wechat operations: {missing}"


def test_wechat_operation_specs_have_metadata(client):
    """Each operation must carry description + category for AI discovery."""
    resp = client.get("/api/operations")
    wechat_ops = [op for op in resp.json()["items"] if op["name"].startswith("wechat.")]
    assert len(wechat_ops) >= 20
    valid_categories = {"navigation", "editor", "save_publish", "publish_settings", "review"}
    for spec in wechat_ops:
        assert spec["description"], f"{spec['name']} missing description"
        assert spec["category"] in valid_categories, f"{spec['name']} bad category: {spec['category']}"


# ── Parameter validation (skip when empty) ──────────────────────────────────
def test_fill_title_empty_text_skips(client):
    """Empty text → skip, not failure."""
    resp = client.post("/api/operations/wechat.fill_title/execute", json={"params": {"text": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_fill_author_empty_text_skips(client):
    resp = client.post("/api/operations/wechat.fill_author/execute", json={"params": {"text": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_fill_digest_empty_text_skips(client):
    resp = client.post("/api/operations/wechat.fill_digest/execute", json={"params": {"text": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_paste_body_empty_skips(client):
    resp = client.post("/api/operations/wechat.paste_body/execute", json={"params": {"markdown": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_fill_editor_required_missing_author_fails(client):
    """The complete editor-fill intent must fail fast when author is absent.

    Single-field operations may skip empty text, but the safe "upload article"
    entrypoint is stricter so agents cannot fill only title/body and continue.
    """
    resp = client.post("/api/operations/wechat.fill_editor_required/execute", json={
        "params": {
            "title": "标题",
            "author": "",
            "body_markdown": "正文",
        }
    })
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["status"] == "failed"
    assert "author" in item["state"]["missing"]


def test_publish_preflight_requires_author_even_when_title_and_body_exist(monkeypatch):
    """Publishing must be blocked when the editor has title/body but no author."""
    from agent_news.operations.wechat import save_publish
    from agent_news.operations.wechat import cover

    class _EditorPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

        def evaluate(self, script, arg=None):  # noqa: ARG002
            return {
                "original": {"ok": True, "checked": True},
                "reward": {"checked": False},
                "collection": {"ok": True},
                "claim_source": {"ok": True},
            }

    def fake_pick_selector(page, selectors, timeout=0):  # noqa: ARG001
        if any("js_author" in selector or "placeholder*='作者'" in selector for selector in selectors):
            return "author"
        if any(
            selector.startswith("div.ProseMirror[data-placeholder")
            or "js_article_title" in selector
            or selector.startswith("input[placeholder*='标题']")
            or selector.startswith("textarea[placeholder*='标题']")
            for selector in selectors
        ):
            return "title"
        return "body"

    def fake_read(page, selector, rich_text=False):  # noqa: ARG001
        values = {
            "title": "完整标题",
            "author": "",
            "body": "这是一段已经写入的正文。",
        }
        return values[selector]

    monkeypatch.setattr(save_publish, "pick_selector", fake_pick_selector)
    monkeypatch.setattr(save_publish, "read_locator_value", fake_read)
    monkeypatch.setattr(cover, "_read_cover_preview_state", lambda page: {"hasCover": True})

    result = save_publish._publish_preflight_result(_EditorPage())

    assert result.status == "failed"
    assert "author" in result.state["missing"]
    assert result.state["checks"]["title"] is True
    assert result.state["checks"]["body"] is True
    assert result.state["checks"]["author"] is False


def test_publish_preflight_blocks_when_wechat_body_word_count_is_zero(monkeypatch):
    """WeChat's bottom-bar body counter is authoritative for publish readiness."""
    from agent_news.operations.wechat import cover
    from agent_news.operations.wechat import save_publish

    class _EditorPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

        def evaluate(self, script, arg=None):  # noqa: ARG002
            return {
                "original": {"ok": True, "checked": True},
                "reward": {"checked": False},
                "collection": {"ok": True},
                "claim_source": {"ok": True},
            }

    def fake_pick_selector(page, selectors, timeout=0):  # noqa: ARG001
        if any("js_word_count" in selector for selector in selectors):
            return "word_count"
        if any("js_author" in selector or "placeholder*='作者'" in selector for selector in selectors):
            return "author"
        if any(
            selector.startswith("div.ProseMirror[data-placeholder")
            or "js_article_title" in selector
            or selector.startswith("input[placeholder*='标题']")
            or selector.startswith("textarea[placeholder*='标题']")
            for selector in selectors
        ):
            return "title"
        return "body"

    def fake_read(page, selector, rich_text=False):  # noqa: ARG001
        values = {
            "title": "完整标题",
            "author": "作者",
            "body": "这是一段看似存在的正文。",
            "word_count": "0",
        }
        return values[selector]

    monkeypatch.setattr(save_publish, "pick_selector", fake_pick_selector)
    monkeypatch.setattr(save_publish, "read_locator_value", fake_read)
    monkeypatch.setattr(cover, "_read_cover_preview_state", lambda page: {"hasCover": True})

    result = save_publish._publish_preflight_result(_EditorPage())

    assert result.status == "failed"
    assert "body" in result.state["missing"]
    assert result.state["checks"]["body"] is False
    assert result.state["body_word_count"] == 0
    assert result.state["body_word_count_source"] == "wechat_counter"


def test_save_current_editor_as_draft_blocks_when_body_word_count_is_zero(monkeypatch):
    """Saving a draft must not click when WeChat reports 正文字数 0."""
    from agent_news.operations.wechat import save_publish

    class _EditorPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

    clicked = {"value": False}

    def fake_pick_selector(page, selectors, timeout=0):  # noqa: ARG001
        if any("js_word_count" in selector for selector in selectors):
            return "word_count"
        return "body"

    def fake_read(page, selector, rich_text=False):  # noqa: ARG001
        values = {
            "body": "这是一段看似存在的正文。",
            "word_count": "0",
        }
        return values[selector]

    def fake_click(*args, **kwargs):  # noqa: ARG001
        clicked["value"] = True
        return "save_button"

    monkeypatch.setattr(save_publish, "pick_selector", fake_pick_selector)
    monkeypatch.setattr(save_publish, "read_locator_value", fake_read)
    monkeypatch.setattr(save_publish, "click_required_selector_once", fake_click)

    result = save_publish._save_current_editor_as_draft(_EditorPage())

    assert result.status == "failed"
    assert clicked["value"] is False
    assert result.state["body_word_count"] == 0


def test_click_publish_blocks_when_body_word_count_is_zero(monkeypatch):
    """The atomic publish click must not touch the publish button when body count is 0."""
    from agent_news.operations.wechat import save_publish

    class _EditorPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

    clicked = {"value": False}

    def fake_pick_selector(page, selectors, timeout=0):  # noqa: ARG001
        if any("js_word_count" in selector for selector in selectors):
            return "word_count"
        return "body"

    def fake_read(page, selector, rich_text=False):  # noqa: ARG001
        values = {
            "body": "这是一段看似存在的正文。",
            "word_count": "0",
        }
        return values[selector]

    def fake_click(*args, **kwargs):  # noqa: ARG001
        clicked["value"] = True
        return "publish_button"

    def fake_with_session(channel=None, *, action_fn, **kwargs):  # noqa: ARG001
        return action_fn(None, _EditorPage())

    monkeypatch.setattr(save_publish, "pick_selector", fake_pick_selector)
    monkeypatch.setattr(save_publish, "read_locator_value", fake_read)
    monkeypatch.setattr(save_publish, "click_required_selector_once", fake_click)
    monkeypatch.setattr(save_publish.BROWSER_MANAGER, "with_session", fake_with_session)

    result = save_publish.click_publish(None)

    assert result.status == "failed"
    assert clicked["value"] is False
    assert result.state["body_word_count"] == 0


def test_set_original_disabled_skips(client):
    resp = client.post("/api/operations/wechat.set_original/execute", json={"params": {"enabled": False}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_set_reward_disabled_skips(client):
    resp = client.post("/api/operations/wechat.set_reward/execute", json={"params": {"enabled": False}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_set_collection_empty_name_skips(client):
    resp = client.post("/api/operations/wechat.set_collection/execute", json={"params": {"name": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_set_claim_source_empty_name_skips(client):
    resp = client.post("/api/operations/wechat.set_claim_source/execute", json={"params": {"name": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_generate_ai_cover_empty_prompt_skips(client):
    resp = client.post("/api/operations/wechat.generate_ai_cover/execute", json={"params": {"prompt": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "skipped"


def test_open_existing_draft_no_title_fails(client):
    """Missing required param → failed (not crash)."""
    resp = client.post("/api/operations/wechat.open_existing_draft/execute", json={"params": {"title": ""}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "failed"


def test_review_draft_box_title_match_helper():
    from agent_news.operations.wechat.drafts import _find_draft_by_title

    items = [{"title": "AgentNews 保存草稿动作测试 20260623"}]

    assert _find_draft_by_title(items, "AgentNews 保存草稿动作测试 20260623")
    assert _find_draft_by_title(items, "AgentNews 保存草稿动作测试")
    assert _find_draft_by_title(items, "完全不存在") is None


def test_publish_metrics_analysis_extracts_quality_signals():
    from agent_news.operations.wechat.history import _analyze_publish_metrics

    items = [
        {
            "title": "示例文章",
            "read_count": 100,
            "like_count": 5,
            "share_count": 2,
            "recommend_count": 1,
            "comment_count": 3,
            "highlight_count": 4,
            "tip_amount": "6.66",
            "reprint_count": 1,
        }
    ]

    result = _analyze_publish_metrics(items, title="示例文章")

    assert result["target_found"] is True
    assert result["scope"] == "matched_title"
    assert result["summary"]["total_reads"] == 100
    assert result["summary"]["total_likes"] == 5
    assert result["summary"]["total_shares"] == 2
    assert result["summary"]["overall_engagement_rate"] == 0.16
    assert result["matched_item"]["signals"]["spread"] == 3
    assert result["matched_item"]["signals"]["monetization"] == 6.66
    assert result["matched_item"]["quality_score"] > 100


# ── Graceful failure without browser ────────────────────────────────────────
def test_open_dashboard_fails_gracefully_without_browser(client):
    """Mock BROWSER_MANAGER.with_session to raise; op must return failed, not 500."""
    from unittest.mock import patch as _patch
    from agent_news.browser import BROWSER_MANAGER

    def _boom(*a, **k):
        raise RuntimeError("no browser available (mocked)")

    with _patch.object(BROWSER_MANAGER, "with_session", _boom):
        resp = client.post("/api/operations/wechat.open_dashboard/execute", json={"params": {}})
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "failed"
    assert resp.json()["item"]["message"]


# ── Batch isolation ─────────────────────────────────────────────────────────
def test_batch_radar_then_wechat_isolates_failures(client):
    """A batch mixing radar + wechat: radar succeeds, a wechat step that needs
    an editor (fill_title) fails gracefully, on_error=continue keeps going."""
    from agent_news.db.intel_repository import get_intel_repository
    from agent_news.models.intel import RawItem
    from datetime import datetime, timezone
    from unittest.mock import patch as _patch
    from agent_news.browser import BROWSER_MANAGER

    repo = get_intel_repository()
    repo.clear_raw_items()
    now = datetime.now(timezone.utc).isoformat()
    repo.add_raw_items([
        RawItem(id="wb1", source_key="openai-blog", source_name="OpenAI",
                title="Batch isolation test OpenAI", link="https://a.com/1",
                published_at=now, tags=["ai"]),
    ])

    class _HomePage:
        url = "https://mp.weixin.qq.com/cgi-bin/home"

    def _with_home_page(channel=None, *, action_fn, **kwargs):
        return action_fn(None, _HomePage())

    with _patch.object(BROWSER_MANAGER, "with_session", _with_home_page):
        resp = client.post("/api/operations/batch", json={
            "steps": [
                {"op": "radar.build_events", "params": {"watchlist": "openai"}},
                {"op": "wechat.fill_title", "params": {"text": "needs editor first"}},
            ],
            "on_error": "continue",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 2
    assert body["results"][0]["result"]["status"] != "failed"
    assert body["results"][1]["result"]["status"] == "failed"
    assert not body["stopped_early"]


# ── Integration test (skipped unless explicitly enabled) ────────────────────
@pytest.mark.skipif(
    not os.getenv("WECHAT_INTEGRATION"),
    reason="requires real browser + WeChat login; set WECHAT_INTEGRATION=1 to run",
)
def test_real_open_dashboard(client):
    """Real browser smoke test. Requires WeChat login state in browser_profile."""
    resp = client.post("/api/operations/wechat.open_dashboard/execute", json={"params": {}})
    assert resp.status_code == 200
    result = resp.json()["item"]
    assert result["ok"], result["message"]
