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
        "wechat.inspect_publish_dialog",
        "wechat.confirm_publish_modal",
        "wechat.confirm_publish_no_notify",
        "wechat.continue_publish",
        "wechat.wait_qrcode",
        "wechat.publish_to_qrcode",
        "wechat.publish_current_editor_to_qrcode",
        "wechat.publish_existing_draft_to_qrcode",
        "wechat.check_publish_done",
        # review
        "wechat.review_publish_history",
        "wechat.analyze_publish_metrics",
        "wechat.pin_publish_record",
        "wechat.set_publish_record_private",
        "wechat.delete_publish_record",
        "wechat.close_publish_record_recommendation",
        "wechat.copy_publish_record_link",
        "wechat.change_publish_record_collection",
        "wechat.change_publish_record_claim_source",
        # publish settings
        "wechat.set_original",
        "wechat.set_original_author",
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


def _publish_dialog_state(dialog_type: str, **overrides):
    state = {
        "dialog_type": dialog_type,
        "url": "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit",
        "dialog_text": "",
        "buttons": [],
        "matched_reason": f"test {dialog_type}",
        "requires_relogin": False,
        "requires_human_scan": dialog_type == "qrcode",
    }
    state.update(overrides)
    return state


class _PublishPage:
    url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

    def wait_for_timeout(self, timeout):  # noqa: ARG002
        return None

    def screenshot(self, path, full_page=True):  # noqa: ARG002
        from pathlib import Path

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake screenshot")


def _patch_publish_preflight_and_first_click(monkeypatch):
    from agent_news.models.operation import OperationResult
    from agent_news.operations.wechat import save_publish

    first_clicks = []
    monkeypatch.setattr(
        save_publish,
        "_publish_preflight_result",
        lambda page, **requirements: OperationResult.success(message="preflight ok"),
    )
    monkeypatch.setattr(save_publish, "_capture_publish_dialog_screenshot", lambda page, label: None)

    def fake_click_required(page, selectors, *, step_name, timeout=0, settle_ms=0):  # noqa: ARG001
        first_clicks.append(step_name)
        return "#js_send button.mass_send"

    monkeypatch.setattr(save_publish, "click_required_selector_once", fake_click_required)
    return first_clicks


def test_publish_modal_selector_profile_has_no_broad_fallbacks():
    from agent_news.browser.selectors import get

    selectors = get("publish_modal_button")

    assert ".weui-desktop-dialog__wrp :text-is('发表')" not in selectors
    assert ".weui-desktop-dialog__wrp .weui-desktop-dialog__bd div:has-text('发表')" not in selectors
    assert ".weui-desktop-dialog__wrp [class*='send'] :has-text('发表')" not in selectors
    assert all("button" in selector for selector in selectors)


def test_publish_to_qrcode_stops_on_account_auth_error(monkeypatch):
    from agent_news.operations.wechat import save_publish

    _patch_publish_preflight_and_first_click(monkeypatch)
    dialog_clicks = []
    monkeypatch.setattr(
        save_publish,
        "_inspect_publish_dialog_state",
        lambda page: _publish_dialog_state(
            "account_auth_error",
            dialog_text="未授权使用切换账号能力，请退出后扫码登录其他账号",
            requires_relogin=True,
        ),
    )
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text),
    )

    result = save_publish._publish_current_editor_to_qrcode(_PublishPage())

    assert result.status == "failed"
    assert result.state["requires_relogin"] is True
    assert result.state["publish_dialog"]["dialog_type"] == "account_auth_error"
    assert dialog_clicks == []


def test_publish_to_qrcode_stops_on_unknown_dialog(monkeypatch):
    from agent_news.operations.wechat import save_publish

    _patch_publish_preflight_and_first_click(monkeypatch)
    dialog_clicks = []
    monkeypatch.setattr(
        save_publish,
        "_inspect_publish_dialog_state",
        lambda page: _publish_dialog_state("unknown_dialog", dialog_text="未知弹窗", buttons=[{"text": "切换账号"}]),
    )
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text),
    )

    result = save_publish._publish_current_editor_to_qrcode(_PublishPage())

    assert result.status == "failed"
    assert result.state["publish_dialog"]["dialog_type"] == "unknown_dialog"
    assert dialog_clicks == []


