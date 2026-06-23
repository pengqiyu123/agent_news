"""Read-only review helpers for the information radar.

These functions do not touch the network and do not mutate storage. Operations
wrap them into OperationResult objects so agents can inspect radar state before
deciding the next atom to run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models.intel import EventDeepDive, IntelAlert, IntelEvent, RawItem, Source


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _latest_iso(values: list[str | None]) -> str | None:
    parsed = [dt for dt in (_parse_dt(v) for v in values) if dt is not None]
    if not parsed:
        return None
    return max(parsed).isoformat()


def summarize_event(event: IntelEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "composite_score": event.composite_score,
        "alert_state": event.alert_state,
        "source_count": event.source_count,
        "platform_count": event.platform_count,
        "published_at": event.published_at,
        "representative_link": event.representative_link,
        "deep_dive_id": event.deep_dive_id,
        "deep_dive_status": event.deep_dive_status,
        "worth_to_brief": event.worth_to_brief,
    }


def summarize_alert(alert: IntelAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "event_id": alert.event_id,
        "title": alert.title,
        "alert_state": alert.alert_state,
        "composite_score": alert.composite_score,
        "source_count": alert.source_count,
        "raised_at": alert.raised_at,
    }


def build_radar_status(
    *,
    sources: list[Source],
    raw_items: list[RawItem],
    raw_total: int,
    events: list[IntelEvent],
    event_total: int,
    alerts: list[IntelAlert],
    alert_total: int,
    deep_dives: list[EventDeepDive],
    deep_dive_total: int,
    include_recent: bool = True,
) -> dict[str, Any]:
    enabled_sources = [s for s in sources if s.enabled]
    if not sources:
        suggested = "radar.seed_defaults"
    elif raw_total == 0 and event_total == 0:
        suggested = "radar.sync_sources"
    elif event_total == 0:
        suggested = "radar.build_events"
    elif deep_dive_total > 0:
        suggested = "radar.review_deep_dive"
    else:
        suggested = "radar.review_events"

    state: dict[str, Any] = {
        "source_count": len(sources),
        "enabled_source_count": len(enabled_sources),
        "disabled_source_count": len(sources) - len(enabled_sources),
        "raw_item_count": raw_total,
        "event_count": event_total,
        "alert_count": alert_total,
        "deep_dive_count": deep_dive_total,
        "latest_raw_collected_at": _latest_iso([item.collected_at for item in raw_items]),
        "latest_event_updated_at": _latest_iso([event.updated_at for event in events]),
        "latest_deep_dive_finished_at": _latest_iso([dive.finished_at for dive in deep_dives]),
        "suggested_next_operation": suggested,
    }
    if include_recent:
        state["recent_events"] = [summarize_event(event) for event in events[:5]]
        state["recent_alerts"] = [summarize_alert(alert) for alert in alerts[:5]]
    return state


def _normalize_watchlist(watchlist: str | list[str] | None) -> list[str]:
    if watchlist is None:
        return []
    if isinstance(watchlist, str):
        return [item.strip().lower() for item in watchlist.split(",") if item.strip()]
    return [str(item).strip().lower() for item in watchlist if str(item).strip()]


def _watchlist_hits(event: IntelEvent, watchlist: list[str]) -> list[str]:
    if not watchlist:
        return []
    haystack = " ".join(
        [
            event.title,
            event.summary,
            " ".join(event.tags),
            " ".join(event.anchor_tokens),
            " ".join(event.entity_names),
        ]
    ).lower()
    return [word for word in watchlist if word and word in haystack]


def explain_event(event: IntelEvent, *, watchlist: str | list[str] | None = None) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []

    if event.source_count > 1:
        reasons.append(f"{event.source_count} 个来源同时覆盖")
    if event.platform_count > 1:
        reasons.append(f"{event.platform_count} 个平台出现")
    if event.alert_state in ("hot", "rising"):
        reasons.append(f"事件状态为 {event.alert_state}")
    if event.composite_score >= 70:
        reasons.append(f"综合分较高：{event.composite_score:.1f}")
    elif event.composite_score >= 50:
        reasons.append(f"综合分达到可关注区间：{event.composite_score:.1f}")
    if event.watchlisted:
        reasons.append("命中已配置 watchlist")

    hits = _watchlist_hits(event, _normalize_watchlist(watchlist))
    if hits:
        reasons.append("命中临时 watchlist: " + ", ".join(hits[:5]))

    if event.published_at:
        reasons.append("包含发布时间信号")
    if event.worth_to_brief:
        reasons.append("已有深挖结果建议可写")

    if not event.deep_dive_status:
        risks.append("尚未深挖全文")
    elif event.deep_dive_status != "ready":
        risks.append(f"深挖状态为 {event.deep_dive_status}")
    if event.source_count <= 1:
        risks.append("来源数量偏少")
    if not event.representative_link:
        risks.append("缺少代表链接")
    if event.ignored:
        risks.append("事件已被标记 ignored")
    if event.composite_score < 30:
        risks.append("综合分偏低")

    if not reasons:
        reasons.append("按综合分排序进入候选列表")
    return reasons, risks


def build_event_review(
    events: list[IntelEvent],
    *,
    total: int,
    watchlist: str | list[str] | None = None,
) -> dict[str, Any]:
    reviewed = []
    for event in events:
        reasons, risks = explain_event(event, watchlist=watchlist)
        reviewed.append(
            {
                **summarize_event(event),
                "why_recommended": reasons,
                "risks": risks,
                "suggested_next_operation": "radar.deep_dive_event",
                "suggested_params": {"event_id": event.id},
            }
        )
    top_id = reviewed[0]["id"] if reviewed else None
    return {
        "events": reviewed,
        "count": len(reviewed),
        "total": total,
        "suggested_top_event_id": top_id,
        "suggested_next_operation": "radar.deep_dive_event" if top_id else "radar.sync_sources",
    }


def source_result_from_dive_source(source) -> dict[str, Any]:
    return {
        "source_key": getattr(source, "source_key", "") or "",
        "source_name": getattr(source, "source_name", "") or "",
        "url": getattr(source, "link", "") or "",
        "title": getattr(source, "title", "") or "",
        "fetch_status": getattr(source, "fetch_status", "") or "",
        "extract_status": getattr(source, "extract_status", "") or "",
        "status": "success"
        if getattr(source, "fetch_status", "") == "success" and getattr(source, "extract_status", "") == "success"
        else "failed",
        "word_count": getattr(source, "word_count", 0) or 0,
        "error": getattr(source, "error", None),
    }


def writing_readiness_for_dive(dive: EventDeepDive) -> str:
    fact_count = len(dive.facts)
    success_count = dive.success_count
    failed_count = dive.failed_count
    if success_count >= 2 and fact_count >= 5 and failed_count <= success_count:
        return "ready"
    if success_count >= 1 and fact_count >= 2:
        return "partial"
    return "weak"


def review_deep_dive_state(dive: EventDeepDive, event: IntelEvent | None = None) -> dict[str, Any]:
    source_results = [source_result_from_dive_source(source) for source in (dive.sources or dive.full_text_sources)]
    readiness = writing_readiness_for_dive(dive)
    risks: list[str] = []
    if dive.success_count == 0:
        risks.append("没有成功来源")
    if len(dive.facts) < 3:
        risks.append("事实数量不足")
    if len(dive.quotes) == 0:
        risks.append("缺少可引用原文")
    if dive.failed_count > 0:
        risks.append("存在来源抓取失败")
    if dive.status != "ready":
        risks.append(f"素材包状态为 {dive.status}")

    return {
        "deep_dive_id": dive.id,
        "event_id": dive.event_id,
        "event_title": event.title if event else "",
        "status": dive.status,
        "writing_readiness": readiness,
        "fact_count": len(dive.facts),
        "quote_count": len(dive.quotes),
        "timeline_count": len(dive.timeline),
        "attempted_count": dive.attempted_count,
        "success_count": dive.success_count,
        "failed_count": dive.failed_count,
        "source_results": source_results,
        "risks": risks,
        "worthiness": dive.worthiness,
        "suggested_next_operation": "article.create"
        if readiness in ("ready", "partial")
        else "radar.deep_dive_event",
    }
