"""Audit routes — expose publish_tasks (operation audit trail) for agent queries.

The agent can GET /api/publish-tasks to review what operations ran, their
status, and error messages — useful for debugging a failed publish chain.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import get_repository
from ..models.publish import PublishTask, PublishTaskListResponse

router = APIRouter(tags=["audit"])


@router.get("/api/publish-tasks", response_model=PublishTaskListResponse)
def list_publish_tasks(
    limit: int = Query(50, ge=1, le=200),
) -> PublishTaskListResponse:
    """List recent operation audit records (newest first)."""
    repo = get_repository()
    items, total = repo.list_publish_tasks(limit=limit)
    return PublishTaskListResponse(items=items, total=total)


@router.get("/api/publish-tasks/{task_id}", response_model=dict)
def get_publish_task(task_id: str) -> dict:
    """Get a single audit record by id."""
    repo = get_repository()
    task = repo.get_publish_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Publish task '{task_id}' not found")
    return {"item": task}
