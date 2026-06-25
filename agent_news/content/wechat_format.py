"""Markdown to WeChat-editor HTML.

It keeps the external operation atomic:
agents still call wechat.paste_body(markdown=...), while this module handles
escaped newlines and presentation-safe HTML.
"""

from __future__ import annotations

import re
from html import escape, unescape

WECHAT_WRAPPER_STYLE = (
    "font-size:15px;line-height:1.85;color:#222;"
    "letter-spacing:0;text-align:left;"
)


def normalize_markdown_newlines(markdown: str) -> str:
    text = str(markdown or "")
    if not text:
        return ""

    actual_newlines = text.count("\n")
    escaped_newlines = text.count("\\n") + text.count("`n")
    if escaped_newlines and (actual_newlines == 0 or escaped_newlines >= max(2, actual_newlines * 2)):
        text = (
            text.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\r", "\n")
            .replace("`r`n", "\n")
            .replace("`n", "\n")
            .replace("`r", "\n")
        )
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_leading_markdown_title(markdown: str, title: str = "") -> str:
    """Remove a leading Markdown heading before writing into the body editor.

    WeChat's title is a separate editor field. The body writer must not put
    the article title into the rich-text body area.
    """
    lines = normalize_markdown_newlines(markdown).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    first = lines[0].strip()
    expected = str(title or "").strip()
    if not first.startswith("#"):
        if expected and first == expected:
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        return "\n".join(lines).strip()

    heading_match = re.match(r"^(#{1,6})\s+(.+)$", first)
    if not heading_match:
        return "\n".join(lines).strip()

    heading_level = len(heading_match.group(1))
    heading = heading_match.group(2).strip()
    # If title is known, only remove a matching heading. If title is unknown,
    # remove a leading H1 heading because fill_title owns the article title.
    if heading_level == 1 and (not expected or heading == expected):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _render_inline_html(text: str) -> str:
    value = str(text or "")
    result: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        if value.startswith("**", index):
            end = value.find("**", index + 2)
            if end > index + 2:
                result.append(
                    "<strong style=\"font-weight:700;color:#111;\">"
                    + _render_inline_html(value[index + 2:end])
                    + "</strong>"
                )
                index = end + 2
                continue
        if value.startswith("__", index):
            end = value.find("__", index + 2)
            if end > index + 2:
                result.append(
                    "<strong style=\"font-weight:700;color:#111;\">"
                    + _render_inline_html(value[index + 2:end])
                    + "</strong>"
                )
                index = end + 2
                continue
        if value[index] == "`":
            end = value.find("`", index + 1)
            if end > index + 1:
                result.append(
                    "<code style=\"font-size:13px;background:#f5f6f7;"
                    "padding:1px 4px;border-radius:3px;\">"
                    + escape(value[index + 1:end])
                    + "</code>"
                )
                index = end + 1
                continue
        if value[index] == "[":
            match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", value[index:])
            if match:
                label = match.group(1).strip()
                href = match.group(2).strip()
                result.append(
                    f"<a href=\"{escape(href, quote=True)}\" "
                    "style=\"color:#576b95;text-decoration:none;\">"
                    f"{_render_inline_html(label)}</a>"
                )
                index += match.end()
                continue
        if value[index] == "*" and not value.startswith("**", index):
            end = value.find("*", index + 1)
            if end > index + 1:
                result.append(f"<em>{_render_inline_html(value[index + 1:end])}</em>")
                index = end + 1
                continue
        result.append(escape(value[index]))
        index += 1
    return "".join(result)


def _paragraph_html(lines: list[str]) -> str:
    return (
        "<p style=\"margin:0 0 14px;font-size:15px;line-height:1.85;color:#222;\">"
        + "<br/>".join(_render_inline_html(item) for item in lines)
        + "</p>"
    )


