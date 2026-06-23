"""Atomic orchestration tests — flexible state machine + advancement rules.

Verifies the architecture principle: agent-news is an atomic operation system,
NOT a fixed pipeline. Two legal paths exist (save / direct-publish), and the
state machine allows flexible composition.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from agent_news.main import app
    return TestClient(app)


# ── Advancement rule: open_dashboard does NOT advance workflow ──────────────
def test_open_dashboard_does_not_advance_workflow(client):
    """open_dashboard is a navigation op, not an editorial state change.
    It must NOT push the workflow to editor_open."""
    from agent_news.routes.operations import _OPERATION_STATE_MAP
    assert "wechat.open_dashboard" not in _OPERATION_STATE_MAP


# ── Advancement rule: skipped does NOT advance workflow ─────────────────────
def test_skipped_operation_does_not_advance():
    """A skipped operation (e.g. set_original enabled=False) is a no-op —
    it must NOT advance the workflow state."""
    from agent_news.routes.operations import _advance_workflow
    from agent_news.models.operation import OperationResult

    # Create a workflow, drive to INIT.
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState
    repo = get_repository()
    art = repo.create_article(title="skipped test")
    wf = repo.create_workflow(art.id)
    assert wf.state == WorkflowState.INIT

    # Simulate a skipped set_original.
    skipped_result = OperationResult.skip(message="skipped")
    _advance_workflow("wechat.set_original", skipped_result, wf.id)

    after = repo.get_workflow(wf.id)
    assert after.state == WorkflowState.INIT  # unchanged


# ── Direct publish path: cover_ready -> pending_confirmation ────────────────
def test_direct_publish_cover_ready_to_pending(client):
    """Direct publish path: agent skips save_as_draft, goes straight from
    cover_ready to pending_confirmation (publish_to_qrcode)."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="direct publish")
    wf = repo.create_workflow(art.id)
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)
    repo.transition_workflow(wf.id, WorkflowState.CONTENT_FILLED)
    repo.transition_workflow(wf.id, WorkflowState.COVER_READY)

    # cover_ready → pending_confirmation must be legal (direct publish).
    resp = client.post(f"/api/workflows/{wf.id}/transition",
                       json={"target": "pending_confirmation"})
    assert resp.status_code == 200
    assert resp.json()["item"]["state"] == "pending_confirmation"


# ── Save path: cover_ready -> saved ──────────────────────────────────────────
def test_save_path_cover_ready_to_saved(client):
    """Save path: from cover_ready, agent can save_as_draft → saved."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="save path")
    wf = repo.create_workflow(art.id)
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)
    repo.transition_workflow(wf.id, WorkflowState.COVER_READY)

    resp = client.post(f"/api/workflows/{wf.id}/transition",
                       json={"target": "saved"})
    assert resp.status_code == 200
    assert resp.json()["item"]["state"] == "saved"


# ── saved -> published still forbidden ──────────────────────────────────────
def test_saved_to_published_still_forbidden(client):
    """saved cannot jump to published — must go through pending_confirmation."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="saved to published forbidden")
    wf = repo.create_workflow(art.id)
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)
    repo.transition_workflow(wf.id, WorkflowState.SAVED)

    resp = client.post(f"/api/workflows/{wf.id}/transition",
                       json={"target": "published"})
    assert resp.status_code == 422


# ── publish_to_qrcode only advances to pending_confirmation ─────────────────
def test_publish_to_qrcode_advances_to_pending_not_published():
    """The _OPERATION_STATE_MAP must map publish_to_qrcode to
    pending_confirmation, never to published."""
    from agent_news.routes.operations import _OPERATION_STATE_MAP
    assert _OPERATION_STATE_MAP.get("wechat.publish_to_qrcode") == "pending_confirmation"
    assert _OPERATION_STATE_MAP.get("wechat.publish_current_editor_to_qrcode") == "pending_confirmation"
    assert _OPERATION_STATE_MAP.get("wechat.publish_existing_draft_to_qrcode") == "pending_confirmation"
    assert _OPERATION_STATE_MAP.get("wechat.publish_to_qrcode") != "published"
    assert _OPERATION_STATE_MAP.get("wechat.publish_current_editor_to_qrcode") != "published"
    assert _OPERATION_STATE_MAP.get("wechat.publish_existing_draft_to_qrcode") != "published"


def test_save_intent_operation_advances_to_saved_not_pending():
    """Saving to draft is its own terminal intent; it should not imply publish."""
    from agent_news.routes.operations import _OPERATION_STATE_MAP
    assert _OPERATION_STATE_MAP.get("wechat.save_current_editor_as_draft") == "saved"
    assert _OPERATION_STATE_MAP.get("wechat.save_current_editor_as_draft") != "pending_confirmation"


def test_fill_editor_required_advances_to_content_filled():
    """The safe editor-fill entrypoint represents title + author + body."""
    from agent_news.routes.operations import _OPERATION_STATE_MAP

    assert _OPERATION_STATE_MAP.get("wechat.fill_editor_required") == "content_filled"


# ── Single-step op with workflow_session_id writes audit ────────────────────
def test_single_op_with_workflow_writes_audit(client):
    """POST /api/operations/{name}/execute with workflow_session_id writes
    a publish_tasks row linked to that workflow."""
    from agent_news.db import get_repository

    repo = get_repository()
    art = repo.create_article(title="audit linkage")
    wf = repo.create_workflow(art.id)
    _, before = repo.list_publish_tasks()

    resp = client.post("/api/operations/radar.seed_defaults/execute", json={
        "params": {},
        "workflow_session_id": wf.id,
    })
    assert resp.status_code == 200

    tasks, after = repo.list_publish_tasks()
    assert after >= before + 1
    # Check the latest task is linked to our workflow.
    linked = [t for t in tasks if t.workflow_session_id == wf.id]
    assert len(linked) >= 1


# ── Flexible: editor_open can go directly to pending_confirmation ───────────
def test_editor_open_direct_to_pending(client):
    """Extreme direct publish: agent opens editor and immediately publishes,
    skipping all fill/settings/cover/save steps."""
    from agent_news.db import get_repository
    from agent_news.models import WorkflowState

    repo = get_repository()
    art = repo.create_article(title="extreme direct")
    wf = repo.create_workflow(art.id)
    repo.transition_workflow(wf.id, WorkflowState.EDITOR_OPEN)

    resp = client.post(f"/api/workflows/{wf.id}/transition",
                       json={"target": "pending_confirmation"})
    assert resp.status_code == 200
