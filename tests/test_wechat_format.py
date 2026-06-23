from __future__ import annotations

from agent_news.content.wechat_format import (
    markdown_to_plain_text,
    markdown_to_wechat_html,
    normalize_markdown_newlines,
    prepare_wechat_body,
    strip_leading_markdown_title,
)


def test_normalize_markdown_newlines_accepts_cli_escaped_newlines():
    text = "测试正文标题\\n\\n这是第一段。\\n\\n这是第二段。"

    normalized = normalize_markdown_newlines(text)

    assert "\\n" not in normalized
    assert normalized == "测试正文标题\n\n这是第一段。\n\n这是第二段。"


def test_markdown_to_wechat_html_adds_basic_article_formatting():
    html = markdown_to_wechat_html(
        "# 小标题\n\n这是**重点**正文。\n\n- 第一条\n- 第二条\n\n> 引用内容",
        include_wrapper=True,
    )

    assert "font-size:15px" in html
    assert "line-height:1.85" in html
    assert "<h1" in html
    assert "font-weight:700" in html
    assert "<strong" in html
    assert "<ul" in html
    assert "<blockquote" in html


def test_markdown_to_plain_text_does_not_leak_markup():
    text = markdown_to_plain_text("# 标题\n\n这是**重点**正文。")

    assert text.startswith("标题")
    assert "<strong" not in text
    assert "**" not in text


def test_strip_leading_markdown_title_removes_article_h1():
    text = strip_leading_markdown_title("# 文章标题\n\n这是正文第一段。")

    assert text == "这是正文第一段。"


def test_strip_leading_markdown_title_keeps_body_subheadings():
    text = strip_leading_markdown_title("## 正文小标题\n\n这是正文第一段。")

    assert text.startswith("## 正文小标题")


def test_strip_leading_markdown_title_removes_current_title_plain_line():
    text = strip_leading_markdown_title(
        "Claude Design 额度翻倍，Anthropic 开始抢 AI 工作台\n\n这是正文第一段。",
        title="Claude Design 额度翻倍，Anthropic 开始抢 AI 工作台",
    )

    assert text == "这是正文第一段。"


def test_body_html_after_title_strip_has_no_h1_for_article_title():
    body = strip_leading_markdown_title("# 文章标题\\n\\n## 正文小标题\\n\\n这是**重点**正文。")
    html = markdown_to_wechat_html(body, include_wrapper=True)

    assert "<h1" not in html
    assert "<h2" in html
    assert "文章标题" not in html
    assert "正文小标题" in html


def test_prepare_wechat_body_is_body_only_even_when_markdown_contains_title():
    prepared = prepare_wechat_body(
        "# Claude Design 额度翻倍，Anthropic 开始抢 AI 工作台\\n\\n## 影响\\n\\n这是**正文**。",
        title="Claude Design 额度翻倍，Anthropic 开始抢 AI 工作台",
        styled=True,
    )

    assert prepared["stripped_title"] is True
    assert "Claude Design 额度翻倍" not in str(prepared["body_html"])
    assert "<h1" not in str(prepared["body_html"])
    assert "<h2" in str(prepared["body_html"])
    assert str(prepared["body_text"]).startswith("影响")
