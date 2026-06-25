"""Repository — single place for all persistence logic.

Instead of spreading lock/read/mutate/write boilerplate across callers, we
expose one `transaction()` context manager. Callers focus on the mutation; the
manager handles the session lifecycle.

Mapping between Pydantic models (canonical schema) and ORM rows (storage) lives
here too, so the rest of the codebase never touches SQLAlchemy directly.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy.orm import Session

from ..models.article import Article, Material
from ..models.operation import OperationResult, OperationStatus
from ..models.publish import PublishRecord, PublishTask
from ..models.workflow import (
    IllegalTransitionError,
    WorkflowSession,
    WorkflowState,
)
from .engine import (
    ArticleRow,
    MaterialRow,
    PublishRecordRow,
    PublishTaskRow,
    WorkflowRow,
    get_session_factory,
)


# ── ID generation ───────────────────────────────────────────────────────────
def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _to_iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _to_int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Repository:
    """All persistence operations, behind a single transaction() entry point."""

    # ── Transaction context manager ─────────────────────────────────────────
    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Yield a Session and commit on success, rollback on exception.

        This keeps write serialization in one place.
        Usage:
            with repo.transaction() as session:
                row = session.get(ArticleRow, article_id)
                row.title = "new"
        """
        session_factory = get_session_factory()
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Articles ────────────────────────────────────────────────────────────
    def create_article(
        self,
        *,
        title: str,
        digest: str = "",
        body_markdown: str = "",
        author: str = "",
        material_id: str | None = None,
        level: str = "article",
    ) -> Article:
        article_id = _new_id("art")
        now = _to_iso(datetime.now(timezone.utc))
        row = ArticleRow(
            id=article_id,
            material_id=material_id,
            title=title,
            digest=digest,
            body_markdown=body_markdown,
            author=author,
            level=level,
            stage="draft",
        )
        with self.transaction() as session:
            session.add(row)
        return self._row_to_article(row, now, now)

    def get_article(self, article_id: str) -> Article | None:
        with self.transaction() as session:
            row = session.get(ArticleRow, article_id)
            if row is None:
                return None
            return self._row_to_article(row)

    def list_articles(self, limit: int = 50, offset: int = 0) -> tuple[list[Article], int]:
        from sqlalchemy import func, select

        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(ArticleRow)) or 0
            rows = session.scalars(
                select(ArticleRow).order_by(ArticleRow.created_at.desc()).offset(offset).limit(limit)
            ).all()
            return [self._row_to_article(r) for r in rows], total

    def update_article(self, article_id: str, **fields) -> Article | None:
        with self.transaction() as session:
            row = session.get(ArticleRow, article_id)
            if row is None:
                return None
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._row_to_article(row)

    def delete_article(self, article_id: str) -> bool:
        with self.transaction() as session:
            row = session.get(ArticleRow, article_id)
            if row is None:
                return False
            session.delete(row)
        return True

    def _row_to_article(self, row: ArticleRow, created_at=None, updated_at=None) -> Article:
        return Article(
            id=row.id,
            material_id=row.material_id,
            title=row.title,
            digest=row.digest or "",
            body_markdown=row.body_markdown or "",
            author=row.author or "",
            level=row.level or "article",
            stage=row.stage or "draft",
            wechat_draft_url=row.wechat_draft_url,
            wechat_appmsg_id=row.wechat_appmsg_id,
            last_error=row.last_error,
            created_at=_to_iso(created_at or row.created_at),
            updated_at=_to_iso(updated_at or row.updated_at),
            published_at=_to_iso(row.published_at),
        )

    # ── Materials ───────────────────────────────────────────────────────────
    def create_material(self, **fields) -> Material:
        material_id = fields.pop("id", None) or _new_id("mat")
        row = MaterialRow(id=material_id, **{k: v for k, v in fields.items() if hasattr(MaterialRow, k)})
        with self.transaction() as session:
            session.add(row)
        return self._row_to_material(row)

    def _row_to_material(self, row: MaterialRow) -> Material:
        return Material(
            id=row.id,
            title=row.title or "",
            facts=row.facts or [],
            quotes=row.quotes or [],
            timeline=row.timeline or [],
            entity_names=row.entity_names or [],
            source_links=row.source_links or [],
            risk_notes=row.risk_notes or [],
            created_at=_to_iso(row.created_at),
            updated_at=_to_iso(row.updated_at),
        )

    # ── Workflows ───────────────────────────────────────────────────────────
    def create_workflow(self, article_id: str) -> WorkflowSession:
        wf_id = _new_id("wf")
        row = WorkflowRow(id=wf_id, article_id=article_id, state=WorkflowState.INIT.value)
        with self.transaction() as session:
            session.add(row)
        return self._row_to_workflow(row)

    def get_workflow(self, workflow_id: str) -> WorkflowSession | None:
        with self.transaction() as session:
            row = session.get(WorkflowRow, workflow_id)
            if row is None:
                return None
            return self._row_to_workflow(row)

    def list_workflows(self, limit: int = 50) -> tuple[list[WorkflowSession], int]:
        from sqlalchemy import func, select

        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(WorkflowRow)) or 0
            rows = session.scalars(
                select(WorkflowRow).order_by(WorkflowRow.started_at.desc()).limit(limit)
            ).all()
            return [self._row_to_workflow(r) for r in rows], total

    def save_workflow(self, workflow: WorkflowSession) -> WorkflowSession:
        """Persist a (possibly mutated) WorkflowSession back to storage."""
        with self.transaction() as session:
            row = session.get(WorkflowRow, workflow.id)
            if row is None:
                raise ValueError(f"Workflow {workflow.id} not found")
            row.state = workflow.state.value
            row.settings_applied = workflow.settings_applied
            row.collection_name = workflow.collection_name
            row.claim_source_name = workflow.claim_source_name
            row.cover_prompt = workflow.cover_prompt
            row.last_error = workflow.last_error
            row.updated_at = datetime.now(timezone.utc)
            if workflow.finished_at:
                row.finished_at = _to_dt(workflow.finished_at)
            session.flush()
            return self._row_to_workflow(row)

    def transition_workflow(self, workflow_id: str, target: WorkflowState) -> WorkflowSession:
        """Validate and apply a state transition, then persist.

        Raises IllegalTransitionError on invalid transitions.
        """
        wf = self.get_workflow(workflow_id)
        if wf is None:
            raise ValueError(f"Workflow {workflow_id} not found")
        wf.transition_to(target)
        return self.save_workflow(wf)

    def _row_to_workflow(self, row: WorkflowRow) -> WorkflowSession:
        return WorkflowSession(
            id=row.id,
            article_id=row.article_id,
            state=WorkflowState(row.state),
            settings_applied=row.settings_applied or {},
            collection_name=row.collection_name,
            claim_source_name=row.claim_source_name,
            cover_prompt=row.cover_prompt,
            last_error=row.last_error,
            started_at=_to_iso(row.started_at),
            updated_at=_to_iso(row.updated_at),
            finished_at=_to_iso(row.finished_at),
        )

    # ── Publish records ─────────────────────────────────────────────────────
    def upsert_publish_record(self, record: PublishRecord) -> PublishRecord:
        with self.transaction() as session:
            row = session.get(PublishRecordRow, record.id)
            if row is None:
                row = PublishRecordRow(
                    id=record.id,
                    article_id=record.article_id,
                    platform=record.platform,
                )
                session.add(row)
            row.remote_url = record.remote_url
            row.remote_appmsg_id = record.remote_appmsg_id
            row.published_at = _to_dt(record.published_at)
            row.read_count = str(record.read_count) if record.read_count is not None else None
            row.like_count = str(record.like_count) if record.like_count is not None else None
            row.share_count = str(record.share_count) if record.share_count is not None else None
            row.comment_count = str(record.comment_count) if record.comment_count is not None else None
            row.tip_amount = str(record.tip_amount) if record.tip_amount is not None else None
            session.flush()
            return self._row_to_publish_record(row)

    def list_publish_records(self, limit: int = 50) -> tuple[list[PublishRecord], int]:
        from sqlalchemy import func, select

        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(PublishRecordRow)) or 0
            rows = session.scalars(
                select(PublishRecordRow).order_by(PublishRecordRow.created_at.desc()).limit(limit)
            ).all()
            return [self._row_to_publish_record(r) for r in rows], total

    def _row_to_publish_record(self, row: PublishRecordRow) -> PublishRecord:
        return PublishRecord(
            id=row.id,
            article_id=row.article_id,
            platform=row.platform,
            remote_url=row.remote_url,
            remote_appmsg_id=row.remote_appmsg_id,
            published_at=_to_iso(row.published_at),
            read_count=_to_int_or_none(row.read_count),
            like_count=_to_int_or_none(row.like_count),
            share_count=_to_int_or_none(row.share_count),
            comment_count=_to_int_or_none(row.comment_count),
            tip_amount=_to_float_or_none(row.tip_amount),
            created_at=_to_iso(row.created_at),
            updated_at=_to_iso(row.updated_at),
        )

    # ── Publish tasks (audit trail) ─────────────────────────────────────────
    def record_publish_task(
        self,
        *,
        operation_name: str,
        workflow_session_id: str | None = None,
        article_id: str | None = None,
        status: str = "pending",
        message: str = "",
        params: dict | None = None,
        artifacts: list[str] | None = None,
    ) -> PublishTask:
        task_id = _new_id("task")
        row = PublishTaskRow(
            id=task_id,
            workflow_session_id=workflow_session_id,
            article_id=article_id,
            operation_name=operation_name,
            status=status,
            message=message,
            params=params or {},
            artifacts=artifacts or [],
        )
        with self.transaction() as session:
            session.add(row)
        return self._row_to_publish_task(row)

    def complete_publish_task(self, task_id: str, status: str, message: str = "") -> PublishTask | None:
        with self.transaction() as session:
            row = session.get(PublishTaskRow, task_id)
            if row is None:
                return None
            row.status = status
            row.message = message
            row.finished_at = datetime.now(timezone.utc)
            session.flush()
            return self._row_to_publish_task(row)

    def list_publish_tasks(self, limit: int = 50) -> tuple[list[PublishTask], int]:
        from sqlalchemy import func, select

        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(PublishTaskRow)) or 0
            rows = session.scalars(
                select(PublishTaskRow).order_by(PublishTaskRow.started_at.desc()).limit(limit)
            ).all()
            return [self._row_to_publish_task(r) for r in rows], total

    def get_publish_task(self, task_id: str) -> PublishTask | None:
        with self.transaction() as session:
            row = session.get(PublishTaskRow, task_id)
            return self._row_to_publish_task(row) if row else None

    def _row_to_publish_task(self, row: PublishTaskRow) -> PublishTask:
        return PublishTask(
            id=row.id,
            workflow_session_id=row.workflow_session_id,
            article_id=row.article_id,
            operation_name=row.operation_name,
            platform=row.platform,
            status=row.status,
            message=row.message or "",
            params=row.params or {},
            artifacts=row.artifacts or [],
            started_at=_to_iso(row.started_at),
            finished_at=_to_iso(row.finished_at),
        )

    # ── Operation-result → audit task helper ────────────────────────────────
    def log_operation_result(
        self,
        operation_name: str,
        result: OperationResult,
        *,
        workflow_session_id: str | None = None,
        article_id: str | None = None,
        params: dict | None = None,
    ) -> PublishTask:
        """Record an OperationResult as a publish-task audit row.

        Maps OperationStatus → PublishStatus: ok/skipped → success, failed → failed.
        """
        status = "failed" if result.status == OperationStatus.FAILED else "success"
        return self.record_publish_task(
            operation_name=operation_name,
            workflow_session_id=workflow_session_id,
            article_id=article_id,
            status=status,
            message=result.message,
            params={**(params or {}), "state": result.state},
            artifacts=result.artifacts,
        )


# Module-level singleton — the whole app shares one Repository.
_repository: Repository | None = None


def get_repository() -> Repository:
    global _repository
    if _repository is None:
        _repository = Repository()
    return _repository
