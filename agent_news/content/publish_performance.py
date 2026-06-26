"""Publish-performance analysis helpers.

This module stays pure. It takes scraped publish-history items and historical
snapshots from `publish_tasks`, then returns structured comparison output that
operations can wrap into OperationResult.
"""

from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter
from statistics import mean
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..intel.tokenizer import tokenize

_READ_GROWTH_STRONG = 1.2
_READ_GROWTH_WEAK = 0.8
_RATE_GROWTH_STRONG = 1.15
_RATE_GROWTH_WEAK = 0.85

_IMPACT_TOKENS = (
    "涨价",
    "账单",
    "成本",
    "钱包",
    "缺电",
    "电网",
    "裁员",
    "监管",
    "供应链",
    "价格",
    "降价",
    "翻车",
    "砍半",
)
_TECH_TOKENS = (
    "ai",
    "openai",
    "英伟达",
    "苹果",
    "三星",
    "芯片",
    "模型",
    "手机",
    "电脑",
    "gpu",
    "cpu",
    "内存",
    "数据中心",
)
_WEAK_FORMAT_TOKENS = ("速览", "今日科技", "科技速览", "发布新产品", "宣布合作")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip().lower()


def _normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw
    if not parsed.scheme and not parsed.netloc:
        return raw
    parsed = parsed._replace(fragment="")
    return urlunsplit(parsed)


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _metric_score(item: dict[str, Any]) -> float:
    return (
        _to_int(item.get("read_count"))
        + _to_int(item.get("like_count")) * 8
        + _to_int(item.get("share_count")) * 10
        + _to_int(item.get("recommend_count")) * 6
        + _to_int(item.get("comment_count")) * 12
        + _to_int(item.get("highlight_count")) * 4
        + _to_int(item.get("reprint_count")) * 15
        + _to_float(item.get("tip_amount")) * 20
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def enrich_metric_item(item: dict[str, Any]) -> dict[str, Any]:
    reads = _to_int(item.get("read_count"))
    likes = _to_int(item.get("like_count"))
    shares = _to_int(item.get("share_count"))
    recommends = _to_int(item.get("recommend_count"))
    comments = _to_int(item.get("comment_count"))
    highlights = _to_int(item.get("highlight_count"))
    reprints = _to_int(item.get("reprint_count"))
    tip_amount = _to_float(item.get("tip_amount"))
    engagement_actions = likes + shares + recommends + comments + highlights + reprints
    return {
        **item,
        "tip_amount_numeric": tip_amount,
        "engagement_actions": engagement_actions,
        "engagement_rate": _rate(engagement_actions, reads),
        "like_rate": _rate(likes, reads),
        "share_rate": _rate(shares, reads),
        "comment_rate": _rate(comments, reads),
        "quality_score": round(_metric_score(item), 2),
        "signals": {
            "audience_reach": reads,
            "audience_approval": likes + recommends,
            "spread": shares + reprints,
            "discussion": comments,
            "deep_reading": highlights,
            "monetization": tip_amount,
        },
    }


def _normalize_title(value: str) -> str:
    return _normalize_text(value).replace("：", ":")


def _item_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or "").strip()


def _item_url(item: dict[str, Any]) -> str:
    return _normalize_url(item.get("url") or "")


def _item_remote_key(item: dict[str, Any]) -> str:
    return str(item.get("remote_key") or "").strip()


def build_analysis_key(item: dict[str, Any] | None) -> str:
    if not item:
        return "all_items"
    remote_key = _item_remote_key(item)
    if remote_key:
        return f"remote_key:{remote_key}"
    url = _item_url(item)
    if url:
        return f"url:{url}"
    appmsg_id = str(item.get("appmsg_id") or "").strip()
    if appmsg_id:
        return f"appmsg_id:{appmsg_id}"
    title = _normalize_title(_item_title(item))
    published_at = str(item.get("published_at") or "").strip()
    if title and published_at:
        return f"title:{title}|published_at:{published_at}"
    if title:
        return f"title:{title}"
    return "all_items"


