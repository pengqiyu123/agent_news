"""Publish-precheck operations tests — registration + skip behavior.

Skip behavior (enabled=False / name="" -> skip) is the core contract.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app

    return TestClient(app)


# ── Registration ────────────────────────────────────────────────────────────
def test_publish_settings_operations_registered(client):
    resp = client.get("/api/operations")
    names = {op["name"] for op in resp.json()["items"]}
    expected = {
        "wechat.set_original",
        "wechat.set_original_author",
        "wechat.set_reward",
        "wechat.set_collection",
        "wechat.set_claim_source",
        "wechat.generate_ai_cover",
        "wechat.inspect_cover_picker",
        "wechat.list_collections",
        "wechat.list_claim_sources",
    }
    missing = expected - names
    assert not missing, f"missing: {missing}"


def test_all_in_publish_settings_category(client):
    """Publish-settings operations must declare category=publish_settings."""
    resp = client.get("/api/operations")
    ops_by_name = {op["name"]: op for op in resp.json()["items"]}
    ps_names = [
        "wechat.set_original", "wechat.set_original_author", "wechat.set_reward",
        "wechat.set_collection", "wechat.set_claim_source",
        "wechat.generate_ai_cover", "wechat.inspect_cover_picker", "wechat.list_collections", "wechat.list_claim_sources",
    ]
    for name in ps_names:
        assert name in ops_by_name, f"{name} not registered"
        assert ops_by_name[name]["category"] == "publish_settings", f"{name} category wrong"


# ── Skip behavior (no browser touched) ──────────────────────────────────────
def test_set_original_disabled_skips(client):
    resp = client.post("/api/operations/wechat.set_original/execute", json={"params": {"enabled": False}})
    assert resp.json()["item"]["status"] == "skipped"
    assert resp.json()["item"]["state"]["original"] is False


class _OriginalAuthorPage:
    url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

    def __init__(self):
        self.dialog_open = False
        self.author = "AI新闻工作室"
        self.pending_author = ""

    def wait_for_timeout(self, timeout):  # noqa: ARG002
        return None

    def evaluate(self, script, arg=None):  # noqa: ARG002
        if "summary_author" in script and "fast_reprint_text" in script:
            return {
                "dialog_open": self.dialog_open,
                "summary_author": self.author,
                "preview_author": self.author,
                "input_value": self.pending_author if self.dialog_open else "",
                "input_found": self.dialog_open,
                "error_text": "",
                "fast_reprint_text": "已开启",
            }
        if "original_entry_not_found" in script:
            self.dialog_open = True
            return {"ok": True, "selector": "#js_original_open .js_edit_ori", "text": "原创"}
        if "author_input_not_found" in script:
            self.pending_author = (arg or {}).get("author", "")
            counter_map = {
                "Agent news": "5/8",
                "ABCDEFGHIJKLMNOPQ": "8.5/8",
            }
            return {
                "ok": True,
                "value": self.pending_author,
                "counter_text": counter_map.get(self.pending_author, "6/8"),
            }
        if "confirm_button_not_found" in script:
            self.author = self.pending_author
            self.dialog_open = False
            return {"ok": True, "button_text": "确定", "agreement_checked": True}
        return {}


def test_set_original_author_rejects_overlength_without_truncating():
    from agent_news.operations.wechat import publish_settings

    result = publish_settings._set_original_author_on_page(_OriginalAuthorPage(), "ABCDEFGHIJKLMNOPQ")

    assert result.status == "failed"
    assert result.state["author"] == "ABCDEFGHIJKLMNOPQ"
    assert result.state["counter_text"] == "8.5/8"
    assert "计数器校验" in result.message


def test_set_original_author_updates_and_reads_back():
    from agent_news.operations.wechat import publish_settings

    page = _OriginalAuthorPage()
    result = publish_settings._set_original_author_on_page(page, "Agent news")

    assert result.status == "ok"
    assert result.state["author"] == "Agent news"
    assert result.state["readback"] == "Agent news"
    assert result.state["counter_text"] == "5/8"
    assert page.author == "Agent news"


def test_set_reward_disabled_skips(client):
    resp = client.post("/api/operations/wechat.set_reward/execute", json={"params": {"enabled": False}})
    assert resp.json()["item"]["status"] == "skipped"
    assert resp.json()["item"]["state"]["reward"] is False


def test_set_collection_empty_name_skips(client):
    resp = client.post("/api/operations/wechat.set_collection/execute", json={"params": {"name": ""}})
    assert resp.json()["item"]["status"] == "skipped"


def test_set_claim_source_empty_name_skips(client):
    resp = client.post("/api/operations/wechat.set_claim_source/execute", json={"params": {"name": ""}})
    assert resp.json()["item"]["status"] == "skipped"


def test_generate_ai_cover_empty_prompt_skips(client):
    resp = client.post("/api/operations/wechat.generate_ai_cover/execute", json={"params": {"prompt": ""}})
    assert resp.json()["item"]["status"] == "skipped"


# ── The full precheck chain as a batch (all skip when disabled) ─────────────
def test_precheck_batch_all_skips_when_disabled(client):
    """Agent runs all 5 precheck steps; with all disabled/empty, every step
    skips without touching the browser. The 'no precheck' path."""
    resp = client.post("/api/operations/batch", json={
        "steps": [
            {"op": "wechat.set_original", "params": {"enabled": False}},
            {"op": "wechat.set_reward", "params": {"enabled": False}},
            {"op": "wechat.set_collection", "params": {"name": ""}},
            {"op": "wechat.set_claim_source", "params": {"name": ""}},
            {"op": "wechat.generate_ai_cover", "params": {"prompt": ""}},
        ],
        "on_error": "stop",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_ok"]
    assert len(body["results"]) == 5
    for r in body["results"]:
        assert r["result"]["status"] == "skipped"
