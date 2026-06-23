"""Article routes — CRUD for finished articles and source materials.

These exist in phase 1 so the SQLite layer is exercised end-to-end through
HTTP. The atomic-operation routes (the heart of the project) come in phase 2.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import get_repository
from ..models import (
    Article,
    ArticleListResponse,
    ArticleResponse,
    CreateArticleRequest,
)

router = APIRouter(tags=["articles"])


@router.post("/api/articles", response_model=ArticleResponse)
def create_article(req: CreateArticleRequest) -> ArticleResponse:
    repo = get_repository()
    article = repo.create_article(
        title=req.title,
        digest=req.digest,
        body_markdown=req.body_markdown,
        author=req.author,
        material_id=req.material_id,
    )
    return ArticleResponse(item=article)


@router.get("/api/articles", response_model=ArticleListResponse)
def list_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ArticleListResponse:
    repo = get_repository()
    offset = (page - 1) * page_size
    items, total = repo.list_articles(limit=page_size, offset=offset)
    return ArticleListResponse(items=items, total=total)


@router.get("/api/articles/{article_id}", response_model=ArticleResponse)
def get_article(article_id: str) -> ArticleResponse:
    repo = get_repository()
    article = repo.get_article(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return ArticleResponse(item=article)


@router.put("/api/articles/{article_id}", response_model=ArticleResponse)
def update_article(article_id: str, fields: dict) -> ArticleResponse:
    repo = get_repository()
    article = repo.update_article(article_id, **fields)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return ArticleResponse(item=article)


@router.delete("/api/articles/{article_id}")
def delete_article(article_id: str) -> dict:
    repo = get_repository()
    ok = repo.delete_article(article_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return {"ok": True, "deleted": article_id}
