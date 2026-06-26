"""Article atomic operations.

These wrap the existing article repository CRUD so agents can stay on the
uniform Operation Registry surface instead of mixing REST calls and operations.
"""

from __future__ import annotations

from ..content.article_quality import review_article_quality
from ..content.wechat_payload import prepare_wechat_payload as build_wechat_payload
from ..content.publish_performance import (
    build_title_history_hint,
    evaluate_title_strategy_fit,
    latest_content_strategy_profile,
    summarize_title_history,
)
from ..db import get_repository
from ..db.intel_repository import get_intel_repository
from ..models.operation import OperationResult
from .base import operation


def _parse_material_ids(material_id: str | None) -> list[str]:
    raw = str(material_id or "").strip()
    if not raw:
        return []
    normalized = raw.replace("，", ",").replace(";", ",").replace("；", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _load_deep_dives_for_article(article):
    """Resolve article.material_id when it points at one or more deep dives."""
    material_id = (article.material_id or "").strip()
    if not material_id:
        return []
    repo = get_intel_repository()
    dives = []
    seen = set()
    for item in _parse_material_ids(material_id):
        dive = repo.get_deep_dive(item)
        if dive is None:
            # Some agents may pass an event id as the material anchor.
            dive = repo.get_deep_dive_by_event(item)
        if dive is None or dive.id in seen:
            continue
        dives.append(dive)
        seen.add(dive.id)
    return dives


@operation(
    name="article.create",
    category="article",
    description="保存 Agent 已写好的文章，不自动发布、不创建 workflow。",
    params={"title": "标题", "digest": "摘要", "body_markdown": "正文 Markdown", "author": "作者", "material_id": "素材 ID"},
)
def create_article(
    ctx,
    title: str,
    digest: str = "",
    body_markdown: str = "",
    author: str = "",
    material_id: str | None = None,
) -> OperationResult:
    if not str(title or "").strip():
        return OperationResult.failure(message="title is required")
    if not str(body_markdown or "").strip():
        return OperationResult.failure(message="body_markdown is required")
    article = get_repository().create_article(
        title=title,
        digest=digest,
        body_markdown=body_markdown,
        author=author,
        material_id=material_id,
    )
    return OperationResult.success(message=f"created article {article.id}", article=article.model_dump(), article_id=article.id)


@operation(
    name="article.get",
    category="article",
    description="读取文章详情。",
    params={"article_id": "文章 ID"},
)
def get_article(ctx, article_id: str) -> OperationResult:
    article = get_repository().get_article(article_id)
    if article is None:
        return OperationResult.failure(message=f"article '{article_id}' not found")
    return OperationResult.success(message=f"loaded article {article.id}", article=article.model_dump())


@operation(
    name="article.list",
    category="article",
    description="列出文章。",
    params={"page": "页码，默认 1", "page_size": "每页数量，默认 50"},
)
def list_articles(ctx, page: int = 1, page_size: int = 50) -> OperationResult:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    items, total = get_repository().list_articles(limit=page_size, offset=(page - 1) * page_size)
    return OperationResult.success(
        message=f"loaded {len(items)} articles",
        items=[item.model_dump() for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@operation(
    name="article.update",
    category="article",
    description="修改标题、摘要、作者、正文、素材关联等文章字段。",
    params={"article_id": "文章 ID", "fields": "要更新的字段 dict"},
)
def update_article(ctx, article_id: str, fields: dict | None = None, **kwargs) -> OperationResult:
    updates = dict(fields or {})
    updates.update(kwargs)
    if not updates:
        return OperationResult.failure(message="no fields to update")
    article = get_repository().update_article(article_id, **updates)
    if article is None:
        return OperationResult.failure(message=f"article '{article_id}' not found")
    return OperationResult.success(message=f"updated article {article.id}", article=article.model_dump())


@operation(
    name="article.review_quality",
    category="article",
    description="只读：按项目内置写作规范复核文章是否可进入微信填写。",
    params={"article_id": "文章 ID"},
)
def review_quality(ctx, article_id: str) -> OperationResult:
    article = get_repository().get_article(article_id)
    if article is None:
        return OperationResult.failure(message=f"article '{article_id}' not found")
    dives = _load_deep_dives_for_article(article)
    report = review_article_quality(article, deep_dives=dives)
    history_tasks, _ = get_repository().list_publish_tasks(limit=100)
    similar_snapshots = summarize_title_history(history_tasks, title=article.title, limit=5)
    history_hint = build_title_history_hint(article.title, similar_snapshots) if similar_snapshots else {}
    content_strategy_profile = latest_content_strategy_profile(history_tasks)
    strategy_fit = evaluate_title_strategy_fit(article.title, content_strategy_profile)
    payload = {
        "article_id": article.id,
        "material_id": article.material_id,
        "quality_report": report.as_dict(),
        "ready_for_wechat_payload": report.passed,
        "suggested_next_operation": report.suggested_next_operation,
        "content_strategy_profile": content_strategy_profile,
        "content_strategy_fit": strategy_fit,
    }
    payload["quality_report"]["metrics"]["content_strategy_fit"] = strategy_fit
    if content_strategy_profile.get("available"):
        payload["quality_report"]["metrics"]["content_strategy_profile"] = content_strategy_profile
    if strategy_fit.get("label") == "weak":
        payload["quality_report"]["warnings"].append(
            "运营策略观察弱匹配（仅供参考）：" + "；".join(strategy_fit.get("warnings") or [])
        )
    if history_hint:
        payload["historical_performance_hint"] = history_hint
        payload["quality_report"]["metrics"]["historical_performance_hint"] = history_hint
        if history_hint.get("best_title"):
            payload["quality_report"]["warnings"].append(
                f"历史相似标题表现参考（观察性，不是因果）：{history_hint.get('best_title')}（{history_hint.get('best_summary', {}).get('total_reads', 0)} 阅读）"
            )
    if not report.passed:
        return OperationResult.failure(
            message="article quality gate failed: " + "; ".join(report.issues),
            **payload,
        )
    return OperationResult.success(message=f"article quality passed for {article.id}", **payload)


@operation(
    name="article.prepare_wechat_payload",
    category="article",
    description="只读：将文章转换成微信填写参数，不打开浏览器。",
    params={
        "article_id": "文章 ID",
        "cover_prompt": "可选封面提示词",
        "override_quality_gate": "默认 false；仅人工确认例外时跳过质量门禁",
    },
)
def prepare_wechat_payload(ctx, article_id: str, cover_prompt: str = "", override_quality_gate: bool = False) -> OperationResult:
    article = get_repository().get_article(article_id)
    if article is None:
        return OperationResult.failure(message=f"article '{article_id}' not found")
    dives = _load_deep_dives_for_article(article)
    quality_report = review_article_quality(article, deep_dives=dives).as_dict()
    payload = build_wechat_payload(
        article,
        cover_prompt=cover_prompt,
        quality_report=quality_report,
        enforce_quality_gate=not override_quality_gate,
    )
    missing = payload.get("missing_required") or []
    if missing:
        return OperationResult.failure(
            message="article missing required fields for wechat fill: " + ", ".join(missing),
            **payload,
            suggested_next_operation="article.update",
            suggested_params={"article_id": article.id, "fields": {field: "" for field in missing}},
        )
    if payload.get("quality_gate_enforced") and not payload.get("quality_gate_passed"):
        issues = (quality_report or {}).get("issues") or []
        return OperationResult.failure(
            message="article quality gate failed: " + "; ".join(issues),
            **payload,
            suggested_next_operation="article.review_quality",
            suggested_params={"article_id": article.id},
        )
    return OperationResult.success(message=f"prepared wechat payload for {article.id}", **payload)
