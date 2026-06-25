"""Score — hotness scoring + event materialization.

Stage 3. Computes the composite hotness score for each cluster and turns
clusters into IntelEvent objects:

    composite = velocity*0.28 + coverage*0.22 + freshness*0.20 + audience_fit*0.30

Plus alert_state classification (new/rising/hot/cooling/cold) and alert_reason.

Pure functions — given a cluster + previous events (for velocity delta), return
a scored IntelEvent. The agent can re-score after new items arrive without
re-clustering if it wants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..models.intel import (
    DiscoveryItem,
    IntelAlert,
    IntelEvent,
    Source,
)
from .cluster import event_id_for_cluster, representative_item


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # Normalize to UTC-aware — SQLite round-trips can drop tzinfo.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Individual score components ─────────────────────────────────────────────
def velocity_score(
    member_count: int,
    previous_member_count: int | None,
    platform_count: int,
    previous_platform_count: int | None,
) -> tuple[float, dict[str, float]]:
    """Growth momentum. Returns (score 0-100, details dict)."""
    prev_members = previous_member_count if previous_member_count is not None else 0
    prev_platforms = previous_platform_count if previous_platform_count is not None else 0

    member_growth = max(0.0, member_count - prev_members)
    platform_growth = max(0.0, platform_count - prev_platforms)

    # Saturating growth: each new member adds diminishing points.
    member_pts = min(60.0, member_growth * 20.0)
    platform_pts = min(40.0, platform_growth * 20.0)
    score = member_pts + platform_pts

    details = {
        "member_delta": float(member_growth),
        "platform_delta": float(platform_growth),
        "previous_member_count": float(prev_members),
        "previous_platform_count": float(prev_platforms),
    }
    return score, details


def coverage_score(source_count: int, platform_count: int) -> float:
    """Breadth of coverage across sources and platforms. 0-100."""
    # Saturating: 3+ sources ≈ full marks; 3+ platforms likewise.
    src_pts = min(60.0, source_count * 20.0)
    plat_pts = min(40.0, platform_count * 13.3)
    return src_pts + plat_pts


def freshness_score(published_at: str | None, now: datetime | None = None) -> float:
    """Recency. Full marks within 1h, decaying to 0 after 72h. 0-100."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    pub = _parse_dt(published_at)
    if pub is None:
        return 30.0  # unknown time — neutral
    # _parse_dt already normalizes to aware, but guard once more.
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_hours = abs((now - pub).total_seconds()) / 3600.0
    if age_hours <= 1:
        return 100.0
    if age_hours >= 72:
        return 0.0
    # Linear decay 1h→72h.
    return max(0.0, 100.0 * (1 - (age_hours - 1) / 71))


def audience_fit_score(
    tags: Iterable[str],
    anchor_tokens: Iterable[str],
    entity_names: Iterable[str],
    watchlist: Iterable[str] = (),
) -> float:
    """Match against the user's entity/tag watchlist. 0-100."""
    watch = {w.lower() for w in watchlist if w}
    if not watch:
        return 50.0  # no watchlist configured — neutral
    pool = {t.lower() for t in tags} | {a.lower() for a in anchor_tokens} | {e.lower() for e in entity_names}
    hits = pool & watch
    if not hits:
        return 30.0
    # Each hit adds points, saturating at 3.
    return min(100.0, 60.0 + len(hits) * 15.0)


# ── Weights ─────────────────────────────────────────────────────────────────
W_VELOCITY = 0.28
W_COVERAGE = 0.22
W_FRESHNESS = 0.20
W_AUDIENCE = 0.30


def composite(vel: float, cov: float, fre: float, aud: float) -> float:
    return vel * W_VELOCITY + cov * W_COVERAGE + fre * W_FRESHNESS + aud * W_AUDIENCE


# ── Alert state classification ──────────────────────────────────────────────
def classify_alert_state(
    composite_val: float,
    velocity_val: float,
    member_delta: int,
    previous_state: str | None = None,
) -> tuple[str, str]:
    """Return (alert_state, alert_reason).

    Thresholds:
    - hot: high composite AND high velocity
    - rising: meaningful growth delta
    - cooling: was hot/rising but composite dropped
    - cold: low everything
    - new: default for first sighting
    """
    if composite_val >= 70 and velocity_val >= 40:
        return "hot", f"composite={composite_val:.0f} velocity={velocity_val:.0f}"
    if member_delta > 0 or velocity_val >= 30:
        return "rising", f"member_delta=+{member_delta} velocity={velocity_val:.0f}"
    if previous_state in ("hot", "rising") and composite_val < 40:
        return "cooling", f"was {previous_state}, now composite={composite_val:.0f}"
    if composite_val < 25:
        return "cold", "low composite score"
    return "new", "newly sighted"


def change_state(previous: IntelEvent | None, current_composite: float, member_delta: int) -> str:
    """Classify how the event changed vs its previous snapshot."""
    if previous is None:
        return "new_event"
    if member_delta > 0:
        return "growing_event"
    if previous.composite_score > 0 and current_composite < previous.composite_score * 0.8:
        return "declining_event"
    return "stable_event"


