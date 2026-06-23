"""Operation registry — the central catalog of all atomic operations.

This is the heart of the architecture. Every publish step registers here, and
the registry exposes uniform execute / execute_batch / list_specs surfaces that
the HTTP routes (and future MCP server) sit on top of.

Design goals:
- Single source of truth for "what can the agent do".
- Isolated failure: execute() catches exceptions and converts to failed results,
  so one bad step never takes down a batch.
- Batch execution with explicit on_error policy (stop / continue / retry_once).
"""

from __future__ import annotations

from typing import Any

from ..models.operation import (
    BatchExecuteRequest,
    BatchExecuteResponse,
    BatchOnError,
    BatchStepResult,
    OperationResult,
    OperationSpec,
)
from .base import OperationContext, OperationEntry


class OperationNotFoundError(KeyError):
    pass


class OperationRegistry:
    """Registry of atomic operations, keyed by dotted name (e.g. 'wechat.set_original')."""

    def __init__(self) -> None:
        self._entries: dict[str, OperationEntry] = {}

    # ── Registration ────────────────────────────────────────────────────────
    def register(self, entry: OperationEntry) -> None:
        if entry.spec.name in self._entries:
            raise ValueError(f"Operation '{entry.spec.name}' is already registered")
        self._entries[entry.spec.name] = entry

    def has(self, name: str) -> bool:
        return name in self._entries

    def get(self, name: str) -> OperationEntry:
        if name not in self._entries:
            raise OperationNotFoundError(name)
        return self._entries[name]

    def list_specs(self) -> list[OperationSpec]:
        return [entry.spec for entry in self._entries.values()]

    # ── Execution ───────────────────────────────────────────────────────────
    def execute(self, op_name: str, *, ctx: OperationContext | None = None, **params: Any) -> OperationResult:
        """Execute one operation by name.

        NOTE: the first parameter is `op_name` (keyword-ish), NOT `name`, to
        avoid colliding with operations that legitimately take a `name` param
        (e.g. set_collection(name=...)). Callers pass the operation name
        positionally; operation params flow through **params without clash.

        Never raises for business failures — returns status=failed instead.
        Programming errors (bugs) still raise so they're visible.
        """
        try:
            entry = self.get(op_name)
        except OperationNotFoundError as e:
            return OperationResult.failure(message=f"Operation '{op_name}' not registered")

        if ctx is None:
            ctx = OperationContext()

        try:
            return entry(ctx, **params)
        except Exception as e:  # noqa: BLE001 — operations must be fault-isolated
            return OperationResult.failure(
                message=f"{op_name}: {type(e).__name__}: {e}",
            )

    def execute_batch(self, req: BatchExecuteRequest) -> BatchExecuteResponse:
        """Execute steps in sequence with the requested on_error policy.

        Each step gets its own result regardless of policy — a failure never
        erases prior successes. This is the direct fix for the old project's
        "one step fails, whole pipeline dies".
        """
        ctx = OperationContext(workflow_session_id=req.workflow_session_id)
        results: list[BatchStepResult] = []
        stopped_early = False

        for step in req.steps:
            attempt = 1
            result = self.execute(step.op, ctx=ctx, **step.params)

            # Retry-once policy: if failed, retry the same step once.
            if (
                result.status.value == "failed"
                and req.on_error == BatchOnError.RETRY_ONCE
            ):
                attempt = 2
                result = self.execute(step.op, ctx=ctx, **step.params)

            results.append(
                BatchStepResult(op=step.op, params=step.params, result=result, attempt=attempt)
            )

            # Stop policy: halt on first failure.
            if result.status.value == "failed" and req.on_error == BatchOnError.STOP:
                stopped_early = True
                break

        all_ok = all(r.result.status.value != "failed" for r in results)
        return BatchExecuteResponse(results=results, all_ok=all_ok, stopped_early=stopped_early)


# Module-level singleton.
OPERATION_REGISTRY = OperationRegistry()
