"""Audit + session-state + graceful-failure tests.

Covers the gaps the user flagged after the cleanup:
- Operation execution writes to publish_tasks (audit trail)
- wechat.session returns current_url / is_editor_page / last_error
- Operations fail gracefully (return failed, never 500) when the browser is
  unavailable — mock BROWSER_MANAGER.with_session (the module-level singleton)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app
    return TestClient(app)


# ── Audit: operation execution writes to publish_tasks ─────────────────────
def test_single_op_execution_is_audited(client):
    """Executing an operation via the API records a publish_tasks row."""
    from agent_news.db.intel_repository import get_intel_repository
    repo = get_intel_repository()
    # Snapshot count before.
    _, before = repo.list_publish_tasks()

    # Execute a cheap operation (radar.seed_defaults is idempotent).
    resp = client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})
    assert resp.status_code == 200

    _, after = repo.list_publish_tasks()
    assert after >= before + 1, "audit row was not written"


def test_batch_execution_audits_each_step(client):
    """Each step in a batch writes its own audit row."""
    from agent_news.db.intel_repository import get_intel_repository
    repo = get_intel_repository()
    _, before = repo.list_publish_tasks()

    resp = client.post("/api/operations/batch", json={
        "steps": [
            {"op": "radar.seed_defaults", "params": {}},
            {"op": "radar.seed_defaults", "params": {}},
        ],
        "on_error": "continue",
    })
    assert resp.status_code == 200

    _, after = repo.list_publish_tasks()
    assert after >= before + 2, "batch steps were not each audited"


def test_audit_never_breaks_response(client):
    """Even if the audit write fails, the operation response is intact."""
    with patch("agent_news.db.repository.get_repository") as mock_repo_fn:
        mock_repo_fn.return_value.log_operation_result.side_effect = RuntimeError("db locked")
        resp = client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})
    assert resp.status_code == 200
    assert resp.json()["item"]["ok"]


# ── Session state fields ────────────────────────────────────────────────────
def test_session_returns_extended_state_fields(client):
    """wechat.session must return current_url, is_editor_page, last_error
    (in addition to manager_alive/busy/resident_page/last_reset_reason)."""
    from agent_news.browser import BROWSER_MANAGER

    # Mock observe_page so we don't need a real browser.
    with patch.object(BROWSER_MANAGER, "observe_page", return_value={
        "current_url": "https://mp.weixin.qq.com/cgi-bin/home",
        "is_editor_page": False,
        "page_count": 1,
        "page_urls": ["https://mp.weixin.qq.com/cgi-bin/home"],
    }), patch.object(BROWSER_MANAGER, "manager_state", return_value={
        "manager_alive": True, "busy": False,
        "resident_page": "home", "last_reset_reason": None, "last_error": None,
    }):
        resp = client.post("/api/operations/wechat.session/execute", json={"params": {}})
    assert resp.status_code == 200
    state = resp.json()["item"]["state"]
    assert "current_url" in state
    assert "is_editor_page" in state
    assert "page_count" in state
    assert "page_urls" in state
    assert "last_error" in state
    assert state["current_url"] == "https://mp.weixin.qq.com/cgi-bin/home"
    assert state["is_editor_page"] is False


# ── Graceful failure when browser unavailable ───────────────────────────────
def test_wechat_op_fails_gracefully_without_browser(client):
    """Mock with_session to raise; operation must return failed, not 500."""
    from agent_news.browser import BROWSER_MANAGER

    def _boom(*a, **k):
        raise RuntimeError("no browser available (mocked)")

    with patch.object(BROWSER_MANAGER, "with_session", _boom):
        resp = client.post("/api/operations/wechat.check_login/execute", json={"params": {}})
    assert resp.status_code == 200  # operation ran, returned a result
    item = resp.json()["item"]
    assert item["status"] == "failed"
    assert item["message"]  # non-empty explanation
