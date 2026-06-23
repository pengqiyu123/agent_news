"""DOM helpers — ported from auto-news-studio publishers/wechat/dom.py + browser_base.py.

The clipboard paste mechanism (_clipboard_paste_text / _clipboard_paste_into_element)
is the reliable body-fill method verified in the old project. Selector picking
(_pick_selector / _pick_visible_locator) tries a list in order, first visible match wins.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def page_url(page) -> str:
    try:
        return str(getattr(page, "url", "") or "")
    except Exception:
        return ""


def pick_visible_locator(page, selectors: list[str], *, timeout: int = 4000):
    """Return the first locator among `selectors` that is visible. None if none."""
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=timeout):
                return loc
        except Exception:
            continue
    return None


def pick_selector(page, selectors: list[str], *, timeout: int = 4000) -> str | None:
    """Return the first selector string with a visible match. None if none."""
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=timeout):
                return selector
        except Exception:
            continue
    return None


def pick_required_selector(
    page, selectors: list[str], *, step_name: str = "pick_selector", timeout: int = 5000
) -> str:
    """Like pick_selector but raises if none match."""
    selector = pick_selector(page, selectors, timeout=timeout)
    if selector is None:
        raise RuntimeError(f"[{step_name}] no visible selector found among {selectors}")
    return selector


def dismiss_wechat_hover_popovers(page) -> None:
    """Best-effort cleanup for WeChat hover/dialog layers that intercept clicks.

    Ported from old project's _dismiss_wechat_hover_popovers and extended with
    the layers observed during reward-setting validation.
    """
    try:
        try:
            page.mouse.move(20, 20)
            page.wait_for_timeout(250)
        except Exception:
            pass
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.evaluate(
            """() => {
                document
                    .querySelectorAll(
                        '.popover_article_setting_switch, .not_recommend_setting_switch, ' +
                        '.js_not_recommend_popover, .simple_dialog_content, ' +
                        '.weui-desktop-dialog__wrp, .weui-desktop-popover__wrp'
                    )
                    .forEach((node) => {
                        node.style.pointerEvents = 'none';
                        node.style.display = 'none';
                    });
            }"""
        )
        page.wait_for_timeout(300)
    except Exception:
        pass


def _click_locator_with_fallback(page, locator, *, timeout: int) -> None:
    try:
        locator.click(timeout=timeout)
    except Exception:
        dismiss_wechat_hover_popovers(page)
        try:
            locator.click(timeout=3000)
        except Exception:
            locator.click(timeout=3000, force=True)


def click_first_visible(page, selectors: list[str], *, timeout: int = 4000) -> bool:
    """Click the first visible selector in the list. Returns True if clicked."""
    loc = pick_visible_locator(page, selectors, timeout=timeout)
    if loc is None:
        return False
    _click_locator_with_fallback(page, loc, timeout=timeout)
    return True


def click_required_selector_once(
    page, selectors: list[str], *, step_name: str, timeout: int = 6000, settle_ms: int = 1200
) -> str:
    """Pick + click + settle. Raises if no visible selector. Ported from editor.py:99-128."""
    selector = pick_required_selector(page, selectors, step_name=step_name, timeout=timeout)
    _click_locator_with_fallback(page, page.locator(selector).first, timeout=timeout)
    if settle_ms > 0:
        page.wait_for_timeout(settle_ms)
    return selector


def clipboard_paste_text(page, text: str) -> None:
    """Put text on the clipboard via a hidden textarea, then paste with Ctrl+V.

    FAITHFUL COPY of auto-news-studio dom.py:70-84. This is the reliable way
    to fill long content into WeChat's ProseMirror editor.
    """
    page.evaluate(
        """(text) => {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }""",
        text,
    )
    page.keyboard.press("Control+v")


def clipboard_paste_into_element(page, selector: str, text: str) -> None:
    """Click an element, select-all, then paste text into it via clipboard.

    FAITHFUL COPY of auto-news-studio dom.py:86-93.
    """
    loc = page.locator(selector).first
    loc.click(timeout=4000)
    page.wait_for_timeout(300)
    page.keyboard.press("Control+a")
    page.wait_for_timeout(200)
    clipboard_paste_text(page, text)
    page.wait_for_timeout(500)


def write_plain_field(page, selectors: list[str], value: str, *, field_label: str = "field") -> str:
    """Fill a plain input (title/author/digest). Tries fill(), then type(), then paste.

    Ported from _write_plain_field (dom.py:175-198). Returns the selector used.
    """
    selector = pick_required_selector(page, selectors, step_name=f"write_{field_label}")
    loc = page.locator(selector).first
    try:
        loc.fill(value)
    except Exception:
        try:
            loc.click(timeout=4000)
            page.keyboard.press("Control+a")
            loc.type(value, delay=8)
        except Exception:
            clipboard_paste_into_element(page, selector, value)
    page.wait_for_timeout(300)
    return selector


def select_hidden_option_by_text(page, text: str, *, dropdown_selector: str = ".select-opt-li, li") -> bool:
    """Click a hidden dropdown option by its visible text via synthetic events.

    Ported from _select_hidden_wechat_option_by_text (editor.py:211-239).
    Used for dynamic collection selection (parameterized, not hardcoded).
    """
    return bool(
        page.evaluate(
            """({ text, dropdownSelector }) => {
                const nodes = Array.from(document.querySelectorAll(dropdownSelector));
                const target = nodes.find(n => (n.innerText || n.textContent || '').trim().includes(text));
                if (!target) return false;
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                }
                target.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }""",
            {"text": text, "dropdownSelector": dropdown_selector},
        )
    )


def read_locator_value(page, selector: str, *, rich_text: bool = False) -> str:
    """Read the current text content of an element."""
    script = """({ selector, richText }) => {
        const node = document.querySelector(selector);
        if (!node) return "";
        if (richText) {
            return (node.innerText || node.textContent || '').trim();
        }
        return (node.value || node.innerText || node.textContent || '').trim();
    }"""
    try:
        return str(page.evaluate(script, {"selector": selector, "richText": rich_text})) or ""
    except Exception:
        return ""
