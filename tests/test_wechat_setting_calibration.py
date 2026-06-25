"""Regression tests for WeChat write/settings calibration.

These are pure unit tests around the helper contracts that broke in the real
browser run: author readback, scoped collection options, and claim-source radios.
"""

from __future__ import annotations


class _FakeLocator:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):  # noqa: ARG002
        return self.visible

    def fill(self, value):  # noqa: ARG002
        return None


class _FakePage:
    url = "https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit"

    def __init__(self, evaluate_result):
        self.evaluate_result = evaluate_result
        self.scripts: list[str] = []

    def evaluate(self, script, arg=None):  # noqa: ARG002
        self.scripts.append(script)
        return self.evaluate_result

    def locator(self, selector):  # noqa: ARG002
        return _FakeLocator()

    def wait_for_timeout(self, timeout):  # noqa: ARG002
        return None


def test_fill_author_mismatch_is_failed_by_default(monkeypatch):
    """A platform default author must not be reported as a clean write."""
    from agent_news.operations.wechat import editor

    monkeypatch.setattr(editor, "write_plain_field", lambda page, selectors, value, field_label: "input.js_author")
    monkeypatch.setattr(editor, "read_locator_value", lambda page, selector: "煜的奇思妙想")

    def fake_with_session(channel, *, action_fn, **kwargs):  # noqa: ARG001
        return action_fn(object(), _FakePage({}))

    monkeypatch.setattr(editor.BROWSER_MANAGER, "with_session", fake_with_session)

    result = editor.fill_author(None, "测试作者")
    assert result.status == "failed"
    assert result.state["expected"] == "测试作者"
    assert result.state["value"] == "煜的奇思妙想"


def test_fill_author_allows_platform_default_when_explicit(monkeypatch):
    from agent_news.operations.wechat import editor

    monkeypatch.setattr(editor, "write_plain_field", lambda page, selectors, value, field_label: "input.js_author")
    monkeypatch.setattr(editor, "read_locator_value", lambda page, selector: "煜的奇思妙想")

    def fake_with_session(channel, *, action_fn, **kwargs):  # noqa: ARG001
        return action_fn(object(), _FakePage({}))

    monkeypatch.setattr(editor.BROWSER_MANAGER, "with_session", fake_with_session)

    result = editor.fill_author(None, "测试作者", allow_platform_default=True)
    assert result.status == "ok"
    assert result.state["platform_default"] is True


def test_editor_single_field_operations_accept_agent_friendly_aliases(monkeypatch):
    """Single-field editor ops keep canonical text/markdown params but accept common aliases."""
    from agent_news.operations.wechat import editor

    calls: list[tuple[str, str]] = []

    def fake_with_session(channel, *, action_fn, **kwargs):  # noqa: ARG001
        return action_fn(object(), _FakePage({}))

    monkeypatch.setattr(editor.BROWSER_MANAGER, "with_session", fake_with_session)
    monkeypatch.setattr(
        editor,
        "_fill_title_on_page",
        lambda page, text: calls.append(("title", text)) or editor.OperationResult.success(value=text),
    )
    monkeypatch.setattr(
        editor,
        "_fill_author_on_page",
        lambda page, text, allow_platform_default=False: calls.append(("author", text)) or editor.OperationResult.success(value=text),
    )
    monkeypatch.setattr(
        editor,
        "write_plain_field",
        lambda page, selectors, value, field_label: calls.append(("digest", value)) or "textarea.js_desc",
    )
    monkeypatch.setattr(editor, "read_locator_value", lambda page, selector: "摘要别名")
    monkeypatch.setattr(
        editor,
        "_paste_body_on_page",
        lambda page, markdown, styled=True: calls.append(("body", markdown)) or editor.OperationResult.success(value=markdown),
    )

    assert editor.fill_title(None, title="标题别名").status == "ok"
    assert editor.fill_author(None, author="作者别名").status == "ok"
    assert editor.fill_digest(None, digest="摘要别名").status == "ok"
    assert editor.paste_body(None, body_markdown="正文别名").status == "ok"

    assert ("title", "标题别名") in calls
    assert ("author", "作者别名") in calls
    assert ("digest", "摘要别名") in calls
    assert ("body", "正文别名") in calls


def test_collection_options_are_scoped_to_picker_dropdown():
    from agent_news.operations.wechat.publish_settings import _list_collection_options

    page = _FakePage(["AI新闻"])
    assert _list_collection_options(page) == ["AI新闻"]
    script = page.scripts[-1]
    assert ".select-opts-con" in script
    assert "[role=\"option\"]" not in script
    assert "退出登录" not in script


def test_claim_source_options_are_radio_labels_only():
    from agent_news.operations.wechat.publish_settings import _list_claim_source_options

    page = _FakePage(["个人观点，仅供参考"])
    assert _list_claim_source_options(page) == ["个人观点，仅供参考"]
    script = page.scripts[-1]
    assert "input[type='radio']" in script
    assert ".weui-desktop-radio-group" in script
    assert "[role=\"option\"]" not in script


def test_selector_profile_keeps_old_project_publish_setting_keys():
    from agent_news.browser.selectors import WECHAT_MP_V1

    assert WECHAT_MP_V1["collection_ai_news_option"][0] == "li.select-opt-li:has-text('AI新闻')"
    assert (
        WECHAT_MP_V1["claim_source_personal_option"][0]
        == "label.weui-desktop-form__check-label:has-text('个人观点，仅供参考')"
    )
