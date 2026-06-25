"""WeChat editor fill operations — title / author / digest / body.

Each operation runs inside BROWSER_MANAGER.with_session. The editor must be
open first. paste_body writes rich HTML first and uses clipboard paste as a
fallback for long content.
"""

from __future__ import annotations

from ...browser import BROWSER_MANAGER, default_wechat_channel, get_selectors
from ...browser.dom import (
    clipboard_paste_into_element,
    dismiss_wechat_hover_popovers,
    page_url,
    pick_required_selector,
    pick_selector,
    read_locator_value,
    write_plain_field,
)
from ...content.wechat_format import (
    normalize_markdown_newlines,
    prepare_wechat_body,
)
from ...models.operation import OperationResult
from ..base import operation

_CHANNEL = default_wechat_channel()


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


def _require_editor(page) -> OperationResult | None:
    url = page_url(page)
    if "action=edit" not in url and "appmsg_edit" not in url:
        return OperationResult.failure(
            message="当前不在编辑页——请先调用 wechat.open_new_editor 或 wechat.open_existing_draft",
            url=url,
        )
    return None


def _fill_title_on_page(page, text: str) -> OperationResult:
    selector = write_plain_field(page, _selectors("title_input"), text[:64], field_label="title")
    actual = read_locator_value(page, selector)
    return OperationResult.success(message=f"标题已填写：{actual[:40]}", selector=selector, value=actual)


def _fill_author_on_page(page, text: str, *, allow_platform_default: bool = False) -> OperationResult:
    author = text[:8]
    selector = write_plain_field(page, _selectors("author_input"), author, field_label="author")
    actual = read_locator_value(page, selector)
    if actual.strip() != author.strip():
        if allow_platform_default and actual.strip():
            return OperationResult.success(
                message=f"微信保留平台默认作者：{actual}",
                selector=selector, value=actual, expected=author,
                platform_default=True,
            )
        return OperationResult.failure(
            message=f"作者写入后回读不一致：expected={author} actual={actual}",
            selector=selector, value=actual, expected=author,
        )
    return OperationResult.success(
        message=f"作者已填写：{actual}",
        selector=selector, value=actual, expected=author,
    )


