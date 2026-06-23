"""Publish record & task models.

- PublishRecord: an article's publish outcome on a platform (engagement metrics etc.)
- PublishTask  : an audit-trail entry for each browser operation attempt

These are populated after publish operations run, not authored directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


Platform = Literal["wechat", "douyin"]
PublishStatus = Literal["pending", "success", "failed"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PublishRecord(BaseModel):
    """One article's published state on one platform, with engagement metrics.

    Metrics are populated by scraping the platform's publish history
    (e.g. WeChat 内容管理 → 发表记录), mirroring the old project's pattern.
    """

    id: str
    article_id: str
    platform: Platform
    # Remote identifiers
    remote_url: str | None = None
    remote_appmsg_id: str | None = None
    published_at: str | None = None
    # Engagement (populated lazily by history-scrape operations)
    read_count: int | None = None
    like_count: int | None = None
    share_count: int | None = None
    comment_count: int | None = None
    tip_amount: float | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class PublishTask(BaseModel):
    """Audit-trail entry for a single browser operation attempt.

    Every atomic operation execution (single or batch step) can record a task
    so the operator can review what happened, even if the step failed.
    """

    id: str
    workflow_session_id: str | None = None
    article_id: str | None = None
    operation_name: str               # e.g. "wechat.save_as_draft"
    platform: Platform = "wechat"
    status: PublishStatus = "pending"
    message: str = ""
    # Snapshot of params passed in (for reproducibility).
    params: dict[str, object] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=_utcnow)
    finished_at: str | None = None


class PublishRecordResponse(BaseModel):
    item: PublishRecord


class PublishRecordListResponse(BaseModel):
    items: list[PublishRecord]
    total: int


class PublishTaskListResponse(BaseModel):
    items: list[PublishTask]
    total: int
