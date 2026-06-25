"""Browser package — persistent manager + DOM helpers."""

from .manager import (
    BROWSER_MANAGER,
    WECHAT_HOME_URL,
    BrowserManager,
    default_wechat_channel,
    ensure_channel_defaults,
    get_browser_manager,
    reset_browser_manager,
)
from .selectors import get as get_selectors, get_selector_profile
from .dom import (
    click_first_visible,
    clipboard_paste_into_element,
    clipboard_paste_text,
    pick_required_selector,
    pick_selector,
    pick_visible_locator,
)

__all__ = [
    "BROWSER_MANAGER",
    "BrowserManager",
    "WECHAT_HOME_URL",
    "click_first_visible",
    "clipboard_paste_into_element",
    "clipboard_paste_text",
    "default_wechat_channel",
    "ensure_channel_defaults",
    "get_browser_manager",
    "get_selector_profile",
    "get_selectors",
    "pick_required_selector",
    "pick_selector",
    "pick_visible_locator",
    "reset_browser_manager",
]