# ── Full event materialization ──────────────────────────────────────────────
def score_event(
    cluster: list[DiscoveryItem],
    event_id: str,
    sources_by_key: dict[str, Source] | None = None,
    previous: IntelEvent | None = None,
    watchlist: Iterable[str] = (),
    now: datetime | None = None,
) -> IntelEvent:
    """Score one cluster into a fully-populated IntelEvent.

    Args:
        cluster: discovery items grouped as one event (from cluster_discovery_items).
        event_id: deterministic id from event_id_for_cluster().
        sources_by_key: source lookup for names/weights.
        previous: prior IntelEvent with the same id (for velocity delta), if any.
        watchlist: user's entity/tag watchlist for audience_fit scoring.
        now: override "now" for freshness (testing / replay).
    """
    sources_by_key = sources_by_key or {}
    now = now or datetime.now(timezone.utc)

    rep = representative_item(cluster, sources_by_key)
    source_keys = sorted({item.source_key for item in cluster if item.source_key})
    source_names = sorted({item.source_name for item in cluster if item.source_name})
    platforms = sorted({item.metadata.get("platform", "web") for item in cluster if item.metadata.get("platform")})
    platforms = platforms or ["web"]
    member_count = len(cluster)
    source_count = len(source_keys)
    platform_count = len(platforms)
    published_times = [t for t in (_parse_dt(item.published_at or item.collected_at) for item in cluster) if t]
    published_at = min(published_times).isoformat() if published_times else None
    first_seen = min(published_times).isoformat() if published_times else None
    last_seen = max(published_times).isoformat() if published_times else None
    all_tags = sorted({t for item in cluster for t in item.tags})
    all_anchors = sorted({a for item in cluster for a in item.anchor_tokens})
    all_entities = sorted({e for item in cluster for e in item.entity_names})

    prev_members = previous.member_count if previous else None
    prev_platforms = previous.platform_count if previous else None

    vel, vel_details = velocity_score(member_count, prev_members, platform_count, prev_platforms)
    cov = coverage_score(source_count, platform_count)
    fre = freshness_score(published_at, now)
    aud = audience_fit_score(all_tags, all_anchors, all_entities, watchlist)
    comp = composite(vel, cov, fre, aud)
    member_delta = max(0, member_count - (prev_members or 0))
    platform_delta = max(0, platform_count - (prev_platforms or 0))

    alert_state, alert_reason = classify_alert_state(
        comp, vel, member_delta, previous.alert_state if previous else None
    )
    chg = change_state(previous, comp, member_delta)

    return IntelEvent(
        id=event_id,
        title=rep.title,
        summary=rep.summary or rep.title,
        representative_link=rep.link,
        discovery_item_ids=[item.id for item in cluster],
        source_keys=source_keys,
        source_names=source_names,
        platforms=platforms,
        platform_count=platform_count,
        source_count=source_count,
        member_count=member_count,
        story_count=member_count,
        member_delta=member_delta,
        platform_delta=platform_delta,
        published_at=published_at,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        tags=all_tags,
        anchor_tokens=all_anchors,
        velocity_score=round(vel, 2),
        coverage_score=round(cov, 2),
        freshness_score=round(fre, 2),
        audience_fit_score=round(aud, 2),
        composite_score=round(comp, 2),
        velocity_details=vel_details,
        alert_state=alert_state,
        change_state=chg,
        alert_reason=alert_reason,
        entity_names=all_entities,
        watchlisted=bool(previous and previous.watchlisted),
        ignored=bool(previous and previous.ignored),
        created_at=previous.created_at if previous else now.isoformat(),
        updated_at=now.isoformat(),
    )


def build_events_from_clusters(
    clusters: list[list[DiscoveryItem]],
    sources_by_key: dict[str, Source] | None = None,
    previous_events: dict[str, IntelEvent] | None = None,
    watchlist: Iterable[str] = (),
    now: datetime | None = None,
) -> list[IntelEvent]:
    """Score every cluster into an event, carrying forward previous state.

    previous_events maps event_id → prior IntelEvent for velocity delta.
    """
    previous_events = previous_events or {}
    events: list[IntelEvent] = []
    for cluster in clusters:
        eid = event_id_for_cluster(cluster)
        prev = previous_events.get(eid)
        events.append(score_event(cluster, eid, sources_by_key, prev, watchlist, now))
    return events


def materialize_alerts(events: list[IntelEvent], threshold: float = 50.0) -> list[IntelAlert]:
    """Turn high-scoring events into alerts for the agent's attention.

    Only events at/above threshold OR in rising/hot state become alerts.
    """
    import uuid

    out: list[IntelAlert] = []
    for evt in events:
        if evt.composite_score < threshold and evt.alert_state not in ("rising", "hot"):
            continue
        out.append(
            IntelAlert(
                id=f"alert-{uuid.uuid4().hex[:10]}",
                event_id=evt.id,
                title=evt.title,
                summary=evt.summary,
                alert_state=evt.alert_state,
                alert_reason=evt.alert_reason,
                velocity_score=evt.velocity_score,
                coverage_score=evt.coverage_score,
                freshness_score=evt.freshness_score,
                audience_fit_score=evt.audience_fit_score,
                composite_score=evt.composite_score,
                platform_count=evt.platform_count,
                source_count=evt.source_count,
                representative_link=evt.representative_link,
                entity_names=evt.entity_names,
            )
        )
    return out
