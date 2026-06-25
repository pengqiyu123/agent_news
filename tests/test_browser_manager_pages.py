"""BrowserManager page selection regressions."""

from __future__ import annotations


class _Page:
    def __init__(self, url: str):
        self.url = url
        self.closed = False
        self.goto_url = None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def evaluate(self, script):  # noqa: ARG002
        return "complete"

    def goto(self, url, **kwargs):  # noqa: ARG002
        self.url = url
        self.goto_url = url


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


def test_prepare_working_page_reuses_initial_blank_page():
    from agent_news.browser.manager import BrowserManager

    manager = BrowserManager()
    blank = _Page("about:blank")
    context = type("Context", (), {"pages": [blank]})()

    page = manager._prepare_working_page(context, "https://mp.weixin.qq.com/")  # noqa: SLF001

    assert page is blank
    assert not blank.closed
    assert blank.goto_url == "https://mp.weixin.qq.com/"
    assert manager._page is blank  # noqa: SLF001


def test_prepare_working_page_reports_new_page_error_when_no_page_available():
    from agent_news.browser.manager import BrowserManager

    class FailingContext:
        pages = []

        def new_page(self):
            raise RuntimeError("context already closed")

    manager = BrowserManager()

    try:
        manager._prepare_working_page(FailingContext(), "https://mp.weixin.qq.com/")  # noqa: SLF001
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "new_page_error=RuntimeError: context already closed" in message