def test_publish_to_qrcode_clicks_exact_publish_confirm(monkeypatch):
    from agent_news.operations.wechat import save_publish

    _patch_publish_preflight_and_first_click(monkeypatch)
    states = iter([
        _publish_dialog_state("publish_confirm", buttons=[{"text": "取消"}, {"text": "发表"}]),
        _publish_dialog_state("qrcode", qrcode_selector="img.js_qrcode"),
    ])
    dialog_clicks = []
    monkeypatch.setattr(save_publish, "_inspect_publish_dialog_state", lambda page: next(states))

    def fake_click(page, text):  # noqa: ARG001
        dialog_clicks.append(text)
        return {"clicked": True, "text": text}

    monkeypatch.setattr(save_publish, "_click_visible_dialog_button_exact", fake_click)

    result = save_publish._publish_current_editor_to_qrcode(_PublishPage())

    assert result.status == "ok"
    assert result.state["reached_qrcode"] is True
    assert result.state["requires_human_scan"] is True
    assert dialog_clicks == ["发表"]


def test_publish_to_qrcode_clicks_exact_continue_publish(monkeypatch):
    from agent_news.operations.wechat import save_publish

    _patch_publish_preflight_and_first_click(monkeypatch)
    states = iter([
        _publish_dialog_state("continue_publish", buttons=[{"text": "取消"}, {"text": "继续发表"}]),
        _publish_dialog_state("qrcode", qrcode_selector="img.js_qrcode"),
    ])
    dialog_clicks = []
    monkeypatch.setattr(save_publish, "_inspect_publish_dialog_state", lambda page: next(states))
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text) or {"clicked": True, "text": text},
    )

    result = save_publish._publish_current_editor_to_qrcode(_PublishPage())

    assert result.status == "ok"
    assert result.state["reached_qrcode"] is True
    assert result.state["continue_clicks"] == 1
    assert dialog_clicks == ["继续发表"]


def test_publish_to_qrcode_clicks_exact_no_notify_continue(monkeypatch):
    from agent_news.operations.wechat import save_publish

    _patch_publish_preflight_and_first_click(monkeypatch)
    states = iter([
        _publish_dialog_state(
            "publish_no_notify",
            dialog_text="未开启群发通知 内容将展示在公众号主页",
            buttons=[{"text": "取消"}, {"text": "继续发表"}],
        ),
        _publish_dialog_state("qrcode", qrcode_selector="img.js_qrcode"),
    ])
    dialog_clicks = []
    monkeypatch.setattr(save_publish, "_inspect_publish_dialog_state", lambda page: next(states))
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text) or {"clicked": True, "text": text},
    )

    result = save_publish._publish_current_editor_to_qrcode(_PublishPage())

    assert result.status == "ok"
    assert result.state["reached_qrcode"] is True
    assert result.state["continue_clicks"] == 1
    assert dialog_clicks == ["继续发表"]
    assert any(log["step"] == "confirm_publish_no_notify" for log in result.state["publish_step_logs"])


def test_confirm_publish_no_notify_only_clicks_no_notify_state(monkeypatch):
    from agent_news.operations.wechat import save_publish

    dialog_clicks = []
    monkeypatch.setattr(
        save_publish,
        "_inspect_publish_dialog_state",
        lambda page: _publish_dialog_state(
            "publish_no_notify",
            dialog_text="未开启群发通知",
            buttons=[{"text": "继续发表"}],
        ),
    )
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text) or {"clicked": True, "text": text},
    )
    monkeypatch.setattr(
        save_publish.BROWSER_MANAGER,
        "with_session",
        lambda channel=None, *, action_fn, **kwargs: action_fn(None, _PublishPage()),  # noqa: ARG005
    )

    result = save_publish.confirm_publish_no_notify(None)

    assert result.status == "ok"
    assert result.state["publish_no_notify_confirmed"] is True
    assert dialog_clicks == ["继续发表"]


def test_confirm_publish_no_notify_refuses_other_states(monkeypatch):
    from agent_news.operations.wechat import save_publish

    dialog_clicks = []
    monkeypatch.setattr(
        save_publish,
        "_inspect_publish_dialog_state",
        lambda page: _publish_dialog_state("continue_publish", buttons=[{"text": "继续发表"}]),
    )
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text),
    )
    monkeypatch.setattr(
        save_publish.BROWSER_MANAGER,
        "with_session",
        lambda channel=None, *, action_fn, **kwargs: action_fn(None, _PublishPage()),  # noqa: ARG005
    )

    result = save_publish.confirm_publish_no_notify(None)

    assert result.status == "failed"
    assert result.state["publish_dialog"]["dialog_type"] == "continue_publish"
    assert dialog_clicks == []


