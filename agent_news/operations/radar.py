"""Radar atomic operations — the information radar as agent-selectable steps.

Each stage of the radar (collect → normalize → cluster → score → deep-dive) is
an independently-callable operation registered in OPERATION_REGISTRY. The agent
can run any one alone, or chain them via the batch endpoint.

This is the direct realization of the project's core principle: the old project
fused these into one build_intel_state call driven by a scheduler; here every
stage is its own operation that fails or succeeds in isolation.

All operations are pure-of-side-effect except DB writes (via intel repository)
and network fetches (RSS / deep-dive source URLs). They never raise for
expected business failures — they return OperationResult(status=failed).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db.intel_repository import get_intel_repository
from ..intel.connectors import collect_sources, fetch_source
from ..intel.normalize import normalize_raw_items
from ..intel.cluster import cluster_discovery_items, event_id_for_cluster
from ..intel.score import build_events_from_clusters, materialize_alerts
from ..intel.deep_dive import build_deep_dive
from ..intel.writing_guide import build_article_writing_guide
from ..models.intel import RawItem
from ..models.operation import OperationResult
from .base import operation


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Stage 1: collect ────────────────────────────────────────────────────────
@operation(
    name="radar.sync_sources",
    category="radar",
    description=(
        "采集：从所有已启用的源拉取最新内容，写入 raw_items。"
        "可指定 source_key 只同步单个源。"
    ),
    params={"source_key": "可选，单个源 key；省略则同步全部"},
)
def sync_sources(ctx, source_key: str | None = None) -> OperationResult:
    """Fetch from enabled sources and persist raw items."""
    repo = get_intel_repository()
    sources = repo.list_sources(enabled_only=True)

    if source_key:
        sources = [s for s in sources if s.key == source_key]
        if not sources:
            return OperationResult.failure(message=f"source '{source_key}' not found or disabled")

    raw_items = collect_sources(sources)
    # assign ids if missing
    for item in raw_items:
        if not item.id:
            import uuid
            item.id = f"raw-{uuid.uuid4().hex[:12]}"
    repo.add_raw_items(raw_items)

    return OperationResult.success(
        message=f"synced {len(raw_items)} raw items from {len(sources)} sources",
        raw_count=len(raw_items),
        source_count=len(sources),
        synced_at=_utcnow(),
    )


@operation(
    name="radar.sync_one_source",
    category="radar",
    description="采集单个源（按 key）。失败不影响其他源。",
    params={"source_key": "必填，要同步的源 key"},
)
def sync_one_source(ctx, source_key: str) -> OperationResult:
    return sync_sources(ctx, source_key=source_key)


# ── Stage 1b → 3: cluster + score (one operation, the common case) ──────────
@operation(
    name="radar.build_events",
    category="radar",
    description=(
        "聚类+打分：把当前 raw_items 归并成事件、计算热度分数、物化 alerts。"
        "可调节 merge_threshold 和 alert_threshold。"
    ),
    params={
        "merge_threshold": "聚类合并阈值 0-1，默认 0.34",
        "alert_threshold": "alert 物化分数线，默认 50",
        "watchlist": "受众匹配 watchlist，逗号分隔或列表",
        "clear_raw": "是否在聚类后清空 raw_items，默认 True",
    },
)
def build_events(
    ctx,
    merge_threshold: float = 0.34,
    alert_threshold: float = 50.0,
    watchlist=None,
    clear_raw: bool = True,
) -> OperationResult:
    """Normalize → cluster → score → persist events + alerts."""
    repo = get_intel_repository()

    # normalize watchlist arg (accept comma string or list)
    if watchlist is None:
        watchlist = []
    elif isinstance(watchlist, str):
        watchlist = [w.strip() for w in watchlist.split(",") if w.strip()]

    raw_items, raw_total = repo.list_raw_items(limit=10000)
    if raw_total == 0:
        return OperationResult.skip(message="no raw items to cluster; run radar.sync_sources first")

    sources = {s.key: s for s in repo.list_sources()}
    discovery_items = normalize_raw_items(raw_items, sources)
    clusters = cluster_discovery_items(discovery_items, merge_threshold=merge_threshold)

    # carry forward previous event state for velocity delta
    previous_events_list, _ = repo.list_events(limit=10000, ignored=None)
    previous_events = {e.id: e for e in previous_events_list}

    events = build_events_from_clusters(
        clusters,
        sources_by_key=sources,
        previous_events=previous_events,
        watchlist=watchlist,
    )
    # persist events (upsert by deterministic id)
    repo.upsert_events(events)

    # materialize + persist alerts
    repo.clear_alerts()
    alerts = materialize_alerts(events, threshold=alert_threshold)
    if alerts:
        repo.add_alerts(alerts)

    # optionally clear raw items (they've been folded into events)
    cleared = 0
    if clear_raw:
        cleared = repo.clear_raw_items()

    # update deep-dive linkage worthiness on events
    hot_count = sum(1 for e in events if e.alert_state in ("hot", "rising"))

    return OperationResult.success(
        message=(
            f"clustered {len(discovery_items)} items into {len(events)} events "
            f"({hot_count} hot/rising, {len(alerts)} alerts)"
        ),
        discovery_count=len(discovery_items),
        event_count=len(events),
        alert_count=len(alerts),
        hot_count=hot_count,
        raw_cleared=cleared,
        built_at=_utcnow(),
    )


# ── Stage 4: deep dive ──────────────────────────────────────────────────────
@operation(
    name="radar.deep_dive_event",
    category="radar",
    description=(
        "深挖：抓取事件来源全文，提取事实/引文/时间线，附写作指南。"
        "不调用 LLM——这是给外部 AI 写作用的素材包。"
    ),
    params={
        "event_id": "必填，要深挖的事件 id",
        "max_sources": "最多抓取多少来源，默认 6",
        "force": "是否强制重新深挖（即使已有结果），默认 False",
    },
)
def deep_dive_event(ctx, event_id: str, max_sources: int = 6, force: bool = False) -> OperationResult:
    """Build (or fetch cached) deep dive for one event."""
    repo = get_intel_repository()
    event = repo.get_event(event_id)
    if event is None:
        return OperationResult.failure(message=f"event '{event_id}' not found")

    if not force:
        existing = repo.get_deep_dive_by_event(event_id)
        if existing and existing.status in ("ready", "partial"):
            return OperationResult.success(
                message=f"deep dive already exists (status={existing.status})",
                deep_dive_id=existing.id,
                cached=True,
                fact_count=len(existing.facts),
                source_count=existing.success_count,
            )

    # rebuild discovery items from the event's stored item ids + a fresh fetch
    # (we store discovery item data inside the event, so reconstruct minimal items)
    from ..models.intel import DiscoveryItem

    discovery_items = [
        DiscoveryItem(
            id=item_id,
            source_key=sk,
            source_name=sn,
            title=event.title,
            link=event.representative_link,
        )
        for item_id, sk, sn in zip(
            event.discovery_item_ids,
            event.source_keys or [],
            event.source_names or [],
        )
    ]
    # if we have fewer links than item ids, pad with representative link
    while len(discovery_items) < len(event.discovery_item_ids):
        discovery_items.append(
            DiscoveryItem(id="pad", title=event.title, link=event.representative_link)
        )

    guide = build_article_writing_guide()
    dive = build_deep_dive(event, discovery_items, article_writing_guide=guide, max_sources=max_sources)
    repo.upsert_deep_dive(dive)

    # link deep dive back to event
    event.deep_dive_id = dive.id
    event.deep_dive_status = dive.status
    event.deep_dive_summary = dive.worthiness.get("reason", "")
    event.worth_to_brief = dive.worthiness.get("worth_to_brief", False)
    event.worth_reason = dive.worthiness.get("reason", "")
    repo.upsert_event(event)

    return OperationResult.success(
        message=f"deep dive {dive.status}: {dive.success_count}/{dive.attempted_count} sources, "
                f"{len(dive.facts)} facts, worth_to_brief={event.worth_to_brief}",
        deep_dive_id=dive.id,
        status=dive.status,
        fact_count=len(dive.facts),
        quote_count=len(dive.quotes),
        timeline_count=len(dive.timeline),
        source_count=dive.success_count,
        worth_to_brief=event.worth_to_brief,
        reason=event.worth_reason,
    )


# ── Stage 1+: source management ─────────────────────────────────────────────
@operation(
    name="radar.seed_defaults",
    category="radar",
    description="初始化默认源（仅当当前无任何源时生效）。幂等。",
    params={},
)
def seed_defaults(ctx) -> OperationResult:
    from ..intel.defaults import seed_default_sources

    repo = get_intel_repository()
    count = seed_default_sources(repo)
    if count == 0:
        return OperationResult.skip(message="sources already exist; nothing seeded")
    return OperationResult.success(message=f"seeded {count} default sources", seeded=count)


@operation(
    name="radar.add_source",
    category="radar",
    description="添加一个信息源。",
    params={
        "key": "必填，唯一 key",
        "source_name": "显示名",
        "url": "RSS URL",
        "kind": "源类型，默认 rss",
        "tags": "标签列表，逗号分隔或列表",
        "priority": "优先级 0-100，默认 50",
    },
)
def add_source(ctx, key: str, source_name: str = "", url: str = "", kind: str = "rss",
               tags=None, priority: int = 50) -> OperationResult:
    from ..models.intel import Source

    if tags is None:
        tags = []
    elif isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    repo = get_intel_repository()
    if repo.get_source(key) is not None:
        return OperationResult.failure(message=f"source '{key}' already exists")
    source = Source(key=key, name=source_name or key, kind=kind, url=url, tags=tags, priority=priority)
    repo.upsert_source(source)
    return OperationResult.success(message=f"added source '{key}'", source_key=key)


@operation(
    name="radar.remove_source",
    category="radar",
    description="删除一个信息源。",
    params={"source_key": "必填，要删除的源 key"},
)
def remove_source(ctx, source_key: str) -> OperationResult:
    repo = get_intel_repository()
    if not repo.delete_source(source_key):
        return OperationResult.failure(message=f"source '{source_key}' not found")
    return OperationResult.success(message=f"removed source '{source_key}'", source_key=source_key)