def _paste_body_on_page(page, markdown: str, *, styled: bool = True) -> OperationResult:
    original_markdown = str(markdown or "")
    input_markdown = normalize_markdown_newlines(original_markdown)
    title_selector = pick_selector(page, _selectors("title_input"), timeout=1000)
    current_title = read_locator_value(page, title_selector).strip() if title_selector else ""
    prepared = prepare_wechat_body(input_markdown, title=current_title, styled=styled)
    body_html = str(prepared["body_html"])
    body_text = str(prepared["body_text"])
    minimum_length = max(10, min(len(body_text.strip()), 120))
    selector = pick_required_selector(page, _selectors("editor"), step_name="paste_body", timeout=5000)

    dismiss_wechat_hover_popovers(page)

    editor_loc = page.locator(selector).first
    # Use force=True to bypass any remaining pointer-event interception.
    editor_loc.click(timeout=4000, force=True)
    page.wait_for_timeout(300)

    # Strategy 1: set rich HTML directly. This preserves Markdown structure
    # and avoids the literal "\n\n" issue from command-line escaped input.
    try:
        page.evaluate(
            """({ selector, html, text }) => {
                const node = document.querySelector(selector);
                if (!node) return;
                node.focus();
                if (html) {
                    node.innerHTML = html;
                } else {
                    const blocks = String(text || '').split(/\\n+/).map((item) => item.trim()).filter(Boolean);
                    node.innerHTML = blocks.length
                        ? blocks.map((item) => '<p>' + item.replace(/[&<>"']/g, (ch) => ({
                            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                        }[ch])) + '</p>').join('')
                        : '<section><span leaf=""><br class="ProseMirror-trailingBreak"></span></section>';
                }
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(node);
                range.collapse(false);
                selection.removeAllRanges();
                selection.addRange(range);
                node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: node.innerText || '' }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            {"selector": selector, "html": body_html, "text": body_text},
        )
        page.wait_for_timeout(900)
        actual = read_locator_value(page, selector, rich_text=True)
        if len(actual.strip()) >= minimum_length:
            return OperationResult.success(
                message=f"正文已写入（innerHTML）：{len(actual)} 字符",
                selector=selector,
                char_count=len(actual),
                strategy="innerHTML",
                styled=styled,
                normalized_newlines=input_markdown != original_markdown,
                stripped_title=bool(prepared["stripped_title"]),
                title=current_title,
            )
    except Exception:
        pass

    # Strategy 2: browser editing command. ProseMirror often tracks DOM edits
    # through input events better when content is inserted as an editing action.
    try:
        page.evaluate(
            """({ selector, html, text }) => {
                const node = document.querySelector(selector);
                if (!node) return;
                node.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(node);
                selection.removeAllRanges();
                selection.addRange(range);
                document.execCommand('delete', false, null);
                const safeHtml = html || String(text || '')
                    .split(/\\n+/)
                    .map((item) => item.trim())
                    .filter(Boolean)
                    .map((item) => '<p>' + item.replace(/[&<>"']/g, (ch) => ({
                        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                    }[ch])) + '</p>')
                    .join('');
                if (safeHtml) {
                    document.execCommand('insertHTML', false, safeHtml);
                } else {
                    document.execCommand('insertText', false, String(text || ''));
                }
                node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: node.innerText || '' }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            {"selector": selector, "html": body_html, "text": body_text},
        )
        page.wait_for_timeout(900)
        actual = read_locator_value(page, selector, rich_text=True)
        if len(actual.strip()) >= minimum_length:
            return OperationResult.success(
                message=f"正文已写入（insertHTML）：{len(actual)} 字符",
                selector=selector,
                char_count=len(actual),
                strategy="insertHTML",
                styled=styled,
                normalized_newlines=input_markdown != original_markdown,
                stripped_title=bool(prepared["stripped_title"]),
                title=current_title,
            )
    except Exception:
        pass

    # Strategy 3: clipboard paste fallback.
    page.keyboard.press("Control+a")
    page.wait_for_timeout(200)
    page.keyboard.press("Delete")
    page.wait_for_timeout(200)
    clipboard_paste_into_element(page, selector, body_text)
    page.wait_for_timeout(800)
    actual = read_locator_value(page, selector, rich_text=True)
    if not actual.strip() or "从这里开始写正文" in actual:
        return OperationResult.failure(message="正文写入失败——innerHTML 和粘贴都没生效", selector=selector)
    return OperationResult.success(
        message=f"正文已粘贴：{len(actual)} 字符",
        selector=selector,
        char_count=len(actual),
        strategy="clipboard",
        styled=False,
        normalized_newlines=input_markdown != original_markdown,
        stripped_title=bool(prepared["stripped_title"]),
        title=current_title,
    )


