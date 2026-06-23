from __future__ import annotations


class FakePage:
    def __init__(self, url: str, title: str = ""):
        self.url = url
        self._title = title
        self.closed = False
        self.front = False

    def is_closed(self):
        return self.closed

    def evaluate(self, script):
        return "complete"

    def title(self):
        return self._title

    def close(self):
        self.closed = True

    def bring_to_front(self):
        self.front = True


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


def test_browser_manager_tab_helpers():
    from agent_news.browser.manager import BrowserManager

    blank = FakePage("about:blank", "blank")
    home = FakePage("https://mp.weixin.qq.com/", "home")
    editor = FakePage("https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit&token=1", "editor")

    manager = BrowserManager()
    manager._manager_alive = True
    manager._context = FakeContext([blank, home, editor])
    manager._page = home
    manager._run_in_worker = lambda fn: fn()

    tabs = manager.observe_tabs()
    assert tabs["page_count"] == 3
    assert any(tab["is_editor"] for tab in tabs["tabs"])

    focused = manager.focus_editor_tab()
    assert focused["focused"] is True
    assert manager._page is editor
    assert editor.front is True

    closed = manager.close_blank_tabs()
    assert closed["closed_count"] == 1
    assert blank.closed is True
    assert editor.closed is False