def test_inspect_publish_dialog_classifies_no_notify(monkeypatch):
    from agent_news.operations.wechat import save_publish

    class _NoNotifyPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

        def evaluate(self, script, arg=None):  # noqa: ARG002
            return {
                "dialog_found": True,
                "publish_no_notify_panel_visible": True,
                "dialog_text": "未开启群发通知 内容将展示在公众号主页",
                "page_text": "未开启群发通知 内容将展示在公众号主页",
                "buttons": [
                    {"text": "取消", "visible": True, "disabled": False},
                    {"text": "继续发表", "visible": True, "disabled": False, "primary": True},
                ],
            }

    monkeypatch.setattr(save_publish, "pick_selector", lambda page, selectors, timeout=0: None)

    state = save_publish._inspect_publish_dialog_state(_NoNotifyPage())

    assert state["dialog_type"] == "publish_no_notify"
    assert state["matched_button"]["text"] == "继续发表"


def test_publish_to_qrcode_returns_qrcode_without_extra_click(monkeypatch):
    from agent_news.operations.wechat import save_publish

    _patch_publish_preflight_and_first_click(monkeypatch)
    dialog_clicks = []
    monkeypatch.setattr(
        save_publish,
        "_inspect_publish_dialog_state",
        lambda page: _publish_dialog_state("qrcode", qrcode_selector="img.js_qrcode"),
    )
    monkeypatch.setattr(
        save_publish,
        "_click_visible_dialog_button_exact",
        lambda page, text: dialog_clicks.append(text),
    )

    result = save_publish._publish_current_editor_to_qrcode(_PublishPage())

    assert result.status == "ok"
    assert result.state["reached_qrcode"] is True
    assert result.state["requires_human_scan"] is True
    assert dialog_clicks == []


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


def test_publish_metrics_analysis_supports_exact_url_matching():
    from agent_news.operations.wechat.history import _analyze_publish_metrics

    items = [
        {
            "title": "同标题文章",
            "url": "https://mp.weixin.qq.com/s/abc123",
            "appmsg_id": "10001",
            "published_at": "2026-06-25 10:00",
            "read_count": 10,
            "like_count": 1,
            "share_count": 0,
            "recommend_count": 0,
            "comment_count": 0,
            "highlight_count": 0,
            "tip_amount": "0",
            "reprint_count": 0,
        },
        {
            "title": "同标题文章",
            "url": "https://mp.weixin.qq.com/s/def456",
            "appmsg_id": "10002",
            "published_at": "2026-06-25 11:00",
            "read_count": 20,
            "like_count": 2,
            "share_count": 1,
            "recommend_count": 0,
            "comment_count": 0,
            "highlight_count": 0,
            "tip_amount": "0",
            "reprint_count": 0,
        },
    ]

    result = _analyze_publish_metrics(items, url="https://mp.weixin.qq.com/s/def456")
    assert result["target_found"] is True
    assert result["target_status"] == "exact_url"
    assert result["analysis_key"].startswith("url:https://mp.weixin.qq.com/s/def456")
    assert result["summary"]["total_reads"] == 20


def test_review_content_performance_requires_locator(client):
    resp = client.post("/api/operations/wechat.review_content_performance/execute", json={"params": {}})
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["status"] == "failed"
    assert "title 或 url" in item["message"]


def test_analyze_publish_metrics_operation_uses_snapshot_time(monkeypatch):
    from agent_news.operations.wechat import history

    class _PublishHistoryPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list"

    monkeypatch.setattr(history, "_open_publish_history_on_page", lambda page, logs: logs.append("nav ok") or True)
    monkeypatch.setattr(
        history,
        "_scrape_publish_history_pages",
        lambda page, max_pages, limit: (
            [
                {
                    "title": "OpenAI芯片成本砍半：开发者账单重算",
                    "url": "https://mp.weixin.qq.com/s/metric-op",
                    "published_at": "2026-06-27 10:00",
                    "read_count": 88,
                    "share_count": 3,
                }
            ],
            ["scrape ok"],
        ),
    )
    monkeypatch.setattr(
        history.BROWSER_MANAGER,
        "with_session",
        lambda channel=None, *, action_fn, **kwargs: action_fn(None, _PublishHistoryPage()),  # noqa: ARG005
    )

    result = history.analyze_publish_metrics(None, limit=1, max_pages=1)

    assert result.status == "ok"
    assert result.state["analysis_snapshot_at"]
    assert result.state["analysis"]["analysis_snapshot_at"] == result.state["analysis_snapshot_at"]
    assert result.state["content_strategy_profile"]["causal_claim_allowed"] is False


