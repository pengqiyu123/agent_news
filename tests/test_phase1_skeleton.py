"""Phase 1 verification: skeleton, state layer, registry, and workflow FSM.

These tests confirm the foundation is sound before phase 2 adds the browser
layer. No browser, no network — pure in-process.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app

    return TestClient(app)


# ── Health ──────────────────────────────────────────────────────────────────
def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


# ── Article CRUD round-trip via SQLite ──────────────────────────────────────
def test_article_create_get_list_delete(client):
    # create
    resp = client.post(
        "/api/articles",
        json={"title": "测试文章", "digest": "摘要", "body_markdown": "# 正文", "author": "AI"},
    )
    assert resp.status_code == 200
    article = resp.json()["item"]
    article_id = article["id"]
    assert article["title"] == "测试文章"
    assert article["stage"] == "draft"

    # get
    resp = client.get(f"/api/articles/{article_id}")
    assert resp.status_code == 200
    assert resp.json()["item"]["id"] == article_id

    # list
    resp = client.get("/api/articles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(a["id"] == article_id for a in body["items"])

    # update
    resp = client.put(f"/api/articles/{article_id}", json={"title": "改后的标题"})
    assert resp.status_code == 200
    assert resp.json()["item"]["title"] == "改后的标题"

    # delete
    resp = client.delete(f"/api/articles/{article_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # get after delete → 404
    resp = client.get(f"/api/articles/{article_id}")
    assert resp.status_code == 404


def test_get_missing_article_404(client):
    resp = client.get("/api/articles/does-not-exist")
    assert resp.status_code == 404


# ── Workflow state machine ──────────────────────────────────────────────────
def test_workflow_legal_transition(client):
    # need an article to attach the workflow to
    art = client.post("/api/articles", json={"title": "工作流测试"}).json()["item"]
    wf = client.post(f"/api/workflows?article_id={art['id']}").json()["item"]
    wf_id = wf["id"]
    assert wf["state"] == "init"

    # legal: init -> editor_open
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "editor_open"})
    assert resp.status_code == 200
    assert resp.json()["item"]["state"] == "editor_open"


def test_workflow_illegal_transition_rejected(client):
    art = client.post("/api/articles", json={"title": "非法转换测试"}).json()["item"]
    wf = client.post(f"/api/workflows?article_id={art['id']}").json()["item"]
    wf_id = wf["id"]

    # illegal: init -> published (skips the whole chain)
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "published"})
    assert resp.status_code == 422
    assert "Illegal transition" in resp.json()["detail"]


def test_workflow_terminal_state_locked(client):
    art = client.post("/api/articles", json={"title": "终态锁定测试"}).json()["item"]
    wf = client.post(f"/api/workflows?article_id={art['id']}").json()["item"]
    wf_id = wf["id"]

    # drive to abandoned (terminal) directly via repo
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    repo.transition_workflow(wf_id, WorkflowState.ABANDONED)

    # any further move from terminal must be rejected
    resp = client.post(f"/api/workflows/{wf_id}/transition", json={"target": "editor_open"})
    assert resp.status_code == 422


def test_allowed_transitions_endpoint(client):
    resp = client.get("/api/workflows/states/allowed")
    assert resp.status_code == 200
    graph = resp.json()
    assert "editor_open" in graph["init"]
    assert "published" not in graph["init"]  # init cannot jump to published
    assert graph["published"] == []  # terminal


# ── Operation registry ─────────────────────────────────────────────────────
def test_registry_lists_operations(client):
    """Registry should list all registered operations (radar ops registered on import)."""
    resp = client.get("/api/operations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    names = {op["name"] for op in body["items"]}
    assert "radar.build_events" in names


def test_registry_execute_unknown_returns_failed(client):
    # unknown op → 404 at the HTTP layer (registry.has check in route)
    resp = client.post("/api/operations/wechat.bogus/execute", json={"params": {}})
    assert resp.status_code == 404


def test_registry_batch_empty_steps_ok(client):
    resp = client.post("/api/operations/batch", json={"steps": [], "on_error": "stop"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_ok"] is True
    assert body["results"] == []
