"""SQLAlchemy engine & ORM table definitions.

We store everything in a single SQLite file. Tables mirror the Pydantic models
in agent_news/models/ but are kept as plain ORM mappings here — the Pydantic
models remain the canonical public schema; ORM rows are storage rows.

JSON-valued columns (lists/dicts from Pydantic) are serialized to TEXT via
SQLAlchemy's JSON type, which SQLite stores as TEXT natively.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArticleRow(Base):
    __tablename__ = "articles"

    id = Column(String, primary_key=True)
    material_id = Column(String, nullable=True)
    title = Column(String, nullable=False, default="")
    digest = Column(Text, default="")
    body_markdown = Column(Text, default="")
    author = Column(String, default="")
    level = Column(String, default="article")          # material | article
    stage = Column(String, default="draft")             # draft|synced|published|failed
    wechat_draft_url = Column(String, nullable=True)
    wechat_appmsg_id = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    published_at = Column(DateTime, nullable=True)


class MaterialRow(Base):
    __tablename__ = "materials"

    id = Column(String, primary_key=True)
    title = Column(String, default="")
    facts = Column(JSON, default=list)
    quotes = Column(JSON, default=list)
    timeline = Column(JSON, default=list)
    entity_names = Column(JSON, default=list)
    source_links = Column(JSON, default=list)
    risk_notes = Column(JSON, default=list)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class WorkflowRow(Base):
    __tablename__ = "workflows"

    id = Column(String, primary_key=True)
    article_id = Column(String, nullable=False)
    state = Column(String, default="init")
    settings_applied = Column(JSON, default=dict)
    collection_name = Column(String, nullable=True)
    claim_source_name = Column(String, nullable=True)
    cover_prompt = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    finished_at = Column(DateTime, nullable=True)


class PublishRecordRow(Base):
    __tablename__ = "publish_records"

    id = Column(String, primary_key=True)
    article_id = Column(String, nullable=False)
    platform = Column(String, default="wechat")
    remote_url = Column(String, nullable=True)
    remote_appmsg_id = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)
    read_count = Column(String, nullable=True)         # stored as text for null safety
    like_count = Column(String, nullable=True)
    share_count = Column(String, nullable=True)
    comment_count = Column(String, nullable=True)
    tip_amount = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class PublishTaskRow(Base):
    __tablename__ = "publish_tasks"

    id = Column(String, primary_key=True)
    workflow_session_id = Column(String, nullable=True)
    article_id = Column(String, nullable=True)
    operation_name = Column(String, nullable=False)
    platform = Column(String, default="wechat")
    status = Column(String, default="pending")
    message = Column(Text, default="")
    params = Column(JSON, default=dict)
    artifacts = Column(JSON, default=list)
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)


# ── Information radar tables ────────────────────────────────────────────────
class SourceRow(Base):
    __tablename__ = "sources"

    key = Column(String, primary_key=True)
    name = Column(String, default="")
    kind = Column(String, default="rss")
    url = Column(Text, default="")
    enabled = Column(String, default="true")           # stored as text for SQLite bool
    priority = Column(String, default="50")
    weight = Column(String, default="1.0")
    tags = Column(JSON, default=list)
    schedule = Column(String, default="")
    capabilities = Column(JSON, default=list)
    config = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class RawItemRow(Base):
    __tablename__ = "raw_items"

    id = Column(String, primary_key=True)
    source_key = Column(String, default="")
    source_name = Column(String, default="")
    title = Column(Text, default="")
    link = Column(Text, default="")
    summary = Column(Text, default="")
    published_at = Column(DateTime, nullable=True)
    collected_at = Column(DateTime, default=_utcnow)
    tags = Column(JSON, default=list)
    engagement_score = Column(String, default="0.0")
    metadata_ = Column("metadata", JSON, default=dict)


class IntelEventRow(Base):
    __tablename__ = "intel_events"

    id = Column(String, primary_key=True)
    title = Column(Text, default="")
    summary = Column(Text, default="")
    representative_link = Column(Text, default="")
    discovery_item_ids = Column(JSON, default=list)
    source_keys = Column(JSON, default=list)
    source_names = Column(JSON, default=list)
    platforms = Column(JSON, default=list)
    platform_count = Column(String, default="0")
    source_count = Column(String, default="0")
    member_count = Column(String, default="0")
    story_count = Column(String, default="0")
    member_delta = Column(String, default="0")
    platform_delta = Column(String, default="0")
    published_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    tags = Column(JSON, default=list)
    anchor_tokens = Column(JSON, default=list)
    velocity_score = Column(String, default="0.0")
    coverage_score = Column(String, default="0.0")
    freshness_score = Column(String, default="0.0")
    audience_fit_score = Column(String, default="0.0")
    composite_score = Column(String, default="0.0")
    velocity_details = Column(JSON, default=dict)
    alert_state = Column(String, default="new")
    change_state = Column(String, default="new_event")
    alert_reason = Column(Text, default="")
    entity_names = Column(JSON, default=list)
    watchlisted = Column(String, default="false")
    ignored = Column(String, default="false")
    deep_dive_id = Column(String, nullable=True)
    article_id = Column(String, nullable=True)
    deep_dive_status = Column(String, nullable=True)
    deep_dive_summary = Column(Text, default="")
    worth_to_brief = Column(String, default="false")
    worth_reason = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class IntelAlertRow(Base):
    __tablename__ = "intel_alerts"

    id = Column(String, primary_key=True)
    event_id = Column(String, nullable=False)
    title = Column(Text, default="")
    summary = Column(Text, default="")
    alert_state = Column(String, default="rising")
    alert_reason = Column(Text, default="")
    velocity_score = Column(String, default="0.0")
    coverage_score = Column(String, default="0.0")
    freshness_score = Column(String, default="0.0")
    audience_fit_score = Column(String, default="0.0")
    composite_score = Column(String, default="0.0")
    platform_count = Column(String, default="0")
    source_count = Column(String, default="0")
    representative_link = Column(Text, default="")
    entity_names = Column(JSON, default=list)
    raised_at = Column(DateTime, default=_utcnow)


class DeepDiveRow(Base):
    __tablename__ = "deep_dives"

    id = Column(String, primary_key=True)
    event_id = Column(String, nullable=False)
    status = Column(String, default="pending")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    attempted_count = Column(String, default="0")
    success_count = Column(String, default="0")
    failed_count = Column(String, default="0")
    resolved_evidence_pack = Column(JSON, default=list)
    full_text_sources = Column(JSON, default=list)
    sources = Column(JSON, default=list)
    facts = Column(JSON, default=list)
    quotes = Column(JSON, default=list)
    timeline = Column(JSON, default=list)
    worthiness = Column(JSON, default=dict)
    article_writing_guide = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    """Lazily create the SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_runtime_dirs()
        # check_same_thread=False: FastAPI runs across threads.
        _engine = create_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(_engine)
    return _engine


def get_session_factory() -> sessionmaker:
    """Lazily create the session factory (singleton)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal
