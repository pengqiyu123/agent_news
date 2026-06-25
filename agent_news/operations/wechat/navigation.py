"""WeChat navigation + login operations.

- open_dashboard: navigate to mp.weixin.qq.com (no login check)
- check_login: real DOM login verification (3 selectors, 1200ms each, first hit wins) + screenshot
- session: read-only manager state

The login detection uses ONLY DOM selectors (no cookie check).
Selectors: .weui-desktop-account__thumb, .weui-desktop-layout__main, .weui-desktop-side-menu
"""

from __future__ import annotations

from ...browser import (
    BROWSER_MANAGER,
    WECHAT_HOME_URL,
    default_wechat_channel,
    get_selectors,
)
from ...browser.dom import page_url, pick_required_selector, pick_selector, click_first_visible
from ...models.operation import OperationResult
from ..base import operation

_CHANNEL = default_wechat_channel()


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


@operation(
    name="wechat.open_dashboard",
    category="navigation",
    description=(
        "打开公众号后台首页（mp.weixin.qq.com）。不验证登录态，只导航 + 记录 URL。"
        "使用本项目持久 Edge 浏览器会话。"
    ),
    params={},
)
def open_dashboard(ctx) -> OperationResult:
    """Navigate to WeChat MP home."""
    entry_url = _CHANNEL["publish_entry_url"]

    def _run(_context, page):
        page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
        try:
            page.evaluate("() => { document.title = 'agent-news-微信专用'; }")
        except Exception:
            pass
        url = str(page.url)
        return OperationResult.success(
            message="已打开公众号后台首页",
            url=url,
            resident_page="home",
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:  # noqa: BLE001
        return OperationResult.failure(message=f"open_dashboard 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.check_login",
    category="navigation",
    description=(
        "真实 DOM 登录检测：访问 mp.weixin.qq.com，用 3 个选择器（1200ms 各）"
        "判断是否已登录。总是全页截图。未登录时截图含二维码。"
    ),
    params={},
)
def check_login(ctx) -> OperationResult:
    """Real DOM login verification.

    Loops the logged_in selectors with wait_for_selector(timeout=1200), first
    match = logged in. Always takes a full-page screenshot (contains QR if not
    logged in). Returns logged_in + screenshot path + url.
    """
    from datetime import datetime, timezone
    from ...config import get_settings

    entry_url = _CHANNEL["publish_entry_url"]
    logged_in_selectors = _selectors("logged_in")
    settings = get_settings()
    settings.ensure_runtime_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    screenshot_path = settings.runtime_dir / f"check_login_{ts}.png"

    def _run(_context, page):
        page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        logged_in = False
        matched = None
        for selector in logged_in_selectors:
            try:
                page.wait_for_selector(selector, timeout=1200)
                logged_in = True
                matched = selector
                break
            except Exception:
                continue

        # Always screenshot (full page — contains QR code if not logged in)
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass

        url = str(page.url)
        shot = str(screenshot_path) if screenshot_path.exists() else None

        if logged_in:
            return OperationResult.success(
                message=f"已登录（命中 {matched}）",
                logged_in=True,
                url=url,
                screenshot=shot,
                matched_selector=matched,
            )
        return OperationResult.failure(
            message="未检测到公众号后台登录态，当前可能仍停留在登录页。已截图（含二维码）。",
            logged_in=False,
            on_login_page=True,
            url=url,
            screenshot=shot,
            requires_login_scan=True,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:  # noqa: BLE001
        return OperationResult.failure(message=f"check_login 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.session",
    category="navigation",
    description=(
        "只读：返回浏览器会话状态。合并 manager 状态 + 当前页面观测。"
        "字段：manager_alive, busy, resident_page, last_error, current_url, is_editor_page。"
    ),
    params={},
)
def session(ctx) -> OperationResult:
    """Read-only session state = manager_state() + observe_page()."""
    state = BROWSER_MANAGER.manager_state()
    page_state = BROWSER_MANAGER.observe_page()
    return OperationResult.success(message="浏览器会话状态", **state, **page_state)


@operation(
    name="wechat.open_new_editor",
    category="navigation",
    description=(
        "从首页点击「新的创作 → 文章」进入空白编辑页。要求当前已在首页。"
    ),
    params={},
)
def open_new_editor(ctx) -> OperationResult:
    """Click 新文章 entry from home, converge to editor page."""
    entry_url = _CHANNEL["publish_entry_url"]

    def _run(_context, page):
        # ensure we're on home
        url = page_url(page)
        if "mp.weixin.qq.com" not in url or "action=edit" in url:
            page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

        new_article_selector = pick_required_selector(
            page, _selectors("new_article"), step_name="open_new_editor", timeout=6000
        )
        page.locator(new_article_selector).first.click()
        page.wait_for_timeout(3000)

        # The editor may open in a new tab — converge to it
        live_pages = [p for p in page.context.pages if not p.is_closed()]
        editor_page = None
        for candidate in live_pages:
            cand_url = page_url(candidate)
            if "action=edit" in cand_url or "appmsg_edit" in cand_url:
                editor_page = candidate
                break
        if editor_page is None:
            editor_page = live_pages[-1] if live_pages else page

        # close extra tabs, keep the editor
        for candidate in live_pages:
            if candidate is not editor_page:
                try:
                    candidate.close()
                except Exception:
                    pass

        editor_url = page_url(editor_page)
        return OperationResult.success(
            message="已进入空白编辑页" if "action=edit" in editor_url else f"已导航到 {editor_url}",
            url=editor_url,
            resident_page="editor",
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:  # noqa: BLE001
        return OperationResult.failure(message=f"open_new_editor 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.open_draft_box",
    category="navigation",
    description="导航到草稿箱列表页（展开内容管理 → 点草稿箱 → 确认 action=list_card）。",
    params={},
)
def open_draft_box(ctx) -> OperationResult:
    """Open the draft box.

    Two-step nav: (1) expand content_manage sidebar, (2) click the draft_box
    link (with goto fallback if click fails). Confirms URL contains
    action=list_card.
    """
    entry_url = _CHANNEL["publish_entry_url"]

    def _run(_context, page):
        # ensure on home
        page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
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
                    # fallback: read href and goto directly
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
                # wait for URL to contain action=list_card
                try:
                    page.wait_for_url("**action=list_card**", timeout=8000)
                except Exception:
                    page.wait_for_timeout(2500)
                url = page_url(page)
                if "action=list_card" in url:
                    return OperationResult.success(
                        message="已进入草稿箱", url=url, draft_box_opened=True,
                    )
            except Exception:
                continue

        url = page_url(page)
        return OperationResult.failure(
            message=f"未能进入草稿箱（URL={url}）",
            url=url, draft_box_opened=False,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:  # noqa: BLE001
        return OperationResult.failure(message=f"open_draft_box 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.open_publish_history",
    category="navigation",
    description="导航到发表记录页。",
    params={},
)
def open_publish_history(ctx) -> OperationResult:
    entry_url = _CHANNEL["publish_entry_url"]

    def _run(_context, page):
        page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        click_first_visible(page, _selectors("content_manage"), timeout=5000)
        page.wait_for_timeout(1500)
        click_first_visible(page, _selectors("publish_history"), timeout=5000)
        page.wait_for_timeout(2500)
        url = page_url(page)
        return OperationResult.success(
            message="已进入发表记录页" if "appmsgpublish" in url else f"导航到 {url}",
            url=url,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:  # noqa: BLE001
        return OperationResult.failure(message=f"open_publish_history 失败: {type(e).__name__}: {e}")
