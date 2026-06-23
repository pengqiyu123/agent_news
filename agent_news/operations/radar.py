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
from ..intel.normalize import normalize_raw_items
from ..intel.cluster import cluster_discovery_items
from ..intel.score import build_events_from_clusters, materialize_alerts
from ..intel.deep_dive import build_deep_dive
from ..intel.writing_guide import build_article_writing_guide
from ..intel.review import (
    build_event_review,
    build_radar_status,
    review_deep_dive_state,
    summarize_event,
)
from ..intel.source_discovery import (
    dedupe_source,
    normalize_candidates,
    proposal_from_validation,
    validate_source_candidate,
)
from ..intel.source_probe import probe_source
from ..models.intel import RawItem
from ..models.operation import OperationResult
from .base import operation


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Stage 1: collect ────────────────────────────────────────────────────────
@operation(
    name="radar.status",
    category="radar",
    description="只读：查看信息雷达源、raw、events、alerts、deep dives 数量和建议下一步。",
    params={"include_recent": "是否返回最近事件/alerts 摘要，默认 True"},
)
def radar_status(ctx, include_recent: bool = True) -> OperationResult:
    repo = get_intel_repository()
    sources = repo.list_sources()
    raw_items, raw_total = repo.list_raw_items(limit=5)
    events, event_total = repo.list_events(limit=5, ignored=False)
    alerts, alert_total = repo.list_alerts(limit=5)
    deep_dives, deep_dive_total = repo.list_deep_dives(limit=5)
    state = build_radar_status(
        sources=sources,
        raw_items=raw_items,
        raw_total=raw_total,
        events=events,
        event_total=event_total,
        alerts=alerts,
        alert_total=alert_total,
        deep_dives=deep_dives,
        deep_dive_total=deep_dive_total,
        include_recent=include_recent,
    )
    return OperationResult.success(message="radar status ready", **state)


@operation(
    name="radar.review_sources",
    category="radar",
    description="只读/可探测：复核源配置和健康状态，probe=false 时不联网。",
    params={
        "probe": "是否实际请求源做健康探测，默认 False",
        "limit_per_source": "探测时最多返回几条样例，默认 3",
        "source_key": "可选，只检查单个源",
    },
)
def review_sources(ctx, probe: bool = False, limit_per_source: int = 3, source_key: str = "") -> OperationResult:
    repo = get_intel_repository()
    sources = repo.list_sources()
    if source_key:
        sources = [source for source in sources if source.key == source_key]
        if not sources:
            return OperationResult.failure(message=f"source '{source_key}' not found")

    rows = []
    ok_count = failed_count = disabled_count = 0
    for source in sources:
        row = {
            "key": source.key,
            "name": source.name,
            "enabled": source.enabled,
            "kind": source.kind,
            "url": source.url,
            "tags": source.tags,
            "priority": source.priority,
            "probe_status": "not_run",
            "probe_count": 0,
            "error": None,
            "sample_items": [],
        }
        if not source.enabled:
            disabled_count += 1
            row["probe_status"] = "disabled"
        elif probe:
            result = probe_source(source, limit_per_source=limit_per_source)
            row["probe_status"] = result.status
            row["probe_count"] = result.item_count
            row["error"] = result.error
            row["sample_items"] = result.sample_items or []
            if result.status == "ok":
                ok_count += 1
            else:
                failed_count += 1
        rows.append(row)
    if not probe:
        ok_count = sum(1 for source in sources if source.enabled)

    return OperationResult.success(
        message=f"reviewed {len(rows)} sources",
        sources=rows,
        source_count=len(rows),
        ok_count=ok_count,
        failed_count=failed_count,
        disabled_count=disabled_count,
        probed=probe,
    )


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

    raw_items: list[RawItem] = []
    source_results = []
    failed_source_count = 0
    for source in sources:
        probe = probe_source(source, limit_per_source=3, include_items=True)
        items = probe.items or []
        raw_items.extend(items)
        if probe.status != "ok":
            failed_source_count += 1
        source_results.append(
            {
                "source_key": source.key,
                "source_name": source.name or source.key,
                "status": "ok" if probe.status == "ok" else probe.status,
                "raw_count": len(items),
                "error": probe.error,
                "sample_items": probe.sample_items or [],
            }
        )
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
        source_results=source_results,
        partial=failed_source_count > 0 and len(raw_items) > 0,
        failed_source_count=failed_source_count,
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
    top_events = [
        summarize_event(event)
        for event in sorted(events, key=lambda item: item.composite_score, reverse=True)[:5]
    ]

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
        top_events=top_events,
        suggested_next_operation="radar.review_events",
        built_at=_utcnow(),
    )


