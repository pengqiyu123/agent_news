"""Article quality gates before platform delivery.

The project intentionally lets an external agent write the article. This module
does not write or rewrite text; it checks whether the stored article is mature
enough to hand to a publishing operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models.article import Article
from ..models.intel import EventDeepDive


LONG_ARTICLE_MIN_CHARS = 800
LONG_ARTICLE_MAX_CHARS = 1800
DIGEST_MIN_CHARS = 600
DIGEST_MAX_CHARS = 1800
MIN_READY_FACTS = 5
MIN_READY_SOURCES = 2
MIN_DIGEST_ITEMS = 5
MIN_DIGEST_TRANSITIONS = 4

_SENTENCE_PATTERN = re.compile(r"[。！？；.!?;]+")
_PARAGRAPH_PATTERN = re.compile(r"\n\s*\n|\n")
_URL_PATTERN = re.compile(r"https?://\S+")
_NUMBERED_HEADING_PATTERN = re.compile(r"(?m)^#{1,3}\s*\d+[\.\、]")
_DIGEST_TRANSITIONS = ("首先", "然后", "接下来", "再说", "最后")
_INTERNAL_MARKERS = (
    "核心事实",
    "这意味着什么",
    "还不确定什么",
    "来源链接",
    "参考资料",
    "素材包",
    "后台栏目",
)
_AI_STYLE_PATTERNS = (
    "在人工智能技术飞速发展的今天",
    "随着大模型技术的不断演进",
    "在当今快速发展的",
    "值得关注",
    "引发关注",
    "引发热议",
    "引发了广泛关注",
    "具有重要意义",
    "不言而喻",
    "令人兴奋的是",
    "值得注意的是",
    "接下来我们来看看",
    "让我们深入探讨",
    "下面将详细介绍",
    "本文将从以下几个方面展开",
    "综上所述",
    "总而言之",
    "总的来说",
    "不仅是.*更是",
    "赋能",
    "深耕",
    "布局",
    "底层逻辑",
    "不可或缺",
    "举足轻重",
    "颠覆性",
    "全新升级",
)


@dataclass(frozen=True)
class ArticleQualityReport:
    """Computed article-quality state returned to agents."""

    passed: bool
    content_form: str
    character_count: int
    sentence_count: int
    paragraph_count: int
    material_ready: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    suggested_next_operation: str = "article.update"

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "content_form": self.content_form,
            "character_count": self.character_count,
            "sentence_count": self.sentence_count,
            "paragraph_count": self.paragraph_count,
            "material_ready": self.material_ready,
            "issues": self.issues,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "suggested_next_operation": self.suggested_next_operation,
        }


def _strip_markdown(text: str) -> str:
    text = re.sub(r"(?m)^#{1,6}\s*", "", text or "")
    text = re.sub(r"[*_>`\-]+", "", text)
    return text.strip()


def _count_sentences(text: str) -> int:
    return len([item for item in _SENTENCE_PATTERN.split(text) if len(item.strip()) > 2])


def _count_paragraphs(text: str) -> int:
    return len([item for item in _PARAGRAPH_PATTERN.split(text or "") if item.strip()])


def _banned_phrase_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in _AI_STYLE_PATTERNS:
        try:
            if re.search(pattern, text):
                hits.append(pattern)
        except re.error:
            if pattern in text:
                hits.append(pattern)
    return hits


def _material_metrics(deep_dives: list[EventDeepDive]) -> dict:
    if not deep_dives:
        return {
            "material_ids": [],
            "material_count": 0,
            "ready_material_count": 0,
            "fact_count": 0,
            "quote_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "materials": [],
        }
    materials = []
    ready_count = 0
    for dive in deep_dives:
        ready = dive.status == "ready" and dive.success_count >= 1 and len(dive.facts) >= 1
        if ready:
            ready_count += 1
        materials.append(
            {
                "material_id": dive.id,
                "event_id": dive.event_id,
                "deep_dive_status": dive.status,
                "fact_count": len(dive.facts),
                "quote_count": len(dive.quotes),
                "success_count": dive.success_count,
                "failed_count": dive.failed_count,
                "ready_for_digest_item": ready,
                "worth_to_brief": bool(dive.worthiness.get("worth_to_brief")),
            }
        )
    return {
        "material_ids": [dive.id for dive in deep_dives],
        "material_count": len(deep_dives),
        "ready_material_count": ready_count,
        "fact_count": sum(len(dive.facts) for dive in deep_dives),
        "quote_count": sum(len(dive.quotes) for dive in deep_dives),
        "success_count": sum(dive.success_count for dive in deep_dives),
        "failed_count": sum(dive.failed_count for dive in deep_dives),
        "materials": materials,
    }


def _material_is_ready_for_long_article(deep_dives: list[EventDeepDive]) -> bool:
    if not deep_dives:
        return False
    dive = deep_dives[0]
    if dive.status != "ready":
        return False
    return dive.success_count >= MIN_READY_SOURCES and len(dive.facts) >= MIN_READY_FACTS


def _material_is_ready_for_digest(deep_dives: list[EventDeepDive]) -> bool:
    if len(deep_dives) < MIN_DIGEST_ITEMS:
        return False
    ready_items = [
        dive for dive in deep_dives
        if dive.status == "ready" and dive.success_count >= 1 and len(dive.facts) >= 1
    ]
    return len(ready_items) >= MIN_DIGEST_ITEMS and sum(len(dive.facts) for dive in ready_items) >= MIN_READY_FACTS


def infer_content_form(article: Article) -> str:
    """Infer the intended article form from title/body signals."""

    text = f"{article.title}\n{article.digest}\n{article.body_markdown}"
    if "5条" in text or "5 条" in text or "五条" in text or "短讯合集" in text or "科技要闻" in text:
        return "five_item_digest"
    return "long_article"


def review_article_quality(
    article: Article,
    *,
    deep_dive: EventDeepDive | None = None,
    deep_dives: list[EventDeepDive] | None = None,
) -> ArticleQualityReport:
    """Check whether an article is ready for WeChat payload preparation."""

    resolved_dives = list(deep_dives or [])
    if deep_dive is not None and not resolved_dives:
        resolved_dives = [deep_dive]
    body = article.body_markdown or ""
    plain = _strip_markdown(body)
    char_count = len(plain)
    sentence_count = _count_sentences(plain)
    paragraph_count = _count_paragraphs(body)
    content_form = infer_content_form(article)
    issues: list[str] = []
    warnings: list[str] = []
    metrics = _material_metrics(resolved_dives)
    material_ready = _material_is_ready_for_digest(resolved_dives) if content_form == "five_item_digest" else _material_is_ready_for_long_article(resolved_dives)

    if content_form == "five_item_digest":
        if not material_ready:
            issues.append("素材不足：5 条短讯合集必须绑定至少 5 个 ready deep dive；每条至少 1 个成功来源和 1 条事实")
        if char_count < DIGEST_MIN_CHARS:
            issues.append(f"短讯合集正文过短：至少 {DIGEST_MIN_CHARS} 字")
        if char_count > DIGEST_MAX_CHARS:
            warnings.append(f"短讯合集正文偏长：建议不超过 {DIGEST_MAX_CHARS} 字")
        transition_count = sum(1 for token in _DIGEST_TRANSITIONS if token in plain)
        metrics["digest_transition_count"] = transition_count
        if transition_count < MIN_DIGEST_TRANSITIONS:
            issues.append("短讯合集必须用“首先/然后/接下来/再说/最后”自然串联 5 条")
        if _NUMBERED_HEADING_PATTERN.search(body):
            issues.append("平台短讯稿不能保留 ## 1. 这类本地素材编号标题")
    else:
        if not material_ready:
            issues.append("素材不足：单事件长文必须绑定 ready deep dive，且至少 2 个成功来源、5 条事实")
        if char_count < LONG_ARTICLE_MIN_CHARS:
            issues.append(f"长文正文过短：至少 {LONG_ARTICLE_MIN_CHARS} 字")
        if char_count > LONG_ARTICLE_MAX_CHARS:
            warnings.append(f"长文正文偏长：建议不超过 {LONG_ARTICLE_MAX_CHARS} 字")
        if sentence_count < 12:
            issues.append("长文信息密度不足：句子数量过少")

    marker_hits = [marker for marker in _INTERNAL_MARKERS if marker in body]
    if marker_hits:
        issues.append("平台稿仍包含后台素材字段：" + "、".join(marker_hits))

    if _URL_PATTERN.search(body):
        issues.append("平台稿正文不能包含裸 URL；来源应留在素材/审计中")

    banned_hits = _banned_phrase_hits(body)
    metrics["banned_phrase_hits"] = banned_hits
    metrics["banned_phrase_count"] = len(banned_hits)
    if len(banned_hits) > 3:
        issues.append("AI 味高频词过多：" + "、".join(banned_hits[:6]))

    if paragraph_count < 4:
        warnings.append("段落数量偏少，建议拆分出更清晰的阅读节奏")

    passed = not issues
    next_op = "article.prepare_wechat_payload" if passed else "article.update"
    return ArticleQualityReport(
        passed=passed,
        content_form=content_form,
        character_count=char_count,
        sentence_count=sentence_count,
        paragraph_count=paragraph_count,
        material_ready=material_ready,
        issues=issues,
        warnings=warnings,
        metrics=metrics,
        suggested_next_operation=next_op,
    )
