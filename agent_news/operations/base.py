"""Atomic-operation base primitives.

This module defines:
- The OperationContext passed to every operation (page, selectors, state).
- The @operation decorator that registers a function into the global registry.
- The callable signature all operations share.

The contract: an operation is a plain function taking an OperationContext plus
its own keyword params, returning an OperationResult. It must NEVER raise for
expected business failures — it returns status=failed so callers decide what
to do. Only programming errors (bugs) raise.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..models.operation import OperationResult, OperationSpec

if TYPE_CHECKING:
    pass  # forward-only imports for typing


@dataclass
class OperationContext:
    """Everything an operation needs from the runtime.

    Populated by the registry's execute() before calling the operation.
    `page` and `selectors` are None when the operation doesn't touch the browser
    (e.g. a pure data operation). Operations should check `ctx.page is not None`
    before browser access and fail gracefully otherwise.
    """

    # Playwright Page — None until the browser layer (phase 2) provides it.
    page: Any = None
    # Selector profile dict (phase 2): {"key": ["selector1", "selector2", ...]}
    selectors: dict[str, list[str]] | None = None
    # The article being published, if any.
    article_id: str | None = None
    # The workflow session, if running within one.
    workflow_session_id: str | None = None
    # Mutable scratch state shared across steps in a batch.
    shared_state: dict[str, Any] = field(default_factory=dict)


# Type of a registered operation function.
OperationFunc = Callable[..., OperationResult]


class OperationEntry:
    """A registered operation: its spec + the callable."""

    def __init__(
        self,
        func: OperationFunc,
        *,
        name: str,
        description: str,
        params: dict[str, str],
        preconditions: list[str],
        category: str,
    ) -> None:
        self.func = func
        self.spec = OperationSpec(
            name=name,
            description=description,
            params=params,
            preconditions=preconditions,
            category=category,
        )

    def __call__(self, ctx: OperationContext, **kwargs: Any) -> OperationResult:
        return self.func(ctx, **kwargs)


def operation(
    *,
    name: str,
    description: str = "",
    params: dict[str, str] | None = None,
    preconditions: list[str] | None = None,
    category: str = "",
) -> Callable[[OperationFunc], OperationFunc]:
    """Decorator: register a function as an atomic operation.

    Usage:
        @operation(name="wechat.set_original", category="publish_settings",
                   params={"enabled": "bool, default True"})
        def set_original(ctx, enabled: bool = True) -> OperationResult:
            ...
    """

    def decorator(func: OperationFunc) -> OperationFunc:
        from .registry import OPERATION_REGISTRY

        # Description: explicit arg wins; else first non-empty docstring line.
        resolved_description = description
        if not resolved_description and func.__doc__:
            doc_lines = [line.strip() for line in func.__doc__.strip().splitlines() if line.strip()]
            resolved_description = doc_lines[0] if doc_lines else ""

        entry = OperationEntry(
            func,
            name=name,
            description=resolved_description,
            params=params or {},
            preconditions=preconditions or [],
            category=category,
        )
        OPERATION_REGISTRY.register(entry)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> OperationResult:
            return func(*args, **kwargs)

        # Attach the entry so introspection works on the bare function too.
        wrapper._operation_entry = entry  # type: ignore[attr-defined]
        return wrapper

    return decorator
