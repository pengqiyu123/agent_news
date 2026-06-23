"""v0.1 closure tests: pending_confirmation + publish-tasks endpoint + no-fallback.

These cover the 3 gaps flagged in the v0.1 acceptance review:
1. SAVED cannot jump directly to PUBLISHED (must go through pending_confirmation).
2. GET /api/publish-tasks returns the audit trail.
3. wechat.* ops do NOT local-fallback when the server is down.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app
    return TestClient(app)


# ── pending_confirmation gate ───────────────────────────────────────────────
def test_saved_cannot_jump_to_published(client):
    """SAVED → PUBLISHED must be REJECTED (422). Must go through pending_confirmation."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="pending_confirmation test")
    wf = repo.create_workflow(art.id)
    # drive to SAVED through legal transitions
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)
    repo.transition_workflow(wf.id, WorkflowState.CONTENT_FILLED)
    repo.transition_workflow(wf.id, WorkflowState.SAVED)

    # SAVED → PUBLISHED should be illegal now
    resp = client.post(f"/api/workflows/{wf.id}/transition", json={"target": "published"})
    assert resp.status_code == 422


def test_saved_can_go_to_pending_confirmation(client):
    """SAVED → pending_confirmation is legal."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="pending_confirmation legal")
    wf = repo.create_workflow(art.id)
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)
    repo.transition_workflow(wf.id, WorkflowState.SAVED)

    resp = client.post(f"/api/workflows/{wf.id}/transition", json={"target": "pending_confirmation"})
    assert resp.status_code == 200
    assert resp.json()["item"]["state"] == "pending_confirmation"


def test_pending_confirmation_to_published_legal(client):
    """pending_confirmation → published is the only path to published."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="pending to published")
    wf = repo.create_workflow(art.id)
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)
    repo.transition_workflow(wf.id, WorkflowState.SAVED)
    repo.transition_workflow(wf.id, WorkflowState.PENDING_CONFIRMATION)

    resp = client.post(f"/api/workflows/{wf.id}/transition", json={"target": "published"})
    assert resp.status_code == 200
    assert resp.json()["item"]["state"] == "published"
    assert resp.json()["item"]["finished_at"]  # terminal


def test_pending_confirmation_in_allowed_transitions_endpoint(client):
    """The state graph endpoint must list pending_confirmation."""
    resp = client.get("/api/workflows/states/allowed")
    graph = resp.json()
    assert "pending_confirmation" in graph["saved"]
    assert "published" in graph["pending_confirmation"]
    assert "published" not in graph["saved"]  # the whole point


# ── GET /api/publish-tasks ──────────────────────────────────────────────────
def test_publish_tasks_endpoint_returns_list(client):
    """GET /api/publish-tasks returns a JSON list with total."""
    # Trigger an audit row first.
    client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})

    resp = client.get("/api/publish-tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] >= 1
    # Each item should have the audit fields.
    item = body["items"][0]
    assert "operation_name" in item
    assert "status" in item


def test_publish_tasks_single_by_id(client):
    """GET /api/publish-tasks/{id} returns one record."""
    # Create an audit row.
    client.post("/api/operations/radar.seed_defaults/execute", json={"params": {}})
    listing = client.get("/api/publish-tasks").json()
    task_id = listing["items"][0]["id"]

    resp = client.get(f"/api/publish-tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["item"]["id"] == task_id


def test_publish_tasks_404_for_missing(client):
    resp = client.get("/api/publish-tasks/does-not-exist")
    assert resp.status_code == 404


# ── wechat.* no local fallback ──────────────────────────────────────────────
def test_wechat_op_rejects_local_fallback():
    """When server is down and auto-start fails, wechat ops return explicit
    failure — they must NOT run locally (would start a competing browser)."""
    from agent_news import cli

    with patch.object(cli, "_server_is_up", return_value=False), \
         patch.object(cli, "_auto_start_server", return_value=False):
        result = cli._exec("wechat.session", {})
    assert result["status"] == "failed"
    assert "服务" in result["message"] or "start.bat" in result["message"]


def test_radar_op_still_local_fallbacks():
    """Radar/data ops SHOULD local-fallback (no browser risk)."""
    from agent_news import cli

    with patch.object(cli, "_server_is_up", return_value=False), \
         patch.object(cli, "_auto_start_server", return_value=False):
        result = cli._exec("radar.seed_defaults", {})
    # radar.seed_defaults is idempotent — returns ok or skipped, not the
    # "服务未运行" failure message.
    assert result.get("status") in ("ok", "skipped"), result
