"""Operation models — the contract between the atomic-operation registry and callers.

Every atomic operation returns an OperationResult. This is the single normalized
shape that lets AI treat all steps uniformly: success, skip, failure are all
first-class outcomes, never exceptions that cascade across steps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


class OperationStatus(str, Enum):
    """Outcome of a single atomic operation."""

    OK = "ok"            # step succeeded
    SKIPPED = "skipped"  # caller asked to skip (e.g. enabled=False)
    FAILED = "failed"    # step ran but did not achieve its goal


class OperationResult(BaseModel):
    """Normalized return value of every registered atomic operation.

    Design: a failed step never raises. It returns status=failed so the caller
    (batch executor or AI) decides whether to stop, continue, or retry. This is
    the direct fix for the old project's "one step fails, whole pipeline dies".
    """

    status: OperationStatus
    message: str = ""
    # Free-form state snapshot — what this step observed or changed.
    # e.g. {"original_declared": true} or {"collections": ["AI新闻", ...]}
    state: dict[str, Any] = Field(default_factory=dict)
    # Screenshots / artifact paths produced by this step.
    artifacts: list[str] = Field(default_factory=list)
    # Human-readable step log lines for audit.
    step_logs: list[str] = Field(default_factory=list)
    # When this result was produced (UTC ISO).
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """True if the step did not fail (ok or skipped). Serialized into JSON."""
        return self.status != OperationStatus.FAILED

    @classmethod
    def success(cls, message: str = "", **state: Any) -> OperationResult:
        return cls(status=OperationStatus.OK, message=message, state=dict(state))

    @classmethod
    def skip(cls, message: str = "", **state: Any) -> OperationResult:
        return cls(status=OperationStatus.SKIPPED, message=message, state=dict(state))

    @classmethod
    def failure(cls, message: str = "", **state: Any) -> OperationResult:
        return cls(status=OperationStatus.FAILED, message=message, state=dict(state))


class OperationSpec(BaseModel):
    """Static description of a registered operation (introspection / AI discovery)."""

    name: str                          # e.g. "wechat.set_collection"
    description: str = ""
    params: dict[str, str] = Field(default_factory=dict)  # param name → human desc
    preconditions: list[str] = Field(default_factory=list)  # e.g. ["editor_open"]
    category: str = ""                 # e.g. "publish_settings", "navigation"


class OperationExecuteRequest(BaseModel):
    """Body of POST /api/operations/{name}/execute.

    Fields under `params` are passed as kwargs to the operation function.
    `workflow_session_id` optionally binds this execution to a workflow —
    enabling audit linkage and automatic state advancement.
    """

    params: dict[str, Any] = Field(default_factory=dict)
    workflow_session_id: str | None = None


class OperationExecuteResponse(BaseModel):
    """Envelope for a single operation execution."""

    item: OperationResult


class BatchStep(BaseModel):
    """One step in a batch execution request."""

    op: str                            # operation name
    params: dict[str, Any] = Field(default_factory=dict)


class BatchOnError(str, Enum):
    """What the batch executor does when a step fails."""

    STOP = "stop"            # halt immediately (default)
    CONTINUE = "continue"    # keep running remaining steps
    RETRY_ONCE = "retry_once"  # retry the failed step once, then continue regardless


class BatchExecuteRequest(BaseModel):
    """Body of POST /api/operations/batch."""

    steps: list[BatchStep]
    on_error: BatchOnError = BatchOnError.STOP
    # Optional workflow session to record progress against.
    workflow_session_id: str | None = None


class BatchStepResult(BaseModel):
    """Result of one step within a batch."""

    op: str
    params: dict[str, Any]
    result: OperationResult
    attempt: int = 1  # 1 for first try, 2 if retried once


class BatchExecuteResponse(BaseModel):
    """Envelope for batch execution.

    Every step has its own result regardless of on_error policy — a failed step
    never erases the success of prior steps.
    """

    results: list[BatchStepResult]
    all_ok: bool
    stopped_early: bool = False  # true if on_error=stop halted mid-way
