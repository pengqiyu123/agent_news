"""Audit review atomic operations."""

from __future__ import annotations

from ..db import get_repository
from ..models.operation import OperationResult
from .base import operation


@operation(
    name="audit.review_tasks",
    category="audit",
    description="只读：查看最近操作审计、失败步骤和错误信息。",
    params={"limit": "返回数量，默认 20", "status": "可选过滤状态", "operation_prefix": "可选操作名前缀"},
)
def review_tasks(ctx, limit: int = 20, status: str = "", operation_prefix: str = "") -> OperationResult:
    limit = max(1, min(int(limit), 200))
    items, total = get_repository().list_publish_tasks(limit=limit)
    filtered = []
    for item in items:
        if status and item.status != status:
            continue
        if operation_prefix and not item.operation_name.startswith(operation_prefix):
            continue
        filtered.append(item)
    failures = [item for item in filtered if item.status == "failed"]
    return OperationResult.success(
        message=f"reviewed {len(filtered)} audit tasks",
        items=[item.model_dump() for item in filtered],
        total=total,
        filtered_count=len(filtered),
        failure_count=len(failures),
        failures=[item.model_dump() for item in failures],
    )

