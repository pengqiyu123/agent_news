"""WeChat existing-draft operations.

The draft card location and edit-button click logic are part of this project's
own WeChat automation contract. Do not reinvent these DOM selectors without a
real-browser regression test.
"""

from __future__ import annotations

import re

from ...browser import BROWSER_MANAGER, WECHAT_HOME_URL, default_wechat_channel, get_selectors
from ...browser.dom import page_url, pick_selector
from ...models.operation import OperationResult
from ..base import operation

_CHANNEL = default_wechat_channel()


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


def _open_draft_box_on_page(page) -> bool:
    """Navigate to the draft box: home → expand content_manage → click draft_box.

    Returns True if URL contains action=list_card.
    """
    page.goto(WECHAT_HOME_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    # step 1: expand content_manage sidebar
    cm_sel = pick_selector(page, _selectors("content_manage"), timeout=2500)
    if cm_sel:
        try:
            page.locator(cm_sel).first.click()
            page.wait_for_timeout(1200)
        except Exception:
            pass

    # step 2: click draft_box link (with goto fallback), confirm URL
    for selector in _selectors("draft_box"):
        try:
            locator = page.locator(selector).first
            try:
                locator.click(timeout=2000)
            except Exception:
                href = ""
                try:
                    href = str(locator.get_attribute("href", timeout=1200) or "").strip()
                except Exception:
                    pass
                if href and "action=list_card" in href:
                    target = href if href.startswith("http") else f"https://mp.weixin.qq.com{href}"
                    page.goto(target, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1200)
                else:
                    locator.click(timeout=2000, force=True)
            try:
                page.wait_for_url("**action=list_card**", timeout=8000)
            except Exception:
                page.wait_for_timeout(2500)
            if "action=list_card" in page_url(page):
                return True
        except Exception:
            continue
    return False


def _scrape_draft_items(page) -> list[dict]:
    """Scrape draft card titles + URLs from the draft box page.

    Selector: .publish_card_container, .weui-desktop-card.weui-desktop-publish,
    .weui-desktop-media__list-col .weui-desktop-card.
    """
    rows = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const results = [];
            const seenStable = new Set();
            const containers = Array.from(
                document.querySelectorAll(
                    '.publish_card_container, .weui-desktop-card.weui-desktop-publish, .weui-desktop-media__list-col .weui-desktop-card'
                )
            );
            const resolveTitleNode = (container) =>
                container.querySelector('.weui-desktop-publish__cover__title span') ||
                container.querySelector('.weui-desktop-publish__cover__title') ||
                container.querySelector('.weui-desktop-card__title') ||
                container.querySelector('a[title]');
            const resolveLinkNode = (container) =>
                container.querySelector('a.weui-desktop-publish__cover__title[href]') ||
                container.querySelector('.weui-desktop-publish__cover__title[href]') ||
                container.querySelector('a[href]');
            containers.forEach((container, index) => {
                const titleNode = resolveTitleNode(container);
                const linkNode = resolveLinkNode(container);
                const title = normalize(titleNode ? titleNode.textContent : '');
                const href = normalize(linkNode ? linkNode.getAttribute('href') : '');
                if (!title && !href) return;
                const normalizedHref = href && !href.startsWith('javascript:') ? (href.startsWith('/') ? window.location.origin + href : href) : '';
                let appmsgId = null;
                try {
                    if (normalizedHref) {
                        const parsed = new URL(normalizedHref, window.location.origin);
                        appmsgId = parsed.searchParams.get('appmsgid');
                    }
                } catch (_) {}
                const titleKey = title.toLowerCase();
                const stableKey = appmsgId ? 'appmsg:' + appmsgId : normalizedHref ? 'url:' + normalizedHref : '';
                const dedupeKey = stableKey || 'title:' + titleKey;
                if (seenStable.has(dedupeKey)) return;
                seenStable.add(dedupeKey);
                results.push({ title, url: normalizedHref, appmsg_id: appmsgId });
            });
            return results.slice(0, 80);
        }"""
    )
    return rows if isinstance(rows, list) else []


def _is_editor_url(url: str) -> bool:
    """Return whether a WeChat URL is an editor URL, not a preview/list page."""
    normalized = str(url or "")
    if not normalized:
        return False
    if "action=list_card" in normalized:
        return False
    if "action=preview" in normalized or "/s/" in normalized:
        return False
    return "media/appmsg_edit" in normalized or "action=edit" in normalized


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip().lower()


def _title_matches(left: str, right: str) -> bool:
    left_norm = _normalize_title(left).replace("：", ":")
    right_norm = _normalize_title(right).replace("：", ":")
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    shorter, longer = (left_norm, right_norm) if len(left_norm) <= len(right_norm) else (right_norm, left_norm)
    return len(shorter) >= 18 and (longer.startswith(shorter) or shorter in longer)


def _find_draft_by_title(items: list[dict], title: str) -> dict | None:
    if not title:
        return None
    for item in items:
        if _title_matches(str(item.get("title") or ""), title):
            return item
    needle = _normalize_title(title)
    for item in items:
        item_title = _normalize_title(str(item.get("title") or ""))
        if needle and item_title and (needle in item_title or item_title in needle):
            return item
    return None


def _click_edit_button_by_title(page, title: str) -> dict:
    """Click the edit button on a draft card matching the title.

    Uses only the action area, avoiding title/cover/preview links.
    """
    compact_title = _normalize_title(title)
    result = page.evaluate(
        """({ title }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
            const titleMatches = (left, right) => {
                if (!left || !right) return false;
                left = left.replace(/[：:]/g, ":");
                right = right.replace(/[：:]/g, ":");
                if (left === right) return true;
                const shorter = left.length <= right.length ? left : right;
                const longer = left.length <= right.length ? right : left;
                return shorter.length >= 18 && (longer.startsWith(shorter) || longer.includes(shorter));
            };
            const rows = Array.from(document.querySelectorAll(
                '.publish_card_container, .weui-desktop-card.weui-desktop-publish, .weui-desktop-media__list-col .weui-desktop-card'
            ));
            for (const row of rows) {
                const titleNode =
                    row.querySelector('.weui-desktop-publish__cover__title span') ||
                    row.querySelector('.weui-desktop-publish__cover__title') ||
                    row.querySelector('.weui-desktop-card__title') ||
                    row.querySelector('a[title]');
                const rowTitle = normalize(titleNode ? titleNode.textContent : row.innerText || '');
                if (!titleMatches(rowTitle, title)) continue;

                row.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, view: window }));
                row.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true, cancelable: true, view: window }));

                const actionArea =
                    row.querySelector('.weui-desktop-card__action') ||
                    row.querySelector('.weui-desktop-publish__opr') ||
                    row;
                const wrappers = Array.from(actionArea.querySelectorAll(
                    '.weui-desktop-tooltip__wrp, .weui-desktop-popover__wrp, .weui-desktop-link'
                ));
                const editWrapper = wrappers.find((wrapper) => {
                    const text = normalize(wrapper.innerText || wrapper.textContent || '');
                    if (!text.includes('编辑')) return false;
                    if (text.includes('预览') || text.includes('删除') || text.includes('发表')) return false;
                    return !!wrapper.querySelector('a, button, [role="button"]');
                });

                let editButton = editWrapper
                    ? editWrapper.querySelector('a, button, [role="button"]')
                    : null;
                if (!editButton) {
                    const buttons = Array.from(actionArea.querySelectorAll(
                        'a.weui-desktop-icon20.weui-desktop-icon-btn, a.weui-desktop-icon-btn, button'
                    )).filter((button) => {
                        const text = normalize(button.innerText || button.textContent || button.getAttribute('title') || '');
                        const classes = String(button.className || '');
                        if (classes.includes('disable') || button.closest('.appmsg_publish_disable')) return false;
                        if (text.includes('预览') || text.includes('删除') || text.includes('发表')) return false;
                        return true;
                    });
                    editButton = buttons.length >= 2 ? buttons[1] : buttons[0] || null;
                }
                if (!editButton) {
                    return { ok: false, reason: 'edit_button_not_found', title: rowTitle };
                }

                editButton.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, view: window }));
                editButton.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                editButton.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                editButton.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                return { ok: true, reason: 'clicked', title: rowTitle };
            }
            return { ok: false, reason: 'draft_card_not_found', title: '' };
        }""",
        {"title": compact_title},
    )
    return result if isinstance(result, dict) else {"ok": False, "reason": "unknown"}


def _open_existing_draft_editor_on_page(context, page, title: str) -> OperationResult:
    """Open an existing draft editor using the current browser session.

    This helper is shared by the atomic navigation op and by the thin
    publish-existing-draft op. It must not call BROWSER_MANAGER.with_session;
    callers already hold the browser session lock.
    """
    if not title:
        return OperationResult.failure(message="title 为空，无法定位草稿")

    # 1. open draft box
    if not _open_draft_box_on_page(page):
        return OperationResult.failure(message=f"未能进入草稿箱（URL={page_url(page)}）")
    page.wait_for_timeout(1800)

    # 2. scrape draft items + match by title
    items = _scrape_draft_items(page)
    if not items:
        return OperationResult.failure(message="草稿箱为空或抓取失败")

    target = _find_draft_by_title(items, title)

    if not target:
        titles = [str(i.get("title") or "")[:30] for i in items[:5]]
        return OperationResult.failure(
            message=f"草稿箱未找到标题匹配「{title}」的草稿。前5条：{titles}",
            available_titles=titles,
        )

    target_title = str(target.get("title") or "").strip()
    target_url = str(target.get("url") or "").strip()

    # 3. open editor: if URL is an editor URL, goto directly; else click edit button
    if _is_editor_url(target_url):
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2200)
    else:
        click_result = _click_edit_button_by_title(page, target_title)
        if not click_result.get("ok"):
            return OperationResult.failure(
                message=f"点击编辑按钮失败：{click_result.get('reason')}（title={target_title}）",
                target_title=target_title,
            )
        page.wait_for_timeout(2500)

    # 4. converge to editor tab — POLL for up to 12s (editor opens in new tab)
    # The editor often opens in a new tab, so poll briefly for action=edit.
    import time as _time
    deadline = _time.time() + 12
    editor_page = None
    live_pages = []
    while _time.time() < deadline:
        live_pages = [p for p in context.pages if not p.is_closed()]
        for candidate in live_pages:
            cand_url = page_url(candidate)
            if "action=edit" in cand_url or "appmsg_edit" in cand_url:
                try:
                    candidate.wait_for_load_state("domcontentloaded", timeout=1500)
                except Exception:
                    pass
                editor_page = candidate
                break
        if editor_page:
            break
        try:
            page.wait_for_timeout(500)
        except Exception:
            break
    if editor_page is None:
        # fallback: pick last live page
        live_pages = [p for p in context.pages if not p.is_closed()]
        editor_page = live_pages[-1] if live_pages else page

    # close extra tabs, keep only the editor
    for candidate in live_pages:
        if candidate is not editor_page:
            try:
                candidate.close()
            except Exception:
                pass

    # Rebind the manager's working page to the editor tab.
    BROWSER_MANAGER._page = editor_page  # noqa: SLF001

    editor_url = page_url(editor_page)
    if "action=edit" in editor_url:
        return OperationResult.success(
            message=f"已打开草稿「{target_title}」的编辑页",
            url=editor_url, resident_page="editor", title=target_title,
        )
    return OperationResult.failure(
        message=f"点击编辑后未到达 action=edit 页面，当前 URL={editor_url}",
        url=editor_url, target_title=target_title,
    )


@operation(
    name="wechat.open_existing_draft",
    category="navigation",
    description=(
        "打开草稿箱里一个已有草稿进入编辑页。"
        "通过草稿标题定位目标草稿，点击其操作区编辑按钮（不点标题/封面/预览链接）。"
        "成功标志：最终 URL 含 action=edit。"
    ),
    params={"title": "必填，目标草稿的标题（部分匹配即可，18字以上模糊匹配）"},
)
def open_existing_draft(ctx, title: str) -> OperationResult:
    """Open an existing draft for editing."""
    if not title:
        return OperationResult.failure(message="title 为空，无法定位草稿")

    def _run(context, page):
        return _open_existing_draft_editor_on_page(context, page, title)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"open_existing_draft 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.list_drafts",
    category="navigation",
    description="只读：列出草稿箱里的草稿标题（用 .publish_card_container 选择器），用于 AI 决定打开哪个。",
    params={"limit": "最多返回多少条，默认 20"},
)
def list_drafts(ctx, limit: int = 20) -> OperationResult:
    """List drafts. Uses _scrape_draft_items (same selector as open_existing_draft)."""
    def _run(_context, page):
        if not _open_draft_box_on_page(page):
            return OperationResult.failure(message=f"未能进入草稿箱（URL={page_url(page)}）")
        page.wait_for_timeout(1800)

        items = _scrape_draft_items(page)
        titles = [str(i.get("title") or "").strip() for i in items[:limit] if i.get("title")]
        return OperationResult.success(
            message=f"草稿箱列出 {len(titles)} 条草稿",
            items=titles, count=len(titles), url=page_url(page),
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"list_drafts 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.review_draft_box",
    category="review",
    description=(
        "只读：进入草稿箱复核远端草稿列表。"
        "可传 title 校验某篇文章是否已保存到草稿箱；不打开编辑器、不修改内容。"
    ),
    params={
        "title": "可选，目标草稿标题；传入时会校验草稿箱是否命中",
        "limit": "最多返回多少条，默认 20",
    },
)
def review_draft_box(ctx, title: str = "", limit: int = 20) -> OperationResult:
    def _run(_context, page):
        if not _open_draft_box_on_page(page):
            return OperationResult.failure(message=f"未能进入草稿箱（URL={page_url(page)}）")
        page.wait_for_timeout(1800)
        items = _scrape_draft_items(page)
        limited_items = items[: max(1, limit)]
        titles = [str(i.get("title") or "").strip() for i in limited_items if i.get("title")]
        matched = _find_draft_by_title(limited_items, title) if title else None
        state = {
            "items": limited_items,
            "titles": titles,
            "count": len(titles),
            "url": page_url(page),
            "title": title,
            "target_found": bool(matched) if title else None,
            "matched_item": matched,
        }
        if title and not matched:
            return OperationResult.failure(
                message=f"草稿箱未命中标题「{title}」",
                **state,
            )
        return OperationResult.success(
            message=f"草稿箱复核完成，共读取 {len(titles)} 条" + (f"，已命中「{title}」" if title else ""),
            **state,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"review_draft_box 失败: {type(e).__name__}: {e}")