def _match_by_url(items: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    target_url = _normalize_url(url)
    if not target_url:
        return None
    for item in items:
        if _item_url(item) == target_url:
            return item
    return None


def _match_by_title(items: list[dict[str, Any]], title: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    needle = _normalize_title(title)
    if not needle:
        return None, []
    exact_matches = [item for item in items if _normalize_title(_item_title(item)) == needle]
    if exact_matches:
        return (exact_matches[0] if len(exact_matches) == 1 else None), exact_matches
    partial_matches = []
    for item in items:
        haystack = _normalize_title(_item_title(item))
        if needle in haystack or haystack in needle:
            partial_matches.append(item)
    if len(partial_matches) == 1:
        return partial_matches[0], partial_matches
    return None, partial_matches


def resolve_metric_target(
    items: list[dict[str, Any]],
    *,
    title: str = "",
    url: str = "",
) -> dict[str, Any]:
    enriched = [enrich_metric_item(item) for item in items]
    target = None
    target_status = "all_items"
    matches: list[dict[str, Any]] = []

    if url:
        target = _match_by_url(enriched, url)
        if target is None:
            return {
                "items": enriched,
                "scope_items": enriched,
                "matched_item": None,
                "target_found": False,
                "target_status": "target_not_found",
                "target_matches": [],
                "analysis_key": "",
            }
        matches = [target]
        target_status = "exact_url"
    elif title:
        target, matches = _match_by_title(enriched, title)
        if target is None and len(matches) > 1:
            return {
                "items": enriched,
                "scope_items": enriched,
                "matched_item": None,
                "target_found": False,
                "target_status": "ambiguous_title",
                "target_matches": matches[:8],
                "analysis_key": "",
            }
        if target is None:
            return {
                "items": enriched,
                "scope_items": enriched,
                "matched_item": None,
                "target_found": False,
                "target_status": "target_not_found",
                "target_matches": matches[:8],
                "analysis_key": "",
            }
        target_status = "exact_title"
    else:
        return {
            "items": enriched,
            "scope_items": enriched,
            "matched_item": None,
            "target_found": None,
            "target_status": "all_items",
            "target_matches": [],
            "analysis_key": "all_items",
        }

    analysis_key = build_analysis_key(target)
    return {
        "items": enriched,
        "scope_items": [target],
        "matched_item": target,
        "target_found": True,
        "target_status": target_status,
        "target_matches": matches[:8],
        "analysis_key": analysis_key,
    }


def build_publish_metrics_analysis(
    items: list[dict[str, Any]],
    *,
    title: str = "",
    url: str = "",
    snapshot_at: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_metric_target(items, title=title, url=url)
    enriched = resolved["items"]
    scope_items = resolved["scope_items"]
    matched = resolved["matched_item"]

    total_reads = sum(_to_int(item.get("read_count")) for item in scope_items)
    total_likes = sum(_to_int(item.get("like_count")) for item in scope_items)
    total_shares = sum(_to_int(item.get("share_count")) for item in scope_items)
    total_recommends = sum(_to_int(item.get("recommend_count")) for item in scope_items)
    total_comments = sum(_to_int(item.get("comment_count")) for item in scope_items)
    total_highlights = sum(_to_int(item.get("highlight_count")) for item in scope_items)
    total_reprints = sum(_to_int(item.get("reprint_count")) for item in scope_items)
    total_tips = round(sum(_to_float(item.get("tip_amount_numeric")) for item in scope_items), 2)
    total_engagement = total_likes + total_shares + total_recommends + total_comments + total_highlights + total_reprints
    overall_engagement_rate = _rate(total_engagement, total_reads)
    overall_like_rate = _rate(total_likes, total_reads)
    overall_share_rate = _rate(total_shares, total_reads)
    overall_comment_rate = _rate(total_comments, total_reads)

    sorted_by_score = sorted(enriched, key=lambda item: float(item.get("quality_score") or 0), reverse=True)
    sorted_by_reads = sorted(enriched, key=lambda item: _to_int(item.get("read_count")), reverse=True)
    sorted_by_share_rate = sorted(
        [item for item in enriched if _to_int(item.get("read_count")) > 0],
        key=lambda item: float(item.get("share_rate") or 0),
        reverse=True,
    )

    analysis: dict[str, Any] = {
        "scope": "matched_title" if matched else ("all_items" if not title and not url else "matched_items"),
        "target_found": resolved["target_found"],
        "target_status": resolved["target_status"],
        "analysis_key": resolved["analysis_key"],
        "analysis_snapshot_at": snapshot_at or _utcnow(),
        "requested_title": title,
        "requested_url": _normalize_url(url),
        "matched_item": matched,
        "matched_items": resolved["target_matches"],
        "items": enriched,
        "summary": {
            "item_count": len(scope_items),
            "total_reads": total_reads,
            "total_likes": total_likes,
            "total_shares": total_shares,
            "total_recommends": total_recommends,
            "total_comments": total_comments,
            "total_highlights": total_highlights,
            "total_reprints": total_reprints,
            "total_tip_amount": total_tips,
            "total_engagement_actions": total_engagement,
            "overall_engagement_rate": overall_engagement_rate,
            "overall_like_rate": overall_like_rate,
            "overall_share_rate": overall_share_rate,
            "overall_comment_rate": overall_comment_rate,
        },
        "top_items": {
            "by_quality_score": sorted_by_score[:5],
            "by_reads": sorted_by_reads[:5],
            "by_share_rate": sorted_by_share_rate[:5],
        },
        "metric_meaning": {
            "read_count": "阅读人数，衡量触达和标题吸引力",
            "like_count": "点赞人数，衡量认可度",
            "share_count": "分享人数，衡量传播性",
            "recommend_count": "推荐人数，衡量平台内推荐意愿",
            "comment_count": "留言条数，衡量讨论度",
            "highlight_count": "划线人数，衡量深读和摘录价值",
            "tip_amount": "赞赏金额，衡量付费认可",
            "reprint_count": "被转载次数，衡量外部引用和扩散",
        },
    }
    return analysis


def _items_from_analysis(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    items = analysis.get("items")
    if isinstance(items, list) and items:
        return [item for item in items if isinstance(item, dict)]

    top_items = analysis.get("top_items") if isinstance(analysis.get("top_items"), dict) else {}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group_name in ("by_quality_score", "by_reads", "by_share_rate"):
        for item in top_items.get(group_name) or []:
            if not isinstance(item, dict):
                continue
            key = build_analysis_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _normal_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = _normal_text(text)
    return any(token.lower() in normalized for token in tokens)


def _extract_title_patterns(items: list[dict[str, Any]], *, top_n: int = 5) -> dict[str, Any]:
    article_items = [
        enrich_metric_item(item)
        for item in items
        if str(item.get("title") or "").strip()
        and "开启留言" not in str(item.get("title") or "")
        and ("/s/" in str(item.get("url") or "") or str(item.get("remote_key") or "").startswith("url:https://mp.weixin.qq.com/s/"))
    ]
    ranked = sorted(article_items, key=lambda item: _metric_score(item), reverse=True)
    winners = ranked[:top_n]
    weak_items = [item for item in article_items if _to_int(item.get("read_count")) <= 5]

    token_counter: Counter[str] = Counter()
    for item in winners:
        title = str(item.get("title") or "")
        for token in tokenize(title):
            token_counter[token] += 1

    observed_patterns: list[str] = []
    if any(_contains_any(item.get("title"), _IMPACT_TOKENS) for item in winners):
        observed_patterns.append("标题包含成本、价格、账单、缺电、裁员、监管等现实后果")
    if any(_contains_any(item.get("title"), _TECH_TOKENS) for item in winners):
        observed_patterns.append("标题前半句有明确公司、产品、硬件或技术对象")
    if any("：" in str(item.get("title") or "") or ":" in str(item.get("title") or "") for item in winners):
        observed_patterns.append("标题使用“具体事件：现实影响”的两段式结构")
    if any("、" in str(item.get("title") or "") for item in winners):
        observed_patterns.append("多事件标题用并列信息点制造密度")

    weak_patterns: list[str] = []
    if any(_contains_any(item.get("title"), _WEAK_FORMAT_TOKENS) for item in weak_items):
        weak_patterns.append("泛科技速览或只报发布动作的标题在当前样本中偏弱")
    if any(_contains_any(item.get("title"), ("视频号",)) for item in weak_items):
        weak_patterns.append("混入视频号或非标准文章记录会污染公众号文章判断")

    return {
        "winning_titles": [
            {
                "title": item.get("title") or "",
                "read_count": _to_int(item.get("read_count")),
                "share_count": _to_int(item.get("share_count")),
                "quality_score": round(_metric_score(item), 2),
            }
            for item in winners
        ],
        "weak_titles": [
            {
                "title": item.get("title") or "",
                "read_count": _to_int(item.get("read_count")),
                "share_count": _to_int(item.get("share_count")),
            }
            for item in weak_items[:top_n]
        ],
        "top_tokens": [{"token": token, "count": count} for token, count in token_counter.most_common(12)],
        "observed_winning_patterns": observed_patterns,
        "observed_weak_patterns": weak_patterns,
    }


def build_content_strategy_profile(
    analysis: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Turn raw publish metrics into reusable operating guidance for agents."""

    items = _items_from_analysis(analysis)
    summary = analysis.get("summary") or {}
    patterns = _extract_title_patterns(items)
    total_reads = _to_int(summary.get("total_reads"))
    total_shares = _to_int(summary.get("total_shares"))
    item_count = _to_int(summary.get("item_count"))
    winning_titles = patterns.get("winning_titles") or []
    top_reads = sum(_to_int(item.get("read_count")) for item in winning_titles[:4])
    top_read_share = round(top_reads / total_reads, 4) if total_reads else 0.0

    guidance = [
        "优先选择 AI、芯片、硬件、手机、电脑、电力、供应链等技术事件，但必须落到成本、价格、账单、裁员、监管或普通用户影响。",
        "标题前半句给具体公司/产品/事件，后半句给现实后果；不要只写发布、合作、升级。",
        "5 条短讯合集必须先提炼共同主线，例如涨价、缺电、成本外溢、监管或供应链变化。",
        "当前点赞、留言、推荐、划线、赞赏样本不足，先以阅读和分享判断选题吸引力。",
    ]
    avoid = [
        "泛科技速览",
        "只有技术名词没有现实后果",
        "只写某公司发布或合作",
        "发布后 24 小时内过早判定失败",
    ]
    return {
        "profile_version": 1,
        "generated_at": generated_at or _utcnow(),
        "source_operation": "wechat.analyze_publish_metrics",
        "source_scope": analysis.get("scope") or "all_items",
        "sample_size": item_count,
        "summary": {
            "total_reads": total_reads,
            "total_shares": total_shares,
            "overall_engagement_rate": _to_float(summary.get("overall_engagement_rate")),
            "top4_read_share": top_read_share,
        },
        "audience_fit_keywords": list(_TECH_TOKENS),
        "impact_keywords": list(_IMPACT_TOKENS),
        "preferred_title_patterns": [
            "具体事件/公司/产品：现实影响",
            "A、B、C：共同趋势",
            "技术变化 + 钱包/成本/电力/供应链后果",
        ],
        "avoid_patterns": avoid,
        "observed_winning_patterns": patterns.get("observed_winning_patterns") or [],
        "observed_weak_patterns": patterns.get("observed_weak_patterns") or [],
        "winning_titles": winning_titles,
        "weak_titles": patterns.get("weak_titles") or [],
        "top_tokens": patterns.get("top_tokens") or [],
        "next_content_guidance": guidance,
        "suggested_next_operation": "radar.review_events",
    }


def _latest_success_analysis(tasks: list[Any]) -> dict[str, Any] | None:
    for task in tasks:
        operation_name = str(getattr(task, "operation_name", "") or (task.get("operation_name") if isinstance(task, dict) else ""))
        status = str(getattr(task, "status", "") or (task.get("status") if isinstance(task, dict) else ""))
        if operation_name != "wechat.analyze_publish_metrics" or status != "success":
            continue
        params = getattr(task, "params", {}) if not isinstance(task, dict) else task.get("params", {})
        if not isinstance(params, dict):
            continue
        state = params.get("state") if isinstance(params.get("state"), dict) else {}
        analysis = state.get("analysis") if isinstance(state.get("analysis"), dict) else {}
        if analysis and isinstance(analysis.get("summary"), dict):
            return analysis
    return None


def latest_content_strategy_profile(task_list: list[Any]) -> dict[str, Any]:
    analysis = _latest_success_analysis(task_list)
    if not analysis:
        return {
            "available": False,
            "profile_version": 1,
            "message": "尚无成功的 wechat.analyze_publish_metrics 快照；先触发指标分析再形成运营闭环。",
            "suggested_next_operation": "wechat.analyze_publish_metrics",
        }
    return {"available": True, **build_content_strategy_profile(analysis)}


def evaluate_title_strategy_fit(title: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Score how well a candidate title follows the current content strategy."""

    title_text = str(title or "").strip()
    if not title_text or not profile.get("available", True):
        return {
            "score": 0,
            "label": "unknown",
            "matched_keywords": [],
            "matched_impact_keywords": [],
            "warnings": ["缺少标题或运营画像不可用"],
            "suggestions": ["先运行 wechat.analyze_publish_metrics 形成内容策略画像。"],
        }

    audience_keywords = [str(item) for item in profile.get("audience_fit_keywords") or _TECH_TOKENS]
    impact_keywords = [str(item) for item in profile.get("impact_keywords") or _IMPACT_TOKENS]
    matched_audience = [token for token in audience_keywords if token.lower() in title_text.lower()]
    matched_impact = [token for token in impact_keywords if token.lower() in title_text.lower()]

    score = 0
    warnings: list[str] = []
    suggestions: list[str] = []
    if matched_audience:
        score += 35
    else:
        warnings.append("标题缺少明确公司、产品、硬件或技术对象")
        suggestions.append("前半句加入具体公司/产品/芯片/模型/设备名。")
    if matched_impact:
        score += 45
    else:
        warnings.append("标题缺少成本、价格、账单、电力、裁员、监管等现实后果")
        suggestions.append("后半句补上这件事会影响谁、钱怎么变、成本怎么变。")
    if "：" in title_text or ":" in title_text:
        score += 10
    else:
        suggestions.append("优先使用“具体事件：现实影响”的两段式标题。")
    if "、" in title_text or "5条" in title_text or "5 条" in title_text:
        score += 10

    if any(token in title_text for token in _WEAK_FORMAT_TOKENS):
        score -= 20
        warnings.append("标题有泛速览/弱动作倾向，需要补共同主线")
    score = max(0, min(100, score))
    label = "strong" if score >= 80 else ("partial" if score >= 50 else "weak")
    return {
        "score": score,
        "label": label,
        "matched_keywords": matched_audience[:8],
        "matched_impact_keywords": matched_impact[:8],
        "warnings": warnings,
        "suggestions": suggestions[:5],
    }


def _baseline_summary(history_snapshots: list[dict[str, Any]]) -> dict[str, float]:
    if not history_snapshots:
        return {}

    metric_map: dict[str, list[float]] = {
        "total_reads": [],
        "total_likes": [],
        "total_shares": [],
        "total_recommends": [],
        "total_comments": [],
        "total_highlights": [],
        "total_reprints": [],
        "total_tip_amount": [],
        "total_engagement_actions": [],
        "overall_engagement_rate": [],
        "overall_like_rate": [],
        "overall_share_rate": [],
        "overall_comment_rate": [],
    }
    for snapshot in history_snapshots:
        summary = snapshot.get("summary") or {}
        for key in metric_map:
            if key in summary:
                metric_map[key].append(_to_float(summary.get(key)))
    return {key: round(mean(values), 4) for key, values in metric_map.items() if values}


def _delta(current: dict[str, Any], baseline: dict[str, float]) -> dict[str, float]:
    summary = current.get("summary") or {}
    delta: dict[str, float] = {}
    for key, baseline_value in baseline.items():
        current_value = _to_float(summary.get(key))
        delta[key] = round(current_value - baseline_value, 4)
    return delta


def _trend_from_delta(delta: dict[str, float]) -> str:
    if not delta:
        return "flat"
    reads = delta.get("total_reads", 0.0)
    spread = delta.get("overall_share_rate", 0.0)
    if reads > 0 and (spread > 0 or delta.get("overall_engagement_rate", 0.0) > 0):
        return "up"
    if reads < 0 or delta.get("overall_engagement_rate", 0.0) < 0 or delta.get("overall_share_rate", 0.0) < 0:
        return "down"
    return "flat"


def _performance_label(current: dict[str, Any], baseline: dict[str, float], history_count: int) -> str:
    summary = current.get("summary") or {}
    current_reads = _to_float(summary.get("total_reads"))
    current_share_rate = _to_float(summary.get("overall_share_rate"))
    current_engagement = _to_float(summary.get("overall_engagement_rate"))
    if history_count == 0:
        if current_reads >= 50 or current_engagement >= 0.05:
            return "strong"
        return "baseline"

    baseline_reads = baseline.get("total_reads", 0.0) or 0.0
    baseline_share_rate = baseline.get("overall_share_rate", 0.0) or 0.0
    baseline_engagement = baseline.get("overall_engagement_rate", 0.0) or 0.0

    if (
        current_reads >= baseline_reads * _READ_GROWTH_STRONG
        or current_share_rate >= baseline_share_rate * _RATE_GROWTH_STRONG
        or current_engagement >= baseline_engagement * _RATE_GROWTH_STRONG
    ):
        return "strong"
    if (
        current_reads <= baseline_reads * _READ_GROWTH_WEAK
        or current_share_rate <= baseline_share_rate * _RATE_GROWTH_WEAK
        or current_engagement <= baseline_engagement * _RATE_GROWTH_WEAK
    ):
        return "weak"
    return "stable"


def _weakness_tags(current: dict[str, Any], baseline: dict[str, float]) -> list[str]:
    summary = current.get("summary") or {}
    tags: list[str] = []
    reads = _to_float(summary.get("total_reads"))
    share_rate = _to_float(summary.get("overall_share_rate"))
    comment_rate = _to_float(summary.get("overall_comment_rate"))
    like_rate = _to_float(summary.get("overall_like_rate"))
    engagement_rate = _to_float(summary.get("overall_engagement_rate"))
    tip_amount = _to_float(summary.get("total_tip_amount"))
    highlights = _to_float(summary.get("total_highlights"))

    baseline_reads = baseline.get("total_reads", 0.0) or 0.0
    baseline_share_rate = baseline.get("overall_share_rate", 0.0) or 0.0
    baseline_comment_rate = baseline.get("overall_comment_rate", 0.0) or 0.0
    baseline_like_rate = baseline.get("overall_like_rate", 0.0) or 0.0
    baseline_engagement = baseline.get("overall_engagement_rate", 0.0) or 0.0
    baseline_tip = baseline.get("total_tip_amount", 0.0) or 0.0
    baseline_highlights = baseline.get("total_highlights", 0.0) or 0.0

    if reads and baseline_reads and reads < baseline_reads * _READ_GROWTH_WEAK:
        tags.append("reach_weak")
    if share_rate < max(0.01, baseline_share_rate * _RATE_GROWTH_WEAK):
        tags.append("spread_weak")
    if comment_rate < max(0.005, baseline_comment_rate * _RATE_GROWTH_WEAK):
        tags.append("discussion_weak")
    if like_rate < max(0.02, baseline_like_rate * _RATE_GROWTH_WEAK):
        tags.append("approval_weak")
    if engagement_rate < max(0.03, baseline_engagement * _RATE_GROWTH_WEAK):
        tags.append("interaction_weak")
    if tip_amount <= baseline_tip and baseline_tip > 0:
        tags.append("monetization_weak")
    if highlights <= baseline_highlights * _RATE_GROWTH_WEAK and baseline_highlights > 0:
        tags.append("depth_weak")

    if not tags and reads <= 20:
        tags.append("reach_weak")
    return tags[:6]


def _winning_patterns(current: dict[str, Any], history_snapshots: list[dict[str, Any]]) -> list[str]:
    summary = current.get("summary") or {}
    patterns: list[str] = []
    current_reads = _to_float(summary.get("total_reads"))
    current_share_rate = _to_float(summary.get("overall_share_rate"))
    current_comment_rate = _to_float(summary.get("overall_comment_rate"))
    current_engagement = _to_float(summary.get("overall_engagement_rate"))

    baseline = _baseline_summary(history_snapshots)
    if baseline:
        if current_reads > baseline.get("total_reads", 0):
            patterns.append("阅读高于历史平均")
        if current_share_rate > baseline.get("overall_share_rate", 0):
            patterns.append("分享效率高于历史平均")
        if current_comment_rate > baseline.get("overall_comment_rate", 0):
            patterns.append("讨论度高于历史平均")
        if current_engagement > baseline.get("overall_engagement_rate", 0):
            patterns.append("互动效率高于历史平均")

    if current_reads >= 50:
        patterns.append("触达面足够")
    if current_share_rate >= 0.03:
        patterns.append("扩散信号明显")
    if current_comment_rate >= 0.01:
        patterns.append("讨论回声出现")

    top_history = sorted(
        history_snapshots,
        key=lambda item: _to_float((item.get("summary") or {}).get("total_reads")),
        reverse=True,
    )[:3]
    for snapshot in top_history:
        title = str(snapshot.get("title") or snapshot.get("requested_title") or "").strip()
        if title:
            patterns.append(f"历史同类样本：{title[:24]}")
    return list(dict.fromkeys(patterns))[:6]


def _next_guidance(performance_label: str, trend: str, weakness_tags: list[str], current: dict[str, Any]) -> tuple[list[str], str]:
    guidance: list[str] = []
    if "reach_weak" in weakness_tags:
        guidance.append("标题前 14 字要先交代最强卖点，不要把信息点拖到后面。")
    if "spread_weak" in weakness_tags:
        guidance.append("补一个能转发的判断句或结论句，让读者有转发理由。")
    if "discussion_weak" in weakness_tags:
        guidance.append("把立场或矛盾点写得更清楚，给评论留出可接话的口子。")
    if "approval_weak" in weakness_tags:
        guidance.append("正文先把事实钉实，再让观点跟着事实走。")
    if "depth_weak" in weakness_tags:
        guidance.append("补上数据、原文引用或背景链路，增强深读信号。")
    if "monetization_weak" in weakness_tags:
        guidance.append("如果有商业目标，补一段更明确的价值落点。")

    if not guidance:
        guidance.append("当前表现稳定，优先复用这条稿的标题结构、开头节奏和信息密度。")

    if performance_label == "weak" or trend == "down":
        next_op = "article.update"
    elif performance_label == "strong":
        next_op = "radar.review_events"
    else:
        next_op = "wechat.analyze_publish_metrics"

    return guidance, next_op


def build_content_performance_review(
    current_analysis: dict[str, Any],
    history_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = _baseline_summary(history_snapshots)
    delta = _delta(current_analysis, baseline)
    trend = _trend_from_delta(delta)
    performance_label = _performance_label(current_analysis, baseline, len(history_snapshots))
    weakness_tags = _weakness_tags(current_analysis, baseline)
    winning_patterns = _winning_patterns(current_analysis, history_snapshots)
    next_content_guidance, suggested_next_operation = _next_guidance(
        performance_label,
        trend,
        weakness_tags,
        current_analysis,
    )
    return {
        "performance_label": performance_label,
        "trend": trend,
        "baseline": baseline,
        "delta": delta,
        "history_count": len(history_snapshots),
        "weakness_tags": weakness_tags,
        "winning_patterns": winning_patterns,
        "next_content_guidance": next_content_guidance,
        "suggested_next_operation": suggested_next_operation,
        "reviewed_at": _utcnow(),
    }


def build_title_history_hint(title: str, history_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize similar historical publish snapshots for a title-like query."""

    if not history_snapshots:
        return {
            "similar_count": 0,
            "top_titles": [],
            "best_title": "",
            "best_snapshot_at": "",
            "best_summary": {},
            "best_metric": "total_reads",
            "suggested_next_operation": "wechat.review_content_performance",
        }

    ranked = sorted(
        history_snapshots,
        key=lambda snapshot: (
            _to_float((snapshot.get("summary") or {}).get("total_reads")),
            _to_float((snapshot.get("summary") or {}).get("overall_engagement_rate")),
        ),
        reverse=True,
    )
    top_titles = []
    top_items = []
    for snapshot in ranked[:5]:
        summary = snapshot.get("summary") or {}
        candidate_title = str(snapshot.get("title") or snapshot.get("analysis", {}).get("requested_title") or "").strip()
        if candidate_title:
            top_titles.append(candidate_title)
        top_items.append(
            {
                "title": candidate_title,
                "snapshot_at": snapshot.get("snapshot_at") or "",
                "total_reads": _to_int(summary.get("total_reads")),
                "overall_engagement_rate": _to_float(summary.get("overall_engagement_rate")),
                "overall_share_rate": _to_float(summary.get("overall_share_rate")),
                "overall_comment_rate": _to_float(summary.get("overall_comment_rate")),
            }
        )

    best = ranked[0]
    best_summary = best.get("summary") or {}
    return {
        "similar_count": len(history_snapshots),
        "top_titles": top_titles[:5],
        "best_title": str(best.get("title") or best.get("analysis", {}).get("requested_title") or "").strip(),
        "best_snapshot_at": str(best.get("snapshot_at") or "").strip(),
        "best_summary": {
            "total_reads": _to_int(best_summary.get("total_reads")),
            "overall_engagement_rate": _to_float(best_summary.get("overall_engagement_rate")),
            "overall_share_rate": _to_float(best_summary.get("overall_share_rate")),
            "overall_comment_rate": _to_float(best_summary.get("overall_comment_rate")),
        },
        "top_items": top_items,
        "suggested_next_operation": "wechat.review_content_performance",
    }


def normalize_history_snapshot(task: Any) -> dict[str, Any] | None:
    if task is None:
        return None
    if isinstance(task, dict):
        operation_name = str(task.get("operation_name") or "")
        params = task.get("params") or {}
        started_at = str(task.get("started_at") or "")
        task_id = str(task.get("id") or "")
    else:
        operation_name = str(getattr(task, "operation_name", "") or "")
        params = getattr(task, "params", {}) or {}
        started_at = str(getattr(task, "started_at", "") or "")
        task_id = str(getattr(task, "id", "") or "")
    state = params.get("state") if isinstance(params, dict) else {}
    if not isinstance(state, dict):
        state = {}
    analysis = state.get("analysis") if isinstance(state, dict) else {}
    if not isinstance(analysis, dict) or not analysis:
        analysis = state
    analysis_key = str(state.get("analysis_key") or analysis.get("analysis_key") or "").strip()
    if not analysis_key:
        return None
    summary = analysis.get("summary") if isinstance(analysis, dict) else {}
    if not isinstance(summary, dict) or not summary:
        return None
    matched_item = analysis.get("matched_item") if isinstance(analysis.get("matched_item"), dict) else {}
    return {
        "task_id": task_id,
        "operation_name": operation_name,
        "analysis_key": analysis_key,
        "snapshot_at": str(state.get("analysis_snapshot_at") or analysis.get("analysis_snapshot_at") or started_at or ""),
        "title": str(state.get("title") or analysis.get("requested_title") or matched_item.get("title") or ""),
        "url": str(state.get("target_url") or state.get("url") or analysis.get("requested_url") or matched_item.get("url") or ""),
        "summary": summary,
        "analysis": analysis,
        "state": state,
    }


def summarize_task_snapshots(task_list: list[Any], *, operation_name: str, analysis_key: str, limit: int = 5) -> list[dict[str, Any]]:
    """Filter publish-task audit rows into analysis snapshots."""

    snapshots: list[dict[str, Any]] = []
    for task in task_list:
        snapshot = normalize_history_snapshot(task)
        if snapshot is None:
            continue
        if snapshot["operation_name"] != operation_name:
            continue
        if snapshot["analysis_key"] != analysis_key:
            continue
        snapshots.append(snapshot)
    return snapshots[: max(0, int(limit))]


def summarize_title_history(task_list: list[Any], *, title: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return snapshots whose saved analysis titles overlap the query text."""

    query_tokens = set(tokenize(title))
    if not query_tokens:
        return []

    snapshots: list[dict[str, Any]] = []
    for task in task_list:
        snapshot = normalize_history_snapshot(task)
        if snapshot is None:
            continue
        candidate_tokens = set(tokenize(snapshot.get("title") or snapshot.get("analysis", {}).get("requested_title") or ""))
        if not candidate_tokens:
            continue
        overlap = len(query_tokens & candidate_tokens)
        if overlap == 0:
            continue
        score = overlap / max(len(query_tokens), len(candidate_tokens))
        if score >= 0.35:
            snapshots.append({**snapshot, "similarity": round(score, 4)})
    snapshots.sort(key=lambda item: (item.get("similarity", 0), item.get("snapshot_at", "")), reverse=True)
    return snapshots[: max(0, int(limit))]
