"""Workflow routes — lifecycle of publish workflow sessions.

The state-machine enforcement (ALLOWED_TRANSITIONS) lives in the model layer;
these routes are a thin HTTP surface over repository + WorkflowSession.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import get_repository
from ..models import (
    IllegalTransitionError,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowState,
    WorkflowTransitionRequest,
)

router = APIRouter(tags=["workflows"])


@router.post("/api/workflows", response_model=WorkflowResponse)
def create_workflow(article_id: str) -> WorkflowResponse:
    """Create a new publish workflow for the given article."""
    repo = get_repository()
    article = repo.get_article(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    wf = repo.create_workflow(article_id)
    return WorkflowResponse(item=wf)


@router.get("/api/workflows", response_model=WorkflowListResponse)
def list_workflows() -> WorkflowListResponse:
    repo = get_repository()
    items, total = repo.list_workflows()
    return WorkflowListResponse(items=items, total=total)


@router.get("/api/workflows/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(workflow_id: str) -> WorkflowResponse:
    repo = get_repository()
    wf = repo.get_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    return WorkflowResponse(item=wf)


@router.post("/api/workflows/{workflow_id}/transition", response_model=WorkflowResponse)
def transition_workflow(workflow_id: str, req: WorkflowTransitionRequest) -> WorkflowResponse:
    """Validate and apply a state transition.

    Returns 422 with the allowed set if the transition is illegal — never
    silently applies an invalid move.
    """
    repo = get_repository()
    try:
        wf = repo.transition_workflow(workflow_id, req.target)
    except IllegalTransitionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return WorkflowResponse(item=wf)


@router.get("/api/workflows/states/allowed")
def list_allowed_transitions() -> dict:
    """Introspection endpoint: show the legal state graph. Useful for AI clients.

    Declared before the {workflow_id} route so FastAPI doesn't try to treat
    'states' as a workflow id.
    """
    from ..models.workflow import ALLOWED_TRANSITIONS

    return {
        state.value: sorted(s.value for s in targets)
        for state, targets in ALLOWED_TRANSITIONS.items()
    }