@operation(
    name="radar.review_events",
    category="radar",
    description="只读：返回 Top events、真实推荐理由、风险和下一步 deep dive 建议。",
    params={
        "limit": "返回事件数量，默认 10",
        "min_score": "最低综合分，默认 0",
        "include_ignored": "是否包含 ignored 事件，默认 False",
        "watchlist": "临时关注词，逗号分隔",
    },
)
def review_events(
    ctx,
    limit: int = 10,
    min_score: float = 0,
    include_ignored: bool = False,
    watchlist: str = "",
) -> OperationResult:
    repo = get_intel_repository()
    ignored = None if include_ignored else False
    events, total = repo.list_events(limit=max(1, int(limit)), ignored=ignored, min_score=min_score)
    state = build_event_review(events, total=total, watchlist=watchlist)
    if not events:
        state["suggested_next_operation"] = "radar.sync_sources"
        return OperationResult.success(message="no events available; run radar.sync_sources then radar.build_events", **state)
    return OperationResult.success(message=f"reviewed {len(events)} events", **state)


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
            review_state = review_deep_dive_state(existing, event)
            for key in ("deep_dive_id", "fact_count", "source_count"):
                review_state.pop(key, None)
            return OperationResult.success(
                message=f"deep dive already exists (status={existing.status})",
                deep_dive_id=existing.id,
                cached=True,
                fact_count=len(existing.facts),
                source_count=existing.success_count,
                **review_state,
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
    review_state = review_deep_dive_state(dive, event)
    review_state.pop("deep_dive_id", None)
    review_state.pop("status", None)
    review_state.pop("fact_count", None)
    review_state.pop("quote_count", None)
    review_state.pop("timeline_count", None)
    review_state.pop("source_count", None)

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
        **review_state,
    )


@operation(
    name="radar.review_deep_dive",
    category="radar",
    description="只读：复核已深挖素材、来源成功/失败和写作准备度。",
    params={"event_id": "可选，按事件查最近 deep dive", "deep_dive_id": "可选，直接查 deep dive"},
)
def review_deep_dive(ctx, event_id: str = "", deep_dive_id: str = "") -> OperationResult:
    repo = get_intel_repository()
    if deep_dive_id:
        dive = repo.get_deep_dive(deep_dive_id)
    elif event_id:
        dive = repo.get_deep_dive_by_event(event_id)
    else:
        return OperationResult.failure(message="event_id or deep_dive_id is required")
    if dive is None:
        return OperationResult.skip(
            message="deep dive not found; run radar.deep_dive_event first",
            event_id=event_id,
            deep_dive_id=deep_dive_id,
            suggested_next_operation="radar.deep_dive_event",
        )
    event = repo.get_event(dive.event_id)
    state = review_deep_dive_state(dive, event)
    return OperationResult.success(message=f"deep dive review: {state['writing_readiness']}", **state)


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
    name="radar.discover_sources",
    category="radar",
    description="候选源发现：本轮支持外部 Agent 传 candidates，项目只规范化候选，不写库。",
    params={
        "candidates": "候选 URL 字符串或对象数组",
        "query": "搜索关键词，仅记录，不触发项目内搜索",
        "topic": "主题标签",
        "kind": "源类型，默认 rss",
        "language": "语言偏好",
        "limit": "最多返回候选数",
    },
)
def discover_sources(
    ctx,
    candidates=None,
    query: str = "",
    topic: str = "",
    kind: str = "rss",
    language: str = "",
    limit: int = 10,
) -> OperationResult:
    normalized = normalize_candidates(
        candidates or [],
        query=query,
        topic=topic,
        kind=kind,
        language=language,
        limit=limit,
    )
    return OperationResult.success(
        message=f"discovered {len(normalized)} source candidates",
        candidates=normalized,
        count=len(normalized),
        suggested_next_operation="radar.validate_source",
    )


@operation(
    name="radar.validate_source",
    category="radar",
    description="验证候选源是否可访问、可解析、近期有内容、未重复，并给出质量评分。",
    params={"url": "候选源 URL", "kind": "源类型，默认 rss", "topic": "主题标签", "limit_per_source": "最多读取样例数"},
)
def validate_source(ctx, url: str, kind: str = "rss", topic: str = "", limit_per_source: int = 5) -> OperationResult:
    repo = get_intel_repository()
    result = validate_source_candidate(
        url=url,
        kind=kind,
        topic=topic,
        limit_per_source=limit_per_source,
        existing_sources=repo.list_sources(),
    )
    status = result.get("decision")
    message = f"source validation {status}: {result.get('reason', '')}"
    return OperationResult.success(message=message, **result)


@operation(
    name="radar.propose_source",
    category="radar",
    description="把 validate_source 结果整理成添加建议，不写库。",
    params={"validated_source": "radar.validate_source 返回的 state"},
)
def propose_source(ctx, validated_source: dict | None = None) -> OperationResult:
    if not isinstance(validated_source, dict):
        return OperationResult.failure(message="validated_source dict is required")
    proposal = proposal_from_validation(validated_source)
    return OperationResult.success(message=f"source proposal: {proposal['decision']}", **proposal)