@operation(
    name="wechat.fill_title",
    category="editor",
    description="在编辑页填写文章标题。要求当前已在编辑页。",
    params={"text": "必填，标题文本；兼容别名 title"},
)
def fill_title(ctx, text: str = "", title: str = "") -> OperationResult:
    value = str(text or title or "")
    if not value:
        return OperationResult.skip(message="title 为空，跳过")

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        return _fill_title_on_page(page, value)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"fill_title 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.fill_author",
    category="editor",
    description=(
        "在编辑页填写作者署名（最多8字符）。要求当前已在编辑页。"
        "默认严格回读校验；若允许平台保留默认作者，可传 allow_platform_default=True。"
    ),
    params={
        "text": "必填，作者名；兼容别名 author",
        "allow_platform_default": "bool，默认 False；True 时接受微信保留的默认作者",
    },
)
def fill_author(
    ctx,
    text: str = "",
    author: str = "",
    allow_platform_default: bool = False,
) -> OperationResult:
    value = str(text or author or "")
    if not value:
        return OperationResult.skip(message="author 为空，跳过")

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        return _fill_author_on_page(page, value, allow_platform_default=allow_platform_default)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"fill_author 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.fill_digest",
    category="editor",
    description="在编辑页填写摘要（最多120字符）。要求当前已在编辑页。",
    params={"text": "必填，摘要文本；兼容别名 digest"},
)
def fill_digest(ctx, text: str = "", digest: str = "") -> OperationResult:
    value = str(text or digest or "")
    if not value:
        return OperationResult.skip(message="digest 为空，跳过")

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        selector = write_plain_field(page, _selectors("digest_input"), value[:120], field_label="digest")
        actual = read_locator_value(page, selector)
        return OperationResult.success(message=f"摘要已填写：{actual[:40]}", selector=selector, value=actual)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"fill_digest 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.paste_body",
    category="editor",
    description=(
        "把文章正文写入编辑器正文区（ProseMirror）。默认把 Markdown 转成微信富文本 HTML，"
        "支持标题、段落、列表、引用、加粗、行距和字号；失败时剪贴板兜底。要求当前已在编辑页。"
    ),
    params={
        "markdown": "必填，正文 Markdown；兼容别名 body_markdown/body；支持真实换行，也兼容命令行传入的 \\n",
        "styled": "bool，默认 True；True 时转成带基础排版的微信 HTML",
    },
)
def paste_body(
    ctx,
    markdown: str = "",
    body_markdown: str = "",
    body: str = "",
    styled: bool = True,
) -> OperationResult:
    value = str(markdown or body_markdown or body or "")
    if not value:
        return OperationResult.skip(message="body 为空，跳过")

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        return _paste_body_on_page(page, value, styled=styled)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"paste_body 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.fill_editor_required",
    category="editor",
    description=(
        "一次填写编辑区必填三件套：标题、作者、正文。"
        "适合“上传文章/保存草稿/直接发表”这类意图，避免只填标题和正文漏掉作者。"
    ),
    params={
        "title": "必填，文章标题",
        "author": "必填，作者署名，微信最多8字符",
        "body_markdown": "必填，正文 Markdown；不要把文章标题放进正文",
        "styled": "bool，默认 True；正文转成微信富文本 HTML",
        "allow_platform_default": "bool，默认 False；True 时接受微信保留的默认作者",
    },
)
def fill_editor_required(
    ctx,
    title: str,
    author: str,
    body_markdown: str,
    styled: bool = True,
    allow_platform_default: bool = False,
) -> OperationResult:
    missing_params = []
    if not str(title or "").strip():
        missing_params.append("title")
    if not str(author or "").strip():
        missing_params.append("author")
    if not str(body_markdown or "").strip():
        missing_params.append("body_markdown")
    if missing_params:
        return OperationResult.failure(
            message="编辑区必填参数缺失：" + "、".join(missing_params),
            missing=missing_params,
        )

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard

        title_result = _fill_title_on_page(page, title)
        if title_result.status == "failed":
            return OperationResult.failure(
                message="标题填写失败：" + title_result.message,
                failed_step="title",
                step_results={"title": title_result.model_dump()},
            )

        author_result = _fill_author_on_page(
            page, author, allow_platform_default=allow_platform_default
        )
        if author_result.status == "failed":
            return OperationResult.failure(
                message="作者填写失败：" + author_result.message,
                failed_step="author",
                step_results={
                    "title": title_result.model_dump(),
                    "author": author_result.model_dump(),
                },
            )

        body_result = _paste_body_on_page(page, body_markdown, styled=styled)
        if body_result.status == "failed":
            return OperationResult.failure(
                message="正文填写失败：" + body_result.message,
                failed_step="body",
                step_results={
                    "title": title_result.model_dump(),
                    "author": author_result.model_dump(),
                    "body": body_result.model_dump(),
                },
            )

        return OperationResult.success(
            message="编辑区必填项已填写：标题、作者、正文",
            title=title_result.state.get("value", ""),
            author=author_result.state.get("value", ""),
            body_char_count=body_result.state.get("char_count", 0),
            body_strategy=body_result.state.get("strategy", ""),
            step_results={
                "title": title_result.model_dump(),
                "author": author_result.model_dump(),
                "body": body_result.model_dump(),
            },
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"fill_editor_required 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.inspect_editor",
    category="editor",
    description="只读：检查当前编辑页各字段的现有内容，不做任何修改。",
    params={},
)
def inspect_editor(ctx) -> OperationResult:
    def _run(_context, page):
        url = page_url(page)
        state = {}
        for key, rich in (("title_input", False), ("author_input", False),
                          ("digest_input", False), ("editor", True)):
            sel = pick_selector(page, _selectors(key), timeout=2000)
            state[key] = read_locator_value(page, sel, rich_text=rich)[:500] if sel else ""
        return OperationResult.success(message="已读取编辑页当前内容", url=url, **state)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"inspect_editor 失败: {type(e).__name__}: {e}")
