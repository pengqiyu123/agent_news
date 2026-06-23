"""Publish-precheck operations tests — registration + skip behavior.

Modeled on old project pattern: test pure logic without mocking the browser.
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
        "wechat.set_original", "wechat.set_reward",
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
