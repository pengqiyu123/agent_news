"""Intel routes — read surface for the information radar.

These are GET endpoints so the agent (or any client) can inspect sources, raw
items, events, alerts, and deep dives. Mutations happen through the operations
registry (POST /api/operations/...), keeping a clean read/write separation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db.intel_repository import get_intel_repository
from ..models.intel import (
    DeepDiveListResponse,
    DeepDiveResponse,
    IntelAlertListResponse,
    IntelEventListResponse,
    IntelEventResponse,
    RawItemListResponse,
    SourceListResponse,
    SourceResponse,
)
from ..models.intel import Source

router = APIRouter(tags=["intel"])


# ── Sources ─────────────────────────────────────────────────────────────────
@router.get("/api/intel/sources", response_model=SourceListResponse)
def list_sources(enabled_only: bool = False) -> SourceListResponse:
    repo = get_intel_repository()
    items = repo.list_sources(enabled_only=enabled_only)
    return SourceListResponse(items=items, total=len(items))


@router.get("/api/intel/sources/{key}", response_model=SourceResponse)
def get_source(key: str) -> SourceResponse:
    repo = get_intel_repository()
    source = repo.get_source(key)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source '{key}' not found")
    return SourceResponse(item=source)


# ── Raw items ───────────────────────────────────────────────────────────────
@router.get("/api/intel/raw-items", response_model=RawItemListResponse)
def list_raw_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> RawItemListResponse:
    repo = get_intel_repository()
    offset = (page - 1) * page_size
    items, total = repo.list_raw_items(limit=page_size, offset=offset)
    return RawItemListResponse(items=items, total=total)


# ── Events ──────────────────────────────────────────────────────────────────
@router.get("/api/intel/events", response_model=IntelEventListResponse)
def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ignored: bool | None = False,
    min_score: float | None = None,
) -> IntelEventListResponse:
    """List events, by default excluding ignored ones, ordered by composite score."""
    repo = get_intel_repository()
    offset = (page - 1) * page_size
    items, total = repo.list_events(limit=page_size, offset=offset, ignored=ignored, min_score=min_score)
    return IntelEventListResponse(items=items, total=total)


@router.get("/api/intel/events/{event_id}", response_model=IntelEventResponse)
def get_event(event_id: str) -> IntelEventResponse:
    repo = get_intel_repository()
    event = repo.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return IntelEventResponse(item=event)


# ── Alerts ──────────────────────────────────────────────────────────────────
@router.get("/api/intel/alerts", response_model=IntelAlertListResponse)
def list_alerts(limit: int = Query(50, ge=1, le=200)) -> IntelAlertListResponse:
    repo = get_intel_repository()
    items, total = repo.list_alerts(limit=limit)
    return IntelAlertListResponse(items=items, total=total)


# ── Deep dives ──────────────────────────────────────────────────────────────
@router.get("/api/intel/deep-dives", response_model=DeepDiveListResponse)
def list_deep_dives(limit: int = Query(50, ge=1, le=200)) -> DeepDiveListResponse:
    repo = get_intel_repository()
    items, total = repo.list_deep_dives(limit=limit)
    return DeepDiveListResponse(items=items, total=total)


@router.get("/api/intel/deep-dives/{dive_id}", response_model=DeepDiveResponse)
def get_deep_dive(dive_id: str) -> DeepDiveResponse:
    repo = get_intel_repository()
    dive = repo.get_deep_dive(dive_id)
    if dive is None:
        raise HTTPException(status_code=404, detail=f"Deep dive '{dive_id}' not found")
    return DeepDiveResponse(item=dive)


@router.get("/api/intel/events/{event_id}/deep-dive", response_model=DeepDiveResponse)
def get_event_deep_dive(event_id: str) -> DeepDiveResponse:
    """Get the most recent deep dive for an event, if any."""
    repo = get_intel_repository()
    dive = repo.get_deep_dive_by_event(event_id)
    if dive is None:
        raise HTTPException(status_code=404, detail=f"No deep dive for event '{event_id}'")
    return DeepDiveResponse(item=dive)
