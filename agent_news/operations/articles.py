"""Article atomic operations.

These wrap the existing article repository CRUD so agents can stay on the
uniform Operation Registry surface instead of mixing REST calls and operations.
"""

from __future__ import annotations

from ..content.wechat_payload import prepare_wechat_payload as build_wechat_payload
from ..db import get_repository
from ..models.operation import OperationResult
from .base import operation


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
    name="article.prepare_wechat_payload",
    category="article",
    description="只读：将文章转换成微信填写参数，不打开浏览器。",
    params={"article_id": "文章 ID", "cover_prompt": "可选封面提示词"},
)
def prepare_wechat_payload(ctx, article_id: str, cover_prompt: str = "") -> OperationResult:
    article = get_repository().get_article(article_id)
    if article is None:
        return OperationResult.failure(message=f"article '{article_id}' not found")
    payload = build_wechat_payload(article, cover_prompt=cover_prompt)
    missing = payload.get("missing_required") or []
    if missing:
        return OperationResult.failure(
            message="article missing required fields for wechat fill: " + ", ".join(missing),
            **payload,
            suggested_next_operation="article.update",
            suggested_params={"article_id": article.id, "fields": {field: "" for field in missing}},
        )
    return OperationResult.success(message=f"prepared wechat payload for {article.id}", **payload)
