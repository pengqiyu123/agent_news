"""Convert stored articles into WeChat editor payloads."""

from __future__ import annotations

from ..models.article import Article


def default_cover_prompt(article: Article) -> str:
    title = (article.title or "").strip()
    if not title:
        return "一件科技产品放在干净桌面上的写实产品摄影图"
    return f"一个与“{title[:24]}”主题相关的物品或办公场景写实图片，不包含文字"


def prepare_wechat_payload(article: Article, *, cover_prompt: str = "") -> dict:
    prompt = (cover_prompt or "").strip() or default_cover_prompt(article)
    missing_required = []
    if not (article.title or "").strip():
        missing_required.append("title")
    if not (article.author or "").strip():
        missing_required.append("author")
    if not (article.body_markdown or "").strip():
        missing_required.append("body_markdown")
    suggested_steps = []
    if not missing_required:
        suggested_steps = [
            {
                "op": "wechat.fill_editor_required",
                "params": {
                    "title": article.title,
                    "author": article.author,
                    "body_markdown": article.body_markdown,
                },
            },
            {"op": "wechat.fill_digest", "params": {"text": article.digest}},
            {"op": "wechat.generate_ai_cover", "params": {"prompt": prompt}},
        ]

    return {
        "article_id": article.id,
        "title": article.title,
        "author": article.author,
        "digest": article.digest,
        "body_markdown": article.body_markdown,
        "cover_prompt": prompt,
        "missing_required": missing_required,
        "ready_for_wechat_fill": not missing_required,
        "suggested_steps": suggested_steps,
    }