@operation(
    name="radar.add_validated_source",
    category="radar",
    description="只添加通过验证的候选源；needs_confirmation 需 confirmed=true。",
    params={"validated_source": "validate/propose 返回的 state", "confirmed": "是否确认添加 needs_confirmation 候选"},
)
def add_validated_source(ctx, validated_source: dict | None = None, confirmed: bool = False) -> OperationResult:
    from ..models.intel import Source

    if not isinstance(validated_source, dict):
        return OperationResult.failure(message="validated_source dict is required")
    source_data = dict(validated_source.get("suggested_source") or {})
    decision = str(validated_source.get("decision") or "")
    valid = bool(validated_source.get("valid"))
    if not valid or decision == "reject":
        return OperationResult.failure(message="candidate is not valid; refusing to add")
    if decision == "needs_confirmation" and not confirmed:
        return OperationResult.failure(message="candidate needs confirmation; pass confirmed=true")
    if decision not in ("auto_add", "needs_confirmation", "confirmed"):
        return OperationResult.failure(message=f"unsupported decision '{decision}'")
    if not source_data.get("key") or not source_data.get("url"):
        return OperationResult.failure(message="suggested_source requires key and url")

    repo = get_intel_repository()
    duplicate = dedupe_source(str(source_data["url"]), repo.list_sources())
    if duplicate.get("duplicate"):
        return OperationResult.failure(
            message=f"duplicate source: {duplicate.get('matched_source_key')}",
            dedupe=duplicate,
        )
    if repo.get_source(str(source_data["key"])) is not None:
        return OperationResult.failure(message=f"source key '{source_data['key']}' already exists")

    source = Source(**source_data)
    repo.upsert_source(source)
    return OperationResult.success(
        message=f"added validated source '{source.key}'",
        source_key=source.key,
        source=source.model_dump(),
        suggested_next_operation="radar.sync_one_source",
        suggested_params={"source_key": source.key},
    )


@operation(
    name="radar.source_health_report",
    category="radar",
    description="只读：汇总源池健康度、低贡献源、疑似重复源和建议动作。",
    params={},
)
def source_health_report(ctx) -> OperationResult:
    repo = get_intel_repository()
    sources = repo.list_sources()
    raw_items, _ = repo.list_raw_items(limit=10000)
    raw_by_source: dict[str, int] = {}
    for item in raw_items:
        raw_by_source[item.source_key] = raw_by_source.get(item.source_key, 0) + 1
    domains: dict[str, list[str]] = {}
    for source in sources:
        from urllib.parse import urlparse

        parsed = urlparse(source.url if "://" in source.url else f"https://{source.url}")
        domain = (parsed.netloc or parsed.path).lower().removeprefix("www.")
        domains.setdefault(domain, []).append(source.key)
    duplicate_groups = [
        {"domain": domain, "source_keys": keys}
        for domain, keys in domains.items()
        if domain and len(keys) > 1
    ]
    rows = []
    for source in sources:
        raw_count = raw_by_source.get(source.key, 0)
        recommendation = "keep"
        if not source.enabled:
            recommendation = "disabled"
        elif raw_count == 0:
            recommendation = "review_or_probe"
        rows.append(
            {
                "key": source.key,
                "name": source.name,
                "enabled": source.enabled,
                "kind": source.kind,
                "url": source.url,
                "raw_item_count": raw_count,
                "recommendation": recommendation,
            }
        )
    return OperationResult.success(
        message=f"source health report for {len(sources)} sources",
        sources=rows,
        duplicate_groups=duplicate_groups,
        high_contribution_sources=sorted(rows, key=lambda r: r["raw_item_count"], reverse=True)[:5],
        low_contribution_sources=[row for row in rows if row["enabled"] and row["raw_item_count"] == 0],
    )


@operation(
    name="radar.disable_stale_sources",
    category="radar",
    description="停用长期无贡献源；默认 dry_run=true，只返回将要停用的源。",
    params={"dry_run": "默认 True", "min_raw_items": "低于该 raw item 数视为 stale，默认 1"},
)
def disable_stale_sources(ctx, dry_run: bool = True, min_raw_items: int = 1) -> OperationResult:
    repo = get_intel_repository()
    sources = repo.list_sources()
    raw_items, _ = repo.list_raw_items(limit=10000)
    raw_by_source: dict[str, int] = {}
    for item in raw_items:
        raw_by_source[item.source_key] = raw_by_source.get(item.source_key, 0) + 1
    stale = [
        source
        for source in sources
        if source.enabled and raw_by_source.get(source.key, 0) < min_raw_items
    ]
    disabled = []
    if not dry_run:
        for source in stale:
            updated = repo.update_source_fields(source.key, enabled=False)
            if updated:
                disabled.append(updated.key)
    return OperationResult.success(
        message=f"{'would disable' if dry_run else 'disabled'} {len(stale if dry_run else disabled)} stale sources",
        dry_run=dry_run,
        stale_sources=[
            {"source_key": source.key, "raw_item_count": raw_by_source.get(source.key, 0), "reason": "low_raw_item_count"}
            for source in stale
        ],
        disabled_source_keys=disabled,
    )


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
