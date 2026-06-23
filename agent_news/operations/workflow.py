"""Workflow observation atomic operations."""

from __future__ import annotations

from ..db import get_repository
from ..models.operation import OperationResult
from ..models.workflow import ALLOWED_TRANSITIONS
from .base import operation


@operation(
    name="workflow.status",
    category="workflow",
    description="只读：查看工作流当前状态和合法下一步，不推进状态。",
    params={"workflow_session_id": "工作流 ID"},
)
def workflow_status(ctx, workflow_session_id: str) -> OperationResult:
    repo = get_repository()
    workflow = repo.get_workflow(workflow_session_id)
    if workflow is None:
        return OperationResult.failure(message=f"workflow '{workflow_session_id}' not found")
    allowed = sorted(state.value for state in ALLOWED_TRANSITIONS.get(workflow.state, set()))
    return OperationResult.success(
        message=f"workflow {workflow.id} is {workflow.state.value}",
        workflow=workflow.model_dump(),
        workflow_session_id=workflow.id,
        article_id=workflow.article_id,
        state=workflow.state.value,
        allowed_next_states=allowed,
        last_error=workflow.last_error,
        settings_applied=workflow.settings_applied,
    )

