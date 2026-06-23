"""Workflow session model — centralized state machine.

This directly fixes the old project's flaw: workflow transitions were scattered
string literals across one 300-line method (briefs_mixin.py:687-996 in
auto-news-studio), easy to leave stuck in "running".

Here, legal transitions are declared in one place (ALLOWED_TRANSITIONS) and
enforced by transition_to(). An illegal transition is rejected, never silently
applied.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class WorkflowState(str, Enum):
    """States an article-publishing workflow can be in.

    Order roughly follows the publish chain but AI may skip steps, so transitions
    are not strictly linear — see ALLOWED_TRANSITIONS.
    """

    INIT = "init"
    EDITOR_OPEN = "editor_open"
    CONTENT_FILLED = "content_filled"
    SETTINGS_APPLIED = "settings_applied"
    COVER_READY = "cover_ready"
    SAVED = "saved"
    PENDING_CONFIRMATION = "pending_confirmation"  # QR code reached, awaiting human scan
    PUBLISHED = "published"
    FAILED = "failed"
    ABANDONED = "abandoned"


# Flexible atomic-orchestration state graph.
# The agent can compose operations in ANY order — this is NOT a fixed pipeline.
# Two legal terminal paths:
#   Save path:  ... -> saved (done, no publish)
#   Publish path: ... -> pending_confirmation -> published (after human scan + check)
#
# saved is NOT a required step before publish — the agent can go directly from
# any content/settings/cover state to pending_confirmation (direct publish).
ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.INIT: {
        WorkflowState.EDITOR_OPEN,
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.EDITOR_OPEN: {
        WorkflowState.CONTENT_FILLED,
        WorkflowState.SETTINGS_APPLIED,
        WorkflowState.COVER_READY,
        WorkflowState.SAVED,
        WorkflowState.PENDING_CONFIRMATION,  # direct publish without saving
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.CONTENT_FILLED: {
        WorkflowState.SETTINGS_APPLIED,
        WorkflowState.COVER_READY,
        WorkflowState.SAVED,
        WorkflowState.PENDING_CONFIRMATION,  # direct publish
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.SETTINGS_APPLIED: {
        WorkflowState.COVER_READY,
        WorkflowState.SAVED,
        WorkflowState.CONTENT_FILLED,     # back-fill if needed
        WorkflowState.PENDING_CONFIRMATION,  # direct publish
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.COVER_READY: {
        WorkflowState.SAVED,
        WorkflowState.SETTINGS_APPLIED,
        WorkflowState.CONTENT_FILLED,     # back-fill if needed
        WorkflowState.PENDING_CONFIRMATION,  # direct publish
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.SAVED: {
        # SAVED cannot jump directly to PUBLISHED — must go through
        # PENDING_CONFIRMATION (QR code reached) first.
        WorkflowState.PENDING_CONFIRMATION,
        WorkflowState.COVER_READY,
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.PENDING_CONFIRMATION: {
        # Reached the QR code → human scans → platform confirms → PUBLISHED.
        # Only check_publish_done (real platform confirmation) may advance here.
        WorkflowState.PUBLISHED,
        WorkflowState.FAILED,
        WorkflowState.ABANDONED,
    },
    WorkflowState.PUBLISHED: set(),        # terminal
    WorkflowState.FAILED: set(),           # terminal
    WorkflowState.ABANDONED: set(),        # terminal
}

TERMINAL_STATES = {
    WorkflowState.PUBLISHED,
    WorkflowState.FAILED,
    WorkflowState.ABANDONED,
}


class IllegalTransitionError(ValueError):
    """Raised when a workflow tries an undeclared state transition."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowSession(BaseModel):
    """A single publish workflow's lifecycle record."""

    id: str
    article_id: str
    state: WorkflowState = WorkflowState.INIT
    # Which publish-precheck settings have been applied (auditable, AI-readable).
    settings_applied: dict[str, bool] = Field(default_factory=dict)
    # e.g. {"original": true, "reward": false, "collection": "AI新闻", "claim_source": "..."}
    collection_name: str | None = None
    claim_source_name: str | None = None
    cover_prompt: str | None = None
    last_error: str | None = None
    started_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    finished_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def transition_to(self, target: WorkflowState) -> None:
        """Validate and apply a state transition.

        Raises IllegalTransitionError if target is not in ALLOWED_TRANSITIONS
        for the current state. This is the single chokepoint that replaces the
        old project's scattered string-literal transitions.
        """
        if self.state in TERMINAL_STATES:
            raise IllegalTransitionError(
                f"Workflow {self.id} is terminal ({self.state.value}); cannot move to {target.value}"
            )
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if target not in allowed:
            raise IllegalTransitionError(
                f"Illegal transition {self.state.value} → {target.value} "
                f"for workflow {self.id}. Allowed: {sorted(s.value for s in allowed)}"
            )
        self.state = target
        self.updated_at = _utcnow()
        if target in TERMINAL_STATES:
            self.finished_at = self.updated_at


class WorkflowResponse(BaseModel):
    item: WorkflowSession


class WorkflowListResponse(BaseModel):
    items: list[WorkflowSession]
    total: int


class WorkflowTransitionRequest(BaseModel):
    """Body of POST /api/workflows/{id}/transition."""

    target: WorkflowState
