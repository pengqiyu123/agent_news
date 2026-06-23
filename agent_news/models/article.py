"""Article & material models.

Two levels, mirroring the old project's proven dual-level brief design
(rule material vs article finished), but simplified:

- Material: the research/source pack (facts, quotes, timeline, sources)
- Article : the finished, publishable piece (title, markdown body, digest)

An Article references its source Material but can exist standalone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


ArticleLevel = Literal["material", "article"]
ArticleStage = Literal["draft", "synced", "published", "failed"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Material(BaseModel):
    """Research / source pack — the "what happened" record, not for publishing."""

    id: str
    title: str = ""
    facts: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class Article(BaseModel):
    """Finished, publishable piece."""

    id: str
    material_id: str | None = None        # optional link to source material
    title: str
    digest: str = ""                       # 摘要
    body_markdown: str = ""                # 正文
    author: str = ""                       # 作者署名
    level: ArticleLevel = "article"
    stage: ArticleStage = "draft"          # draft / synced / published / failed
    # Platform delivery tracking (populated after publish operations).
    wechat_draft_url: str | None = None
    wechat_appmsg_id: str | None = None
    last_error: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    published_at: str | None = None


class CreateArticleRequest(BaseModel):
    """Body of POST /api/articles."""

    title: str
    digest: str = ""
    body_markdown: str = ""
    author: str = ""
    material_id: str | None = None


class ArticleResponse(BaseModel):
    item: Article


class ArticleListResponse(BaseModel):
    items: list[Article]
    total: int
