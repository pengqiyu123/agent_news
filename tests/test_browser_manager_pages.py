"""BrowserManager page selection regressions."""

from __future__ import annotations


class _Page:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def is_closed(self):
        return False

    def close(self):
        self.closed = True

    def evaluate(self, script):  # noqa: ARG002
        return "complete"


def test_pick_reusable_page_prefers_editor_over_about_blank():
    from agent_news.browser.manager import BrowserManager

    manager = BrowserManager()
    blank = _Page("about:blank")
    editor = _Page("https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit")
    manager._page = blank  # noqa: SLF001

    assert manager._pick_reusable_page([blank, editor]) is editor  # noqa: SLF001


def test_pick_reusable_page_uses_non_blank_before_blank():
    from agent_news.browser.manager import BrowserManager

    manager = BrowserManager()
    blank = _Page("about:blank")
    home = _Page("https://mp.weixin.qq.com/")
    manager._page = blank  # noqa: SLF001

    assert manager._pick_reusable_page([blank, home]) is home  # noqa: SLF001


def test_close_extra_pages_closes_about_blank():
    from agent_news.browser.manager import BrowserManager

    manager = BrowserManager()
    blank = _Page("about:blank")
    editor = _Page("https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit")
    context = type("Context", (), {"pages": [editor, blank]})()

    manager._close_extra_pages(context, editor)  # noqa: SLF001

    assert getattr(blank, "closed", False) is True
    assert not getattr(editor, "closed", False)


def test_close_blank_pages_only_closes_blank_tabs():
    from agent_news.browser.manager import BrowserManager

    manager = BrowserManager()
    blank = _Page("about:blank")
    empty = _Page("")
    editor = _Page("https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit")
    context = type("Context", (), {"pages": [blank, empty, editor]})()

    manager._close_blank_pages(context, keep_page=editor)  # noqa: SLF001

    assert getattr(blank, "closed", False) is True
    assert getattr(empty, "closed", False) is True
    assert not getattr(editor, "closed", False)