def _patch_delete_publish_record_nav(monkeypatch):
    from agent_news.operations.wechat import history

    class _PublishHistoryPage:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list"

        def wait_for_timeout(self, timeout):  # noqa: ARG002
            return None

    monkeypatch.setattr(history, "_open_publish_history_on_page", lambda page, logs: logs.append("nav ok") or True)
    monkeypatch.setattr(
        history.BROWSER_MANAGER,
        "with_session",
        lambda channel=None, *, action_fn, **kwargs: action_fn(None, _PublishHistoryPage()),  # noqa: ARG005
    )


def test_delete_publish_record_default_opens_dialog_but_does_not_confirm(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    confirm_clicks = []
    monkeypatch.setattr(
        history,
        "_open_delete_publish_record_dialog",
        lambda page, title, target_url="": {
            "ok": True,
            "matched_title": title,
            "target_url": target_url,
            "action": "delete_option_clicked",
        },
    )
    monkeypatch.setattr(
        history,
        "_inspect_delete_publish_record_dialog",
        lambda page: {
            "dialog_type": "delete_confirm",
            "dialog_text": "删除后用户将无法访问此页面，确定删除？",
            "buttons": [{"text": "确认", "visible": True, "disabled": False}],
        },
    )
    monkeypatch.setattr(history, "_click_delete_confirm_button", lambda page: confirm_clicks.append(True))

    result = history.delete_publish_record(None, title="目标文章", confirmed=False)

    assert result.status == "skipped"
    assert result.state["requires_confirmation"] is True
    assert result.state["deleted"] is False
    assert result.state["matched_title"] == "目标文章"
    assert confirm_clicks == []


def test_delete_publish_record_confirmed_clicks_exact_confirm(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    confirm_clicks = []
    inspect_calls = {"count": 0}
    monkeypatch.setattr(
        history,
        "_open_delete_publish_record_dialog",
        lambda page, title, target_url="": {
            "ok": True,
            "matched_title": title,
            "target_url": target_url,
            "action": "delete_option_clicked",
        },
    )
    def fake_inspect_delete_dialog(page):  # noqa: ARG001
        inspect_calls["count"] += 1
        if inspect_calls["count"] == 1:
            return {
                "dialog_type": "delete_confirm",
                "dialog_text": "删除后用户将无法访问此页面，确定删除？",
                "buttons": [{"text": "确认", "visible": True, "disabled": False}],
            }
        return {
            "dialog_type": "none",
            "dialog_text": "",
            "buttons": [],
        }

    monkeypatch.setattr(history, "_inspect_delete_publish_record_dialog", fake_inspect_delete_dialog)
    monkeypatch.setattr(
        history,
        "_click_publish_record_menu_option",
        lambda page, title, option_text, target_url="": {  # noqa: ARG005
            "ok": False,
            "reason": "target_not_found",
        },
    )
    monkeypatch.setattr(
        history,
        "_click_delete_confirm_button",
        lambda page: confirm_clicks.append("确认") or {"clicked": True, "text": "确认"},
    )

    result = history.delete_publish_record(None, title="目标文章", confirmed=True)

    assert result.status == "ok"
    assert result.state["deleted"] is True
    assert result.state["button"]["text"] == "确认"
    assert confirm_clicks == ["确认"]


def test_delete_publish_record_confirmed_fails_when_record_still_locatable(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    inspect_calls = {"count": 0}
    open_calls = {"count": 0}

    def fake_open_delete_dialog(page, title, target_url=""):  # noqa: ARG001
        open_calls["count"] += 1
        return {
            "ok": True,
            "matched_title": title,
            "target_url": target_url,
            "action": "delete_option_clicked",
        }

    def fake_inspect_delete_dialog(page):  # noqa: ARG001
        inspect_calls["count"] += 1
        if inspect_calls["count"] == 1:
            return {
                "dialog_type": "delete_confirm",
                "dialog_text": "删除后用户将无法访问此页面，确定删除？",
                "buttons": [{"text": "确认", "visible": True, "disabled": False}],
            }
        return {
            "dialog_type": "none",
            "dialog_text": "",
            "buttons": [],
        }

    monkeypatch.setattr(history, "_open_delete_publish_record_dialog", fake_open_delete_dialog)
    monkeypatch.setattr(
        history,
        "_click_publish_record_menu_option",
        lambda page, title, option_text, target_url="": {
            "ok": True,
            "matched_title": title,
            "target_url": target_url,
            "action": "menu_option_clicked",
        },
    )
    monkeypatch.setattr(history, "_inspect_delete_publish_record_dialog", fake_inspect_delete_dialog)
    monkeypatch.setattr(
        history,
        "_click_delete_confirm_button",
        lambda page: {"clicked": True, "text": "确认"},
    )

    result = history.delete_publish_record(None, title="目标文章", confirmed=True)

    assert result.status == "failed"
    assert result.state["deleted"] is False
    assert result.state["post_locate_result"]["ok"] is True


def test_delete_publish_record_confirmed_reports_scan_verification(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    inspect_calls = {"count": 0}

    monkeypatch.setattr(
        history,
        "_open_delete_publish_record_dialog",
        lambda page, title, target_url="": {
            "ok": True,
            "matched_title": title,
            "target_url": target_url,
            "action": "delete_option_clicked",
        },
    )

    def fake_inspect_delete_dialog(page):  # noqa: ARG001
        inspect_calls["count"] += 1
        if inspect_calls["count"] == 1:
            return {
                "dialog_type": "delete_confirm",
                "dialog_text": "删除后用户将无法访问此页面，确定删除？",
                "buttons": [{"text": "确认", "visible": True, "disabled": False}],
            }
        return {"dialog_type": "unknown_dialog", "dialog_text": "扫码验证"}

    monkeypatch.setattr(history, "_inspect_delete_publish_record_dialog", fake_inspect_delete_dialog)
    monkeypatch.setattr(
        history,
        "_inspect_delete_verification_dialog",
        lambda page: {
            "dialog_type": "scan_verification",
            "dialog_text": "扫码验证 管理员微信号与运营者微信号可直接扫码验证",
            "has_qrcode": True,
            "qrcode_src_redacted": "[redacted-qrcode]",
            "requires_human_scan": True,
        },
    )
    monkeypatch.setattr(history, "_click_delete_confirm_button", lambda page: {"clicked": True, "text": "确认"})

    result = history.delete_publish_record(None, title="目标文章", confirmed=True)

    assert result.status == "skipped"
    assert result.state["requires_human_scan"] is True
    assert result.state["verification_dialog"]["has_qrcode"] is True
    assert result.state["verification_dialog"]["qrcode_src_redacted"] == "[redacted-qrcode]"
    assert result.state["deleted"] is False


def test_delete_publish_record_unknown_dialog_fails_without_confirm(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    confirm_clicks = []
    monkeypatch.setattr(
        history,
        "_open_delete_publish_record_dialog",
        lambda page, title, target_url="": {
            "ok": True,
            "matched_title": title,
            "target_url": target_url,
            "action": "delete_option_clicked",
        },
    )
    monkeypatch.setattr(
        history,
        "_inspect_delete_publish_record_dialog",
        lambda page: {
            "dialog_type": "unknown_dialog",
            "dialog_text": "未知弹窗",
            "buttons": [{"text": "确认", "visible": True, "disabled": False}],
        },
    )
    monkeypatch.setattr(history, "_click_delete_confirm_button", lambda page: confirm_clicks.append(True))

    result = history.delete_publish_record(None, title="目标文章", confirmed=True)

    assert result.status == "failed"
    assert result.state["deleted"] is False
    assert result.state["delete_dialog"]["dialog_type"] == "unknown_dialog"
    assert confirm_clicks == []


def test_delete_publish_record_empty_title_fails():
    from agent_news.operations.wechat import history

    result = history.delete_publish_record(None, title="")

    assert result.status == "failed"
    assert "title" in result.message


def test_delete_publish_record_passes_target_url_to_locator(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    observed = {}

    def fake_open_delete_dialog(page, title, target_url=""):  # noqa: ARG001
        observed["target_url"] = target_url
        return {
            "ok": True,
            "matched_title": title,
            "href": target_url,
            "action": "delete_option_clicked",
        }

    monkeypatch.setattr(history, "_open_delete_publish_record_dialog", fake_open_delete_dialog)
    monkeypatch.setattr(
        history,
        "_inspect_delete_publish_record_dialog",
        lambda page: {
            "dialog_type": "delete_confirm",
            "dialog_text": "删除后用户将无法访问此页面，确定删除？",
            "buttons": [{"text": "确认", "visible": True, "disabled": False}],
        },
    )

    result = history.delete_publish_record(
        None,
        title="目标文章",
        url="https://mp.weixin.qq.com/s/pCKCucyFSo1nif3p2KPnoQ",
    )

    assert result.status == "skipped"
    assert observed["target_url"] == "https://mp.weixin.qq.com/s/pCKCucyFSo1nif3p2KPnoQ"


@pytest.mark.parametrize(
    ("func_name", "option_text"),
    [
        ("pin_publish_record", "置顶"),
        ("set_publish_record_private", "仅自己可见"),
        ("close_publish_record_recommendation", "关闭推荐"),
        ("copy_publish_record_link", "复制链接"),
        ("change_publish_record_collection", "修改合集"),
        ("change_publish_record_claim_source", "声明创作来源"),
    ],
)
def test_publish_record_menu_actions_click_exact_option(monkeypatch, func_name, option_text):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    observed = {}

    def fake_click_menu(page, title, clicked_option, target_url=""):  # noqa: ARG001
        observed["title"] = title
        observed["option_text"] = clicked_option
        observed["target_url"] = target_url
        return {
            "ok": True,
            "matched_title": title,
            "href": target_url,
            "action": "menu_option_clicked",
            "option_text": clicked_option,
        }

    monkeypatch.setattr(history, "_click_publish_record_menu_option", fake_click_menu)
    monkeypatch.setattr(
        history,
        "_inspect_publish_record_action_dialog",
        lambda page: {"dialog_type": "none", "dialog_found": False, "buttons": [], "requires_confirmation": False},
    )

    result = getattr(history, func_name)(
        None,
        title="目标文章",
        url="https://mp.weixin.qq.com/s/pCKCucyFSo1nif3p2KPnoQ",
    )

    assert result.status == "ok"
    assert observed == {
        "title": "目标文章",
        "option_text": option_text,
        "target_url": "https://mp.weixin.qq.com/s/pCKCucyFSo1nif3p2KPnoQ",
    }
    assert result.state["option_text"] == option_text


def test_publish_record_menu_action_requires_confirm_by_default(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    confirm_clicks = []
    monkeypatch.setattr(
        history,
        "_click_publish_record_menu_option",
        lambda page, title, option_text, target_url="": {
            "ok": True,
            "matched_title": title,
            "href": target_url,
            "action": "menu_option_clicked",
            "option_text": option_text,
        },
    )
    monkeypatch.setattr(
        history,
        "_inspect_publish_record_action_dialog",
        lambda page: {
            "dialog_type": "confirm",
            "dialog_found": True,
            "dialog_text": "确定将此内容设为仅自己可见？",
            "buttons": [{"text": "确定", "visible": True, "disabled": False}],
            "requires_confirmation": True,
        },
    )
    monkeypatch.setattr(
        history,
        "_click_publish_record_action_confirm_button",
        lambda page: confirm_clicks.append("确定") or {"clicked": True, "text": "确定"},
    )

    result = history.set_publish_record_private(None, title="目标文章")

    assert result.status == "skipped"
    assert result.state["requires_confirmation"] is True
    assert result.state["changed"] is False
    assert confirm_clicks == []


def test_publish_record_menu_action_confirmed_clicks_confirm(monkeypatch):
    from agent_news.operations.wechat import history

    _patch_delete_publish_record_nav(monkeypatch)
    confirm_clicks = []
    monkeypatch.setattr(
        history,
        "_click_publish_record_menu_option",
        lambda page, title, option_text, target_url="": {
            "ok": True,
            "matched_title": title,
            "href": target_url,
            "action": "menu_option_clicked",
            "option_text": option_text,
        },
    )
    monkeypatch.setattr(
        history,
        "_inspect_publish_record_action_dialog",
        lambda page: {
            "dialog_type": "confirm",
            "dialog_found": True,
            "dialog_text": "确定关闭推荐？",
            "buttons": [{"text": "确定", "visible": True, "disabled": False}],
            "requires_confirmation": True,
        },
    )
    monkeypatch.setattr(
        history,
        "_click_publish_record_action_confirm_button",
        lambda page: confirm_clicks.append("确定") or {"clicked": True, "text": "确定"},
    )

    result = history.close_publish_record_recommendation(None, title="目标文章", confirmed=True)

    assert result.status == "ok"
    assert result.state["changed"] is True
    assert result.state["button"]["text"] == "确定"
    assert confirm_clicks == ["确定"]


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
