"""Intel repository — persistence for the information radar.

Kept separate from repository.py (publish chain) so each domain has its own
focused persistence module, both sharing the same transaction() context manager
inherited from Repository.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.intel import (
    DeepDiveSourceItem,
    DiscoveryItem,
    EventDeepDive,
    IntelAlert,
    IntelEvent,
    RawItem,
    Source,
)
from .engine import (
    DeepDiveRow,
    IntelAlertRow,
    IntelEventRow,
    RawItemRow,
    SourceRow,
    get_session_factory,
)
from .repository import Repository, _to_dt, _to_float_or_none, _to_int_or_none, _to_iso


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _bool_text(value: bool | str | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return "false"


def _text_bool(value: str | None) -> bool:
    return str(value).lower() in ("true", "1", "yes")


class IntelRepository(Repository):
    """Information-radar persistence: sources, raw items, events, alerts, deep dives."""

    # ── Sources ─────────────────────────────────────────────────────────────
    def upsert_source(self, source: Source) -> Source:
        with self.transaction() as session:
            row = session.get(SourceRow, source.key)
            if row is None:
                row = SourceRow(key=source.key)
                session.add(row)
            self._apply_source(row, source)
            session.flush()
            return self._row_to_source(row)

    def get_source(self, key: str) -> Source | None:
        with self.transaction() as session:
            row = session.get(SourceRow, key)
            return self._row_to_source(row) if row else None

    def list_sources(self, enabled_only: bool = False) -> list[Source]:
        with self.transaction() as session:
            stmt = select(SourceRow).order_by(SourceRow.priority.desc())
            rows = session.scalars(stmt).all()
            sources = [self._row_to_source(r) for r in rows]
            if enabled_only:
                sources = [s for s in sources if s.enabled]
            return sources

    def delete_source(self, key: str) -> bool:
        with self.transaction() as session:
            row = session.get(SourceRow, key)
            if row is None:
                return False
            session.delete(row)
        return True

    def update_source_fields(self, key: str, **fields: Any) -> Source | None:
        with self.transaction() as session:
            row = session.get(SourceRow, key)
            if row is None:
                return None
            allowed = {
                "name",
                "kind",
                "url",
                "enabled",
                "priority",
                "weight",
                "tags",
                "schedule",
                "capabilities",
                "config",
            }
            for field, value in fields.items():
                if field not in allowed:
                    continue
                if field == "enabled":
                    row.enabled = _bool_text(value)
                elif field in ("priority", "weight"):
                    setattr(row, field, str(value))
                else:
                    setattr(row, field, value)
            session.flush()
            return self._row_to_source(row)

    def _apply_source(self, row: SourceRow, source: Source) -> None:
        row.name = source.name
        row.kind = source.kind
        row.url = source.url
        row.enabled = _bool_text(source.enabled)
        row.priority = str(source.priority)
        row.weight = str(source.weight)
        row.tags = source.tags
        row.schedule = source.schedule
        row.capabilities = source.capabilities
        row.config = source.config

    def _row_to_source(self, row: SourceRow) -> Source:
        return Source(
            key=row.key,
            name=row.name or "",
            kind=row.kind or "rss",
            url=row.url or "",
            enabled=_text_bool(row.enabled),
            priority=_to_int_or_none(row.priority) or 50,
            weight=_to_float_or_none(row.weight) or 1.0,
            tags=row.tags or [],
            schedule=row.schedule or "",
            capabilities=row.capabilities or ["pull", "dedupe", "score"],
            config=row.config or {},
        )

    # ── Raw items ───────────────────────────────────────────────────────────
    def add_raw_items(self, items: list[RawItem]) -> list[RawItem]:
        with self.transaction() as session:
            for item in items:
                row = RawItemRow(
                    id=item.id or _new_id("raw"),
                    source_key=item.source_key,
                    source_name=item.source_name,
                    title=item.title,
                    link=item.link,
                    summary=item.summary,
                    published_at=_to_dt(item.published_at),
                    collected_at=_to_dt(item.collected_at) or datetime.now(timezone.utc),
                    tags=item.tags,
                    engagement_score=str(item.engagement_score),
                    metadata_=item.metadata,
                )
                session.merge(row)
        return items

    def list_raw_items(self, limit: int = 100, offset: int = 0) -> tuple[list[RawItem], int]:
        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(RawItemRow)) or 0
            rows = session.scalars(
                select(RawItemRow).order_by(RawItemRow.collected_at.desc()).offset(offset).limit(limit)
            ).all()
            return [self._row_to_raw_item(r) for r in rows], total

    def clear_raw_items(self) -> int:
        """Wipe raw items (called after clustering so they don't accumulate forever)."""
        with self.transaction() as session:
            count = session.scalar(select(func.count()).select_from(RawItemRow)) or 0
            session.query(RawItemRow).delete()
            return count

    def _row_to_raw_item(self, row: RawItemRow) -> RawItem:
        return RawItem(
            id=row.id,
            source_key=row.source_key or "",
            source_name=row.source_name or "",
            title=row.title or "",
            link=row.link or "",
            summary=row.summary or "",
            published_at=_to_iso(row.published_at),
            collected_at=_to_iso(row.collected_at),
            tags=row.tags or [],
            engagement_score=_to_float_or_none(row.engagement_score) or 0.0,
            metadata=row.metadata_ or {},
        )

    # ── Events ──────────────────────────────────────────────────────────────
    def upsert_event(self, event: IntelEvent) -> IntelEvent:
        with self.transaction() as session:
            row = session.get(IntelEventRow, event.id)
            if row is None:
                row = IntelEventRow(id=event.id)
                session.add(row)
            self._apply_event(row, event)
            session.flush()
            return self._row_to_event(row)

    def update_event_fields(self, event_id: str, **fields: Any) -> IntelEvent | None:
        with self.transaction() as session:
            row = session.get(IntelEventRow, event_id)
            if row is None:
                return None
            allowed = {
                "ignored",
                "watchlisted",
                "worth_to_brief",
                "worth_reason",
                "deep_dive_id",
                "deep_dive_status",
                "deep_dive_summary",
                "article_id",
            }
            for field, value in fields.items():
                if field not in allowed:
                    continue
                if field in ("ignored", "watchlisted", "worth_to_brief"):
                    setattr(row, field, _bool_text(value))
                else:
                    setattr(row, field, value)
            session.flush()
            return self._row_to_event(row)

    def upsert_events(self, events: list[IntelEvent]) -> list[IntelEvent]:
        """Bulk upsert — the common case after a cluster+score run."""
        return [self.upsert_event(e) for e in events]

    def get_event(self, event_id: str) -> IntelEvent | None:
        with self.transaction() as session:
            row = session.get(IntelEventRow, event_id)
            return self._row_to_event(row) if row else None

    def list_events(
        self,
        limit: int = 50,
        offset: int = 0,
        ignored: bool | None = False,
        min_score: float | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[list[IntelEvent], int]:
        with self.transaction() as session:
            stmt = select(IntelEventRow)
            # filter ignored by default — agent doesn't want noise
            if ignored is False:
                stmt = stmt.where(IntelEventRow.ignored == "false")
            elif ignored is True:
                stmt = stmt.where(IntelEventRow.ignored == "true")
            if start_at is not None and end_at is not None:
                # Editorial freshness should prefer the source publish time, but
                # some sources miss it; first/last seen and row timestamps keep
                # same-day collected items usable without surfacing old history.
                event_time = func.coalesce(
                    IntelEventRow.published_at,
                    IntelEventRow.first_seen_at,
                    IntelEventRow.last_seen_at,
                    IntelEventRow.updated_at,
                    IntelEventRow.created_at,
                )
                stmt = stmt.where(event_time >= start_at, event_time <= end_at)
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = session.scalar(count_stmt) or 0
            rows = session.scalars(
                stmt.order_by(IntelEventRow.composite_score.desc()).offset(offset).limit(limit)
            ).all()
            events = [self._row_to_event(r) for r in rows]
            if min_score is not None:
                events = [e for e in events if e.composite_score >= min_score]
            return events, total

    def _apply_event(self, row: IntelEventRow, event: IntelEvent) -> None:
        row.title = event.title
        row.summary = event.summary
        row.representative_link = event.representative_link
        row.discovery_item_ids = event.discovery_item_ids
        row.source_keys = event.source_keys
        row.source_names = event.source_names
        row.platforms = event.platforms
        row.platform_count = str(event.platform_count)
        row.source_count = str(event.source_count)
        row.member_count = str(event.member_count)
        row.story_count = str(event.story_count)
        row.member_delta = str(event.member_delta)
        row.platform_delta = str(event.platform_delta)
        row.published_at = _to_dt(event.published_at)
        row.first_seen_at = _to_dt(event.first_seen_at)
        row.last_seen_at = _to_dt(event.last_seen_at)
        row.tags = event.tags
        row.anchor_tokens = event.anchor_tokens
        row.velocity_score = str(event.velocity_score)
        row.coverage_score = str(event.coverage_score)
        row.freshness_score = str(event.freshness_score)
        row.audience_fit_score = str(event.audience_fit_score)
        row.composite_score = str(event.composite_score)
        row.velocity_details = event.velocity_details
        row.alert_state = event.alert_state
        row.change_state = event.change_state
        row.alert_reason = event.alert_reason
        row.entity_names = event.entity_names
        row.watchlisted = _bool_text(event.watchlisted)
        row.ignored = _bool_text(event.ignored)
        row.deep_dive_id = event.deep_dive_id
        row.article_id = event.article_id
        row.deep_dive_status = event.deep_dive_status
        row.deep_dive_summary = event.deep_dive_summary
        row.worth_to_brief = _bool_text(event.worth_to_brief)
        row.worth_reason = event.worth_reason

    def _row_to_event(self, row: IntelEventRow) -> IntelEvent:
        return IntelEvent(
            id=row.id,
            title=row.title or "",
            summary=row.summary or "",
            representative_link=row.representative_link or "",
            discovery_item_ids=row.discovery_item_ids or [],
            source_keys=row.source_keys or [],
            source_names=row.source_names or [],
            platforms=row.platforms or [],
            platform_count=_to_int_or_none(row.platform_count) or 0,
            source_count=_to_int_or_none(row.source_count) or 0,
            member_count=_to_int_or_none(row.member_count) or 0,
            story_count=_to_int_or_none(row.story_count) or 0,
            member_delta=_to_int_or_none(row.member_delta) or 0,
            platform_delta=_to_int_or_none(row.platform_delta) or 0,
            published_at=_to_iso(row.published_at),
            first_seen_at=_to_iso(row.first_seen_at),
            last_seen_at=_to_iso(row.last_seen_at),
            tags=row.tags or [],
            anchor_tokens=row.anchor_tokens or [],
            velocity_score=_to_float_or_none(row.velocity_score) or 0.0,
            coverage_score=_to_float_or_none(row.coverage_score) or 0.0,
            freshness_score=_to_float_or_none(row.freshness_score) or 0.0,
            audience_fit_score=_to_float_or_none(row.audience_fit_score) or 0.0,
            composite_score=_to_float_or_none(row.composite_score) or 0.0,
            velocity_details=row.velocity_details or {},
            alert_state=row.alert_state or "new",
            change_state=row.change_state or "new_event",
            alert_reason=row.alert_reason or "",
            entity_names=row.entity_names or [],
            watchlisted=_text_bool(row.watchlisted),
            ignored=_text_bool(row.ignored),
            deep_dive_id=row.deep_dive_id,
            article_id=row.article_id,
            deep_dive_status=row.deep_dive_status,
            deep_dive_summary=row.deep_dive_summary or "",
            worth_to_brief=_text_bool(row.worth_to_brief),
            worth_reason=row.worth_reason or "",
            created_at=_to_iso(row.created_at),
            updated_at=_to_iso(row.updated_at),
        )

    # ── Alerts ──────────────────────────────────────────────────────────────
    def add_alerts(self, alerts: list[IntelAlert]) -> list[IntelAlert]:
        with self.transaction() as session:
            for alert in alerts:
                row = IntelAlertRow(
                    id=alert.id or _new_id("alert"),
                    event_id=alert.event_id,
                    title=alert.title,
                    summary=alert.summary,
                    alert_state=alert.alert_state,
                    alert_reason=alert.alert_reason,
                    velocity_score=str(alert.velocity_score),
                    coverage_score=str(alert.coverage_score),
                    freshness_score=str(alert.freshness_score),
                    audience_fit_score=str(alert.audience_fit_score),
                    composite_score=str(alert.composite_score),
                    platform_count=str(alert.platform_count),
                    source_count=str(alert.source_count),
                    representative_link=alert.representative_link,
                    entity_names=alert.entity_names,
                    raised_at=_to_dt(alert.raised_at) or datetime.now(timezone.utc),
                )
                session.add(row)
        return alerts

    def clear_alerts(self) -> int:
        with self.transaction() as session:
            count = session.scalar(select(func.count()).select_from(IntelAlertRow)) or 0
            session.query(IntelAlertRow).delete()
            return count

    def list_alerts(self, limit: int = 50) -> tuple[list[IntelAlert], int]:
        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(IntelAlertRow)) or 0
            rows = session.scalars(
                select(IntelAlertRow).order_by(IntelAlertRow.composite_score.desc()).limit(limit)
            ).all()
            return [self._row_to_alert(r) for r in rows], total

    def _row_to_alert(self, row: IntelAlertRow) -> IntelAlert:
        return IntelAlert(
            id=row.id,
            event_id=row.event_id,
            title=row.title or "",
            summary=row.summary or "",
            alert_state=row.alert_state or "rising",
            alert_reason=row.alert_reason or "",
            velocity_score=_to_float_or_none(row.velocity_score) or 0.0,
            coverage_score=_to_float_or_none(row.coverage_score) or 0.0,
            freshness_score=_to_float_or_none(row.freshness_score) or 0.0,
            audience_fit_score=_to_float_or_none(row.audience_fit_score) or 0.0,
            composite_score=_to_float_or_none(row.composite_score) or 0.0,
            platform_count=_to_int_or_none(row.platform_count) or 0,
            source_count=_to_int_or_none(row.source_count) or 0,
            representative_link=row.representative_link or "",
            entity_names=row.entity_names or [],
            raised_at=_to_iso(row.raised_at),
        )

    # ── Deep dives ──────────────────────────────────────────────────────────
    def upsert_deep_dive(self, dive: EventDeepDive) -> EventDeepDive:
        with self.transaction() as session:
            row = session.get(DeepDiveRow, dive.id)
            if row is None:
                row = DeepDiveRow(id=dive.id, event_id=dive.event_id)
                session.add(row)
            self._apply_deep_dive(row, dive)
            session.flush()
            return self._row_to_deep_dive(row)

    def get_deep_dive(self, dive_id: str) -> EventDeepDive | None:
        with self.transaction() as session:
            row = session.get(DeepDiveRow, dive_id)
            return self._row_to_deep_dive(row) if row else None

    def get_deep_dive_by_event(self, event_id: str) -> EventDeepDive | None:
        with self.transaction() as session:
            row = session.scalar(
                select(DeepDiveRow).where(DeepDiveRow.event_id == event_id).order_by(
                    DeepDiveRow.created_at.desc()
                ).limit(1)
            )
            return self._row_to_deep_dive(row) if row else None

    def list_deep_dives(self, limit: int = 50) -> tuple[list[EventDeepDive], int]:
        with self.transaction() as session:
            total = session.scalar(select(func.count()).select_from(DeepDiveRow)) or 0
            rows = session.scalars(
                select(DeepDiveRow).order_by(DeepDiveRow.created_at.desc()).limit(limit)
            ).all()
            return [self._row_to_deep_dive(r) for r in rows], total

    def _apply_deep_dive(self, row: DeepDiveRow, dive: EventDeepDive) -> None:
        row.event_id = dive.event_id
        row.status = dive.status
        row.started_at = _to_dt(dive.started_at)
        row.finished_at = _to_dt(dive.finished_at)
        row.attempted_count = str(dive.attempted_count)
        row.success_count = str(dive.success_count)
        row.failed_count = str(dive.failed_count)
        row.resolved_evidence_pack = dive.resolved_evidence_pack
        row.full_text_sources = [s.model_dump() for s in dive.full_text_sources]
        row.sources = [s.model_dump() for s in dive.sources]
        row.facts = dive.facts
        row.quotes = dive.quotes
        row.timeline = dive.timeline
        row.worthiness = dive.worthiness
        row.article_writing_guide = dive.article_writing_guide

    def _row_to_deep_dive(self, row: DeepDiveRow) -> EventDeepDive:
        return EventDeepDive(
            id=row.id,
            event_id=row.event_id,
            status=row.status or "pending",
            started_at=_to_iso(row.started_at),
            finished_at=_to_iso(row.finished_at),
            attempted_count=_to_int_or_none(row.attempted_count) or 0,
            success_count=_to_int_or_none(row.success_count) or 0,
            failed_count=_to_int_or_none(row.failed_count) or 0,
            resolved_evidence_pack=row.resolved_evidence_pack or [],
            full_text_sources=[
                DeepDiveSourceItem(**s) for s in (row.full_text_sources or [])
                if isinstance(s, dict)
            ],
            sources=[
                DeepDiveSourceItem(**s) for s in (row.sources or [])
                if isinstance(s, dict)
            ],
            facts=row.facts or [],
            quotes=row.quotes or [],
            timeline=row.timeline or [],
            worthiness=row.worthiness or {},
            article_writing_guide=row.article_writing_guide or "",
            created_at=_to_iso(row.created_at),
            updated_at=_to_iso(row.updated_at),
        )


# Module-level singleton for intel operations.
_intel_repository: IntelRepository | None = None


def get_intel_repository() -> IntelRepository:
    global _intel_repository
    if _intel_repository is None:
        _intel_repository = IntelRepository()
    return _intel_repository
