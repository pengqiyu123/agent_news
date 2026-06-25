from __future__ import annotations

from agent_news.intel.writing_guide import build_article_writing_guide


def test_writing_guide_keeps_legacy_title_and_article_contract():
    guide = build_article_writing_guide()

    required_fragments = [
        "标题策略",
        "前 14 字",
        "最终只保留 1 个定稿标题",
        "article.title",
        "开头 80 字",
        "短讯合集",
        "值得关注",
        "不要向用户抛标题选择题",
    ]
    for fragment in required_fragments:
        assert fragment in guide


def test_writing_guide_is_platform_ready_not_chat_selection_flow():
    guide = build_article_writing_guide()

    assert "可以在内部" in guide
    assert "2-3 个候选" in guide
    assert "最终只保留 1 个定稿标题" in guide
    assert "不要向用户抛标题选择题" in guide