def markdown_to_wechat_html(markdown: str, *, include_wrapper: bool = True) -> str:
    blocks: list[str] = []
    paragraph_lines: list[str] = []
    quote_lines: list[str] = []
    unordered_items: list[str] = []
    ordered_items: list[str] = []
    code_lines: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            blocks.append(_paragraph_html(paragraph_lines))
            paragraph_lines = []

    def flush_quotes() -> None:
        nonlocal quote_lines
        if quote_lines:
            blocks.append(
                "<blockquote style=\"margin:0 0 14px;padding:8px 12px;"
                "border-left:3px solid #d0d7de;background:#f7f8fa;color:#4c4d4e;\">"
                + _paragraph_html(quote_lines)
                + "</blockquote>"
            )
            quote_lines = []

    def flush_unordered() -> None:
        nonlocal unordered_items
        if unordered_items:
            blocks.append(
                "<ul style=\"margin:0 0 14px;padding-left:20px;color:#222;\">"
                + "".join(
                    "<li style=\"margin:0 0 6px;line-height:1.8;\">"
                    + _render_inline_html(item)
                    + "</li>"
                    for item in unordered_items
                )
                + "</ul>"
            )
            unordered_items = []

    def flush_ordered() -> None:
        nonlocal ordered_items
        if ordered_items:
            blocks.append(
                "<ol style=\"margin:0 0 14px;padding-left:20px;color:#222;\">"
                + "".join(
                    "<li style=\"margin:0 0 6px;line-height:1.8;\">"
                    + _render_inline_html(item)
                    + "</li>"
                    for item in ordered_items
                )
                + "</ol>"
            )
            ordered_items = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            blocks.append(
                "<pre style=\"margin:0 0 14px;padding:10px 12px;background:#f5f6f7;"
                "border-radius:4px;white-space:pre-wrap;font-size:13px;line-height:1.65;\">"
                f"<code>{escape(chr(10).join(code_lines))}</code></pre>"
            )
            code_lines = []

    def flush_all() -> None:
        flush_paragraph()
        flush_quotes()
        flush_unordered()
        flush_ordered()
        flush_code()

    for raw in normalize_markdown_newlines(markdown).splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                flush_all()
                in_code_block = True
            continue
        if in_code_block:
            code_lines.append(raw.rstrip())
            continue
        if not stripped:
            flush_all()
            continue

        ordered_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        unordered_match = re.match(r"^[-*+]\s+(.*)$", stripped)

        if stripped.startswith("#"):
            flush_all()
            level = min(3, max(1, len(stripped) - len(stripped.lstrip("#"))))
            title = stripped[level:].strip()
            size = {1: 20, 2: 17, 3: 16}[level]
            blocks.append(
                f"<h{level} style=\"margin:0 0 14px;font-size:{size}px;"
                "line-height:1.45;font-weight:700;color:#111;\">"
                f"{_render_inline_html(title)}</h{level}>"
            )
            continue
        if stripped.startswith("> "):
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            quote_lines.append(stripped[2:].strip())
            continue
        if unordered_match:
            flush_paragraph()
            flush_quotes()
            flush_ordered()
            unordered_items.append(unordered_match.group(1).strip())
            continue
        if ordered_match:
            flush_paragraph()
            flush_quotes()
            flush_unordered()
            ordered_items.append(ordered_match.group(2).strip())
            continue
        flush_quotes()
        flush_unordered()
        flush_ordered()
        paragraph_lines.append(stripped)

    flush_all()
    content = "".join(blocks) or "<p><br/></p>"
    if include_wrapper:
        return f"<section style=\"{WECHAT_WRAPPER_STYLE}\">{content}</section>"
    return content


def markdown_to_plain_text(markdown: str, *, limit: int = 12000) -> str:
    html = markdown_to_wechat_html(markdown, include_wrapper=False)
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</(p|h1|h2|h3|li|blockquote|pre|ul|ol|section)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def prepare_wechat_body(markdown: str, *, title: str = "", styled: bool = True) -> dict[str, object]:
    """Prepare body-only content for the WeChat body editor.

    The WeChat title editor is separate from the body editor. This helper is
    the single gate that strips a duplicated article title before formatting.
    """
    input_markdown = normalize_markdown_newlines(markdown)
    body_markdown = strip_leading_markdown_title(input_markdown, title=title)
    body_html = markdown_to_wechat_html(body_markdown, include_wrapper=True) if styled else ""
    body_text = markdown_to_plain_text(body_markdown) if styled else body_markdown
    return {
        "input_markdown": input_markdown,
        "body_markdown": body_markdown,
        "body_html": body_html,
        "body_text": body_text,
        "stripped_title": body_markdown != input_markdown,
    }
