"""Intel models — the information radar data layer.

Models are trimmed to what agent mode needs. Dashboard-only decoration fields
(snapshots, freshness monitors, top-bar) are dropped — agent mode reads
events/alerts directly.

Stages this covers:
  Stage 1 (采集) → Source / RawItem
  Stage 2 (聚类) → DiscoveryItem → IntelEvent
  Stage 3 (热度) → IntelEvent score fields + IntelAlert
  Stage 4 (深挖) → EventDeepDive
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────
SourceKind = Literal["rss", "html", "hotlist", "monitor", "api"]
IntelEventState = Literal["new", "rising", "hot", "cooling", "cold"]
IntelEventChangeState = Literal["new_event", "growing_event", "stable_event", "declining_event"]
DeepDiveStatus = Literal["pending", "ready", "partial", "failed"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Stage 1: Sources & raw items ────────────────────────────────────────────
class Source(BaseModel):
    """A configured information source (RSS feed, HTML scraper, etc.)."""

    key: str                          # unique id, e.g. "openai-blog"
    name: str = ""
    kind: SourceKind = "rss"
    url: str = ""
    enabled: bool = True
    priority: int = 50                # higher = more authoritative
    weight: float = 1.0               # scoring weight
    tags: list[str] = Field(default_factory=list)
    # cron-style schedule hint (agent may ignore and just sync all)
    schedule: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["pull", "dedupe", "score"])
    config: dict[str, Any] = Field(default_factory=dict)  # kind-specific options


class RawItem(BaseModel):
    """A raw discovery before normalization/clustering."""

    id: str
    source_key: str = ""
    source_name: str = ""
    title: str = ""
    link: str = ""
    summary: str = ""
    published_at: str | None = None
    collected_at: str = Field(default_factory=_utcnow)
    tags: list[str] = Field(default_factory=list)
    engagement_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Stage 2: Discovery items (normalized, tokenized, pre-cluster) ───────────
class DiscoveryItem(BaseModel):
    """A normalized raw item, ready for clustering."""

    id: str
    source_key: str = ""
    source_name: str = ""
    title: str = ""
    summary: str = ""
    link: str = ""
    canonical_link: str = ""
    dedupe_key: str = ""
    source_native_id: str | None = None
    title_tokens: list[str] = Field(default_factory=list)
    anchor_tokens: list[str] = Field(default_factory=list)
    published_at: str | None = None
    collected_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    engagement_score: float = 0.0
    entity_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Stage 2+3: Event (cluster) with hotness scores ──────────────────────────
class IntelEvent(BaseModel):
    """A clustered event (热点簇) with composite hotness scoring.

    This is the central object the agent reads to decide what's worth writing.
    Scores follow the weighted formula:
        composite = velocity*0.28 + coverage*0.22 + freshness*0.20 + audience_fit*0.30
    """

    id: str                           # deterministic evt-<sha1[:12]>
    title: str = ""
    summary: str = ""
    representative_link: str = ""
    discovery_item_ids: list[str] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)
    source_names: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    platform_count: int = 0
    source_count: int = 0
    member_count: int = 0
    story_count: int = 0
    member_delta: int = 0
    platform_delta: int = 0
    published_at: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    anchor_tokens: list[str] = Field(default_factory=list)
    # Hotness scores
    velocity_score: float = 0.0
    coverage_score: float = 0.0
    freshness_score: float = 0.0
    audience_fit_score: float = 0.0
    composite_score: float = 0.0
    velocity_details: dict[str, float] = Field(default_factory=dict)
    alert_state: IntelEventState = "new"
    change_state: IntelEventChangeState = "new_event"
    alert_reason: str = ""
    entity_names: list[str] = Field(default_factory=list)
    watchlisted: bool = False
    ignored: bool = False
    # Deep-dive / article linkage (filled by stages 4-5)
    deep_dive_id: str | None = None
    article_id: str | None = None
    deep_dive_status: DeepDiveStatus | None = None
    deep_dive_summary: str = ""
    worth_to_brief: bool = False
    worth_reason: str = ""
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


# ── Stage 3: Alerts ──────────────────────────────────────────────────────────
class IntelAlert(BaseModel):
    """A materialized hot/rising alert the agent should look at."""

    id: str
    event_id: str
    title: str = ""
    summary: str = ""
    alert_state: IntelEventState = "rising"
    alert_reason: str = ""
    velocity_score: float = 0.0
    coverage_score: float = 0.0
    freshness_score: float = 0.0
    audience_fit_score: float = 0.0
    composite_score: float = 0.0
    platform_count: int = 0
    source_count: int = 0
    representative_link: str = ""
    entity_names: list[str] = Field(default_factory=list)
    raised_at: str = Field(default_factory=_utcnow)


# ── Stage 4: Deep dive ──────────────────────────────────────────────────────
class DeepDiveSourceItem(BaseModel):
    """One source's contribution to a deep dive (fetched + extracted)."""

    source_key: str = ""
    source_name: str = ""
    link: str = ""
    title: str = ""
    published_at: str | None = None
    fetch_status: str = "pending"     # pending | success | failed
    extract_status: str = "pending"
    word_count: int = 0
    cleaned_full_text: str = ""
    excerpt: str = ""
    quotes: list[str] = Field(default_factory=list)
    error: str | None = None


class EventDeepDive(BaseModel):
    """The research pack for one event: facts, quotes, timeline, worthiness.

    Populated by rule-based extraction over fetched source full text (the old
    project does NOT call an LLM for the core deep dive). The writing guide is
    attached so the external AI knows the house style when authoring.
    """

    id: str
    event_id: str
    status: DeepDiveStatus = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    attempted_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    resolved_evidence_pack: list[dict[str, Any]] = Field(default_factory=list)
    full_text_sources: list[DeepDiveSourceItem] = Field(default_factory=list)
    sources: list[DeepDiveSourceItem] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    worthiness: dict[str, Any] = Field(default_factory=dict)  # {worth_to_brief, reason}
    article_writing_guide: str = ""    # house style guide threaded to the AI
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


# ── Response envelopes ──────────────────────────────────────────────────────
class SourceListResponse(BaseModel):
    items: list[Source]
    total: int


class SourceResponse(BaseModel):
    item: Source


class RawItemListResponse(BaseModel):
    items: list[RawItem]
    total: int


class IntelEventListResponse(BaseModel):
    items: list[IntelEvent]
    total: int


class IntelEventResponse(BaseModel):
    item: IntelEvent


class IntelAlertListResponse(BaseModel):
    items: list[IntelAlert]
    total: int


class DeepDiveResponse(BaseModel):
    item: EventDeepDive


class DeepDiveListResponse(BaseModel):
    items: list[EventDeepDive]
    total: int
