"""Operations routes — the atomic-operation registry surface.

Every operation execution is audited into publish_tasks. When a workflow_session_id
is provided, successful operations automatically advance the workflow state
based on a mapping from operation name → target state.

The advance is best-effort: if the transition is illegal for the current state,
it's silently skipped (the workflow stays where it is). This lets the AI call
operations in any order without the state machine rejecting valid-but-out-of-
sequence steps.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import (
    BatchExecuteRequest,
    BatchExecuteResponse,
    OperationExecuteRequest,
    OperationExecuteResponse,
)

router = APIRouter(tags=["operations"])


# ── Operation → workflow state advancement rules ────────────────────────────
# Maps a successful operation name to the workflow state it should advance to.
# The advance is only applied if the transition is legal for the current state
# (illegal transitions are silently skipped, not errors).
#
# Principles:
# - Only status=="ok" advances; "skipped" does NOT (a skipped step is a no-op).
# - open_dashboard / check_login / session have NO mapping (they don't change
#   the editorial state — opening the dashboard is not entering the editor).
# - publish_to_qrcode / wait_qrcode → pending_confirmation (NEVER published).
# - Only check_publish_done (real platform confirmation) → published.
_OPERATION_STATE_MAP: dict[str, str] = {
    "wechat.open_new_editor": "editor_open",
    "wechat.open_existing_draft": "editor_open",
    "wechat.fill_editor_required": "content_filled",
    "wechat.fill_title": "content_filled",
    "wechat.fill_author": "content_filled",
    "wechat.fill_digest": "content_filled",
    "wechat.paste_body": "content_filled",
    "wechat.set_original": "settings_applied",
    "wechat.set_reward": "settings_applied",
    "wechat.set_collection": "settings_applied",
    "wechat.set_claim_source": "settings_applied",
    "wechat.generate_ai_cover": "cover_ready",
    "wechat.save_as_draft": "saved",
    "wechat.save_current_editor_as_draft": "saved",
    # QR code reached → pending_confirmation, NOT published.
    "wechat.publish_to_qrcode": "pending_confirmation",
    "wechat.publish_current_editor_to_qrcode": "pending_confirmation",
    "wechat.publish_existing_draft_to_qrcode": "pending_confirmation",
    "wechat.wait_qrcode": "pending_confirmation",
    # Only check_publish_done (real platform confirmation) → published.
    "wechat.check_publish_done": "published",
}


def _audit(operation_name: str, result, *, workflow_session_id=None, params=None) -> None:
    """Record an operation result into publish_tasks for audit. Never raises."""
    try:
        from ..db import get_repository
        get_repository().log_operation_result(
            operation_name, result,
            workflow_session_id=workflow_session_id,
            params=params,
        )
    except Exception:
        pass  # audit must never break the response


def _advance_workflow(operation_name: str, result, workflow_session_id: str | None) -> None:
    """Best-effort: advance a workflow's state based on a successful operation.

    Only advances if: (a) workflow_session_id is provided, (b) the operation
    succeeded with status=="ok" (skipped does NOT advance — it's a no-op),
    (c) the operation has a state mapping, and (d) the transition is legal
    for the workflow's current state. Any failure is silently ignored.
    """
    if not workflow_session_id:
        return
    if result.status != "ok":
        return  # skipped / failed → no state change
    target_state = _OPERATION_STATE_MAP.get(operation_name)
    if not target_state:
        return
    try:
        from ..db import get_repository
        from ..models import WorkflowState
        repo = get_repository()
        repo.transition_workflow(workflow_session_id, WorkflowState(target_state))
    except Exception:
        pass  # illegal transition / unknown workflow / db error → skip silently


@router.get("/api/operations")
def list_operations() -> dict:
    """List all registered atomic operations."""
    try:
        from ..operations.registry import OPERATION_REGISTRY

        specs = OPERATION_REGISTRY.list_specs()
    except Exception:
        specs = []
    return {"items": [s.model_dump() for s in specs], "total": len(specs)}


@router.post("/api/operations/batch", response_model=BatchExecuteResponse)
def execute_batch(req: BatchExecuteRequest) -> BatchExecuteResponse:
    """Execute multiple operations in sequence with an on-error policy."""
    try:
        from ..operations.registry import OPERATION_REGISTRY
    except ImportError as e:
        raise HTTPException(status_code=501, detail=f"Operations registry not ready: {e}") from e

    response = OPERATION_REGISTRY.execute_batch(req)
    # Audit each step + advance workflow for successful steps.
    for step_result in response.results:
        _audit(
            step_result.op,
            step_result.result,
            workflow_session_id=req.workflow_session_id,
            params=step_result.params,
        )
        _advance_workflow(step_result.op, step_result.result, req.workflow_session_id)
    return response


@router.post("/api/operations/{name}/execute", response_model=OperationExecuteResponse)
def execute_operation(name: str, req: OperationExecuteRequest) -> OperationExecuteResponse:
    """Execute a single atomic operation by name.

    If workflow_session_id is provided, a successful operation automatically
    advances the workflow state (best-effort, illegal transitions skipped).
    """
    try:
        from ..operations.registry import OPERATION_REGISTRY
    except ImportError as e:
        raise HTTPException(status_code=501, detail=f"Operations registry not ready: {e}") from e

    if not OPERATION_REGISTRY.has(name):
        raise HTTPException(status_code=404, detail=f"Operation '{name}' not registered")
    result = OPERATION_REGISTRY.execute(name, **req.params)
    _audit(name, result, workflow_session_id=req.workflow_session_id, params=req.params)
    _advance_workflow(name, result, req.workflow_session_id)
    return OperationExecuteResponse(item=result)
