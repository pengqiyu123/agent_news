"""WeChat save & publish operations.

All run inside BROWSER_MANAGER.with_session.
- save_as_draft: click 保存为草稿
- click_publish / confirm_publish_modal / continue_publish: step 1/2/3 of publish
- wait_qrcode: step 4 poll wechat_verify_qrcode, screenshot
- publish_to_qrcode: full sequence (1+2+3+4)
- check_publish_done: verify left QR page back to home

wait_qrcode/publish_to_qrcode returning reached_qrcode=True does NOT mean
published. Human scan is still required. Never report publish success
without platform confirmation.
"""

from __future__ import annotations

from ...browser import BROWSER_MANAGER, default_wechat_channel, get_selectors
from ...browser.dom import (
    click_required_selector_once,
    page_url,
    pick_selector,
    read_locator_value,
)
from ...models.operation import OperationResult
from ..base import operation

_CHANNEL = default_wechat_channel()
_ACCOUNT_AUTH_MARKERS = (
    "未授权使用切换账号能力",
    "请退出后扫码登录其他账号",
    "允许切换登录我的其他公众号",
)
_LOGIN_REQUIRED_MARKERS = (
    "扫码登录",
    "请使用微信扫一扫",
    "登录超时",
    "请重新登录",
    "安全登录",
)
_PUBLISH_NO_NOTIFY_MARKERS = (
    "未开启群发通知",
    "已开启群发通知",
    "publish_no_notify",
)
_PUBLISH_CONFIRM_TEXT = "发表"
_CONTINUE_PUBLISH_TEXT = "继续发表"
_PUBLISH_DIALOG_TEXT_LIMIT = 500


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


def _truncate_text(value: str, limit: int = _PUBLISH_DIALOG_TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _operation_result(
    status: str,
    message: str,
    *,
    state: dict | None = None,
    step_logs: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> OperationResult:
    return OperationResult(
        status=status,
        message=message,
        state=state or {},
        step_logs=step_logs or [],
        artifacts=artifacts or [],
    )


def _capture_publish_dialog_screenshot(page, label: str) -> str | None:
    from datetime import datetime, timezone
    from ...config import get_settings

    settings = get_settings()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    screenshot_path = settings.runtime_dir / f"publish_dialog_{label}_{ts}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        return None
    return str(screenshot_path) if screenshot_path.exists() else None


def _inspect_publish_dialog_state(page) -> dict:
    """Read the current publish dialog/page state without clicking anything."""
    url = page_url(page)
    state = {
        "dialog_type": "none",
        "url": url,
        "dialog_text": "",
        "buttons": [],
        "matched_reason": "",
        "requires_relogin": False,
        "requires_human_scan": False,
    }

    qrcode_selector = pick_selector(page, _selectors("wechat_verify_qrcode"), timeout=800)
    if qrcode_selector:
        state.update(
            {
                "dialog_type": "qrcode",
                "matched_reason": f"matched qrcode selector: {qrcode_selector}",
                "requires_human_scan": True,
                "qrcode_selector": qrcode_selector,
            }
        )
        return state

    try:
        snapshot = page.evaluate(
            """() => {
                const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                const visible = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && Number(style.opacity || 1) > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const dialogs = Array.from(document.querySelectorAll(
                    ".weui-desktop-dialog__wrp, .weui-dialog, [role='dialog'], " +
                    ".weui-desktop-popover__wrp, .safe_check, .dialog"
                )).filter(visible);
                const dialog = dialogs.length ? dialogs[dialogs.length - 1] : null;
                const noNotifyPanel = document.querySelector(".publish_no_notify");
                const root = dialog || document.body;
                const buttonNodes = dialog
                    ? Array.from(dialog.querySelectorAll(
                        "button, [role='button'], a.weui-desktop-btn, " +
                        "input[type='button'], input[type='submit']"
                    ))
                    : [];
                const buttons = buttonNodes.map((node, index) => {
                    const rect = node.getBoundingClientRect();
                    const className = typeof node.className === "string"
                        ? node.className
                        : String(node.getAttribute("class") || "");
                    return {
                        index,
                        tag: node.tagName,
                        text: normalize(node.innerText || node.textContent || node.value || ""),
                        class: className.slice(0, 160),
                        visible: visible(node),
                        disabled: Boolean(node.disabled || node.getAttribute("aria-disabled") === "true"),
                        primary: className.includes("primary"),
                        rect: {
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                        },
                    };
                });
                return {
                    dialog_found: Boolean(dialog),
                    publish_no_notify_panel_visible: Boolean(noNotifyPanel && visible(noNotifyPanel)),
                    dialog_text: normalize(root?.innerText || root?.textContent || "").slice(0, 1000),
                    page_text: normalize(document.body?.innerText || document.body?.textContent || "").slice(0, 1000),
                    buttons,
                };
            }"""
        )
    except Exception as exc:
        state.update(
            {
                "dialog_type": "unknown_dialog",
                "matched_reason": f"dialog inspect failed: {type(exc).__name__}: {exc}",
            }
        )
        return state

    if not isinstance(snapshot, dict):
        snapshot = {}
    dialog_text = _truncate_text(str(snapshot.get("dialog_text") or ""))
    page_text = _truncate_text(str(snapshot.get("page_text") or ""))
    buttons = [button for button in snapshot.get("buttons") or [] if isinstance(button, dict)]
    visible_buttons = [
        button for button in buttons
        if button.get("visible") and not button.get("disabled") and str(button.get("text") or "").strip()
    ]
    combined_text = f"{dialog_text} {page_text}"
    state.update(
        {
            "dialog_text": dialog_text,
            "buttons": visible_buttons,
            "dialog_found": bool(snapshot.get("dialog_found")),
        }
    )

    if any(marker in combined_text for marker in _ACCOUNT_AUTH_MARKERS):
        state.update(
            {
                "dialog_type": "account_auth_error",
                "requires_relogin": True,
                "matched_reason": "matched account authorization error text",
            }
        )
        return state

    if "loginpage" in url or any(marker in combined_text for marker in _LOGIN_REQUIRED_MARKERS):
        state.update(
            {
                "dialog_type": "login_required",
                "requires_relogin": True,
                "matched_reason": "matched login required url/text",
            }
        )
        return state

    if bool(snapshot.get("publish_no_notify_panel_visible")) or any(
        marker in combined_text for marker in _PUBLISH_NO_NOTIFY_MARKERS
    ):
        for button in visible_buttons:
            if str(button.get("text") or "").strip() == _CONTINUE_PUBLISH_TEXT:
                state.update(
                    {
                        "dialog_type": "publish_no_notify",
                        "matched_reason": "visible no-notify publish panel",
                        "matched_button": button,
                    }
                )
                return state
        state.update(
            {
                "dialog_type": "publish_no_notify",
                "matched_reason": "visible no-notify publish panel without exact continue button",
            }
        )
        return state

    for button in visible_buttons:
        if str(button.get("text") or "").strip() == _CONTINUE_PUBLISH_TEXT:
            state.update(
                {
                    "dialog_type": "continue_publish",
                    "matched_reason": "visible exact continue publish button",
                    "matched_button": button,
                }
            )
            return state

    for button in visible_buttons:
        if str(button.get("text") or "").strip() == _PUBLISH_CONFIRM_TEXT:
            state.update(
                {
                    "dialog_type": "publish_confirm",
                    "matched_reason": "visible exact publish confirm button",
                    "matched_button": button,
                }
            )
            return state

    if state.get("dialog_found"):
        state.update(
            {
                "dialog_type": "unknown_dialog",
                "matched_reason": "visible dialog without a known publish action",
            }
        )
    return state


def _click_visible_dialog_button_exact(page, expected_text: str) -> dict:
    result = page.evaluate(
        """({ expectedText }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const visible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== "none"
                    && style.visibility !== "hidden"
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const dialogs = Array.from(document.querySelectorAll(
                ".weui-desktop-dialog__wrp, .weui-dialog, [role='dialog'], " +
                ".weui-desktop-popover__wrp, .safe_check, .dialog"
            )).filter(visible);
            const dialog = dialogs.length ? dialogs[dialogs.length - 1] : null;
            if (!dialog) return {clicked: false, reason: "no dialog"};
            const buttons = Array.from(dialog.querySelectorAll(
                "button, [role='button'], a.weui-desktop-btn, " +
                "input[type='button'], input[type='submit']"
            ));
            for (const node of buttons) {
                const text = normalize(node.innerText || node.textContent || node.value || "");
                const disabled = Boolean(node.disabled || node.getAttribute("aria-disabled") === "true");
                if (text === expectedText && visible(node) && !disabled) {
                    const className = typeof node.className === "string"
                        ? node.className
                        : String(node.getAttribute("class") || "");
                    node.click();
                    return {
                        clicked: true,
                        text,
                        tag: node.tagName,
                        class: className.slice(0, 160),
                    };
                }
            }
            return {
                clicked: false,
                reason: "no exact visible enabled button",
                expected_text: expectedText,
                visible_buttons: buttons
                    .filter(visible)
                    .map((node) => normalize(node.innerText || node.textContent || node.value || "")),
            };
        }""",
        {"expectedText": expected_text},
    )
    if not isinstance(result, dict) or not result.get("clicked"):
        raise RuntimeError(f"未找到可点击的精确按钮：{expected_text} ({result})")
    page.wait_for_timeout(1600)
    return result


def _publish_failed_result(
    message: str,
    *,
    dialog_state: dict | None = None,
    step_logs: list[str] | None = None,
    structured_logs: list[dict] | None = None,
    screenshot: str | None = None,
    preflight: dict | None = None,
) -> OperationResult:
    artifacts = [screenshot] if screenshot else []
    state = {
        "publish_dialog": dialog_state or {},
        "publish_step_logs": structured_logs or [],
        "requires_relogin": bool((dialog_state or {}).get("requires_relogin")),
        "requires_human_scan": bool((dialog_state or {}).get("requires_human_scan")),
    }
    if screenshot:
        state["screenshot"] = screenshot
    if preflight is not None:
        state["preflight"] = preflight
    return _operation_result(
        "failed",
        message,
        state=state,
        step_logs=step_logs,
        artifacts=artifacts,
    )


def _require_editor(page) -> OperationResult | None:
    url = page_url(page)
    if "action=edit" not in url and "appmsg_edit" not in url:
        return OperationResult.failure(
            message="当前不在编辑页——请先打开编辑器并填写内容", url=url
        )
    return None


def _read_body_word_count_state(page) -> dict:
    """Read WeChat's own bottom-bar body word count, with body text fallback."""
    body_selector = pick_selector(page, _selectors("editor"), timeout=1000)
    body = read_locator_value(page, body_selector, rich_text=True).strip() if body_selector else ""
    count_selector = pick_selector(page, _selectors("body_word_count"), timeout=1000)
    count_text = read_locator_value(page, count_selector).strip() if count_selector else ""
    parsed_count = None
    if count_text:
        import re

        match = re.search(r"\d+", count_text.replace(",", ""))
        if match:
            parsed_count = int(match.group(0))
    fallback_count = len(body)
    effective_count = parsed_count if parsed_count is not None else fallback_count
    return {
        "body_word_count": effective_count,
        "body_word_count_text": count_text,
        "body_word_count_selector": count_selector,
        "body_word_count_source": "wechat_counter" if parsed_count is not None else "body_text_fallback",
        "body_length": fallback_count,
        "body_selector": body_selector,
        "body_has_content": effective_count > 0,
    }


def _body_word_count_result(page) -> OperationResult:
    guard = _require_editor(page)
    if guard is not None:
        return guard
    state = _read_body_word_count_state(page)
    if not state.get("body_has_content"):
        return OperationResult.failure(
            message="正文字数为 0，禁止保存草稿或发表",
            **state,
        )
    return OperationResult.success(message=f"正文字数校验通过：{state['body_word_count']}", **state)


def _read_publish_preflight_state(
    page,
    *,
    require_author: bool = True,
    require_cover: bool = True,
    require_original: bool = True,
    require_reward: bool = False,
    require_collection: bool = True,
    require_claim_source: bool = True,
) -> dict:
    """Inspect whether the current editor has everything needed before publish."""
    title_selector = pick_selector(page, _selectors("title_input"), timeout=1000)
    author_selector = pick_selector(page, _selectors("author_input"), timeout=1000)
    body_selector = pick_selector(page, _selectors("editor"), timeout=1000)
    title = read_locator_value(page, title_selector).strip() if title_selector else ""
    author = read_locator_value(page, author_selector).strip() if author_selector else ""
    body = read_locator_value(page, body_selector, rich_text=True).strip() if body_selector else ""
    body_count_state = _read_body_word_count_state(page)

    try:
        from .cover import _read_cover_preview_state

        cover_state = _read_cover_preview_state(page)
    except Exception as exc:
        cover_state = {"hasCover": False, "error": f"{type(exc).__name__}: {exc}"}

    settings_state = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const visible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== "none"
                    && style.visibility !== "hidden"
                    && Number(style.opacity || 1) > 0
                    && rect.width >= 0
                    && rect.height >= 0;
            };
            const readCheckArea = (selectors) => {
                const area = selectors.map((sel) => document.querySelector(sel)).find(Boolean);
                const checkbox = area?.querySelector?.("input[type='checkbox'], input.frm_checkbox");
                const text = normalize(area?.innerText || area?.textContent || "");
                return {
                    found: Boolean(area),
                    checked: Boolean(checkbox?.checked),
                    text,
                };
            };
            const collectionArea = document.querySelector("#js_article_tags_area");
            const collectionText = normalize(collectionArea?.querySelector(".js_article_tags_content")?.textContent);
            const collectionCheckbox = collectionArea?.querySelector("input.js_article_tags, input.frm_checkbox");

            const claimArea = document.querySelector("#js_claim_source_area, label.claim_source_label_wrapper");
            const claimSelectedNode = claimArea?.querySelector(".js_claim_source_selected");
            const claimDefaultNode = claimArea?.querySelector(".lbl_content_desc_default");
            const claimSelected = normalize(claimSelectedNode?.textContent);
            const claimDefault = normalize(claimDefaultNode?.textContent);

            const original = readCheckArea(["#js_original", ".js_original_apply_cell", ".origined__setting-group"]);
            const originalInputs = Array.from(document.querySelectorAll(
                ".js_original_apply, .js_ori_setting_checkbox, #js_original input[type='checkbox'], .origined__setting-group input[type='checkbox']"
            ));
            original.checked = original.checked
                || originalInputs.some((input) => Boolean(input.checked));
            original.ok = original.checked
                || original.text.includes("文字原创")
                || original.text.includes("作者:");
            const reward = readCheckArea(["#js_reward_setting_area", ".js_reward_open_cell", ".reward__setting-group"]);

            return {
                original,
                reward,
                collection: {
                    found: Boolean(collectionArea),
                    checked: Boolean(collectionCheckbox?.checked),
                    text: collectionText,
                    ok: Boolean(collectionCheckbox?.checked) && collectionText && !collectionText.includes("未添加"),
                },
                claim_source: {
                    found: Boolean(claimArea),
                    selectedText: claimSelected,
                    defaultText: claimDefault,
                    selectedVisible: visible(claimSelectedNode),
                    ok: Boolean(claimSelected) && !claimSelected.includes("未添加"),
                },
            };
        }"""
    )
    if not isinstance(settings_state, dict):
        settings_state = {}

    checks = {
        "title": bool(title),
        "author": bool(author) if require_author else True,
        "body": bool(body) and bool(body_count_state.get("body_has_content")),
        "cover": bool(cover_state.get("hasCover")) if require_cover else True,
        "original": bool(settings_state.get("original", {}).get("ok") or settings_state.get("original", {}).get("checked")) if require_original else True,
        "reward": bool(settings_state.get("reward", {}).get("checked")) if require_reward else True,
        "collection": bool(settings_state.get("collection", {}).get("ok")) if require_collection else True,
        "claim_source": bool(settings_state.get("claim_source", {}).get("ok")) if require_claim_source else True,
    }
    missing = [key for key, ok in checks.items() if not ok]
    return {
        "ok": not missing,
        "missing": missing,
        "checks": checks,
        "title": title,
        "author": author,
        "body_length": body_count_state.get("body_length", len(body)),
        "body_word_count": body_count_state.get("body_word_count", 0),
        "body_word_count_text": body_count_state.get("body_word_count_text", ""),
        "body_word_count_selector": body_count_state.get("body_word_count_selector"),
        "body_word_count_source": body_count_state.get("body_word_count_source", ""),
        "cover": cover_state,
        "settings": settings_state,
        "requirements": {
            "author": require_author,
            "cover": require_cover,
            "original": require_original,
            "reward": require_reward,
            "collection": require_collection,
            "claim_source": require_claim_source,
        },
    }


def _publish_preflight_result(page, **requirements) -> OperationResult:
    guard = _require_editor(page)
    if guard is not None:
        return guard
    state = _read_publish_preflight_state(page, **requirements)
    if not state.get("ok"):
        return OperationResult.failure(
            message="发表前校验未通过，缺少：" + "、".join(state.get("missing") or []),
            **state,
        )
    return OperationResult.success(message="发表前校验通过", **state)


def _save_current_editor_as_draft(page) -> OperationResult:
    """Click 保存为草稿 on the current editor page. Caller holds session lock."""
    guard = _require_editor(page)
    if guard is not None:
        return guard
    body_count = _body_word_count_result(page)
    if body_count.status == "failed":
        return body_count
    selector = click_required_selector_once(
        page, _selectors("save_draft_button"),
        step_name="save_as_draft", timeout=6000, settle_ms=3500,
    )
    after_url = page_url(page)
    left_editor = "action=edit" not in after_url and "appmsg_edit" not in after_url
    return OperationResult.success(
        message="已点击保存草稿" + ("，页面已离开编辑器" if left_editor else "（仍在编辑页，可能需复查）"),
        url=after_url,
        selector=selector,
        left_editor=left_editor,
        body_word_count=body_count.state.get("body_word_count", 0),
        body_word_count_source=body_count.state.get("body_word_count_source", ""),
    )


def _publish_current_editor_to_qrcode(
    page,
    *,
    max_continue_clicks: int = 3,
    require_author: bool = True,
    require_cover: bool = True,
    require_original: bool = True,
    require_reward: bool = False,
    require_collection: bool = True,
    require_claim_source: bool = True,
) -> OperationResult:
    """Run publish clicks on the current editor page until QR code appears."""
    preflight = _publish_preflight_result(
        page,
        require_author=require_author,
        require_cover=require_cover,
        require_original=require_original,
        require_reward=require_reward,
        require_collection=require_collection,
        require_claim_source=require_claim_source,
    )
    if preflight.status == "failed":
        return preflight

    step_logs: list[str] = []
    structured_logs: list[dict] = []

    # step 1: 发表
    try:
        selector = click_required_selector_once(
            page, _selectors("article_publish_button"),
            step_name="click_publish", timeout=6000, settle_ms=1800,
        )
        step_logs.append(f"step1 已点击发表 (selector={selector})")
        structured_logs.append(
            {"step": "click_publish", "dialog_type": None, "action": "click", "selector": selector}
        )
    except Exception as e:
        return _publish_failed_result(
            f"step1 点击发表失败: {e}",
            step_logs=step_logs,
            structured_logs=structured_logs,
            preflight=preflight.state,
        )

    continue_clicks = 0
    confirm_clicks = 0
    max_state_checks = max(18, max_continue_clicks + 12)
    last_state: dict | None = None

    for attempt in range(1, max_state_checks + 1):
        dialog_state = _inspect_publish_dialog_state(page)
        last_state = dialog_state
        dialog_type = str(dialog_state.get("dialog_type") or "none")
        structured_logs.append(
            {
                "step": "inspect_publish_dialog",
                "attempt": attempt,
                "dialog_type": dialog_type,
                "action": "observe",
                "matched_reason": dialog_state.get("matched_reason", ""),
            }
        )
        step_logs.append(f"inspect#{attempt} dialog_type={dialog_type}")

        if dialog_type == "qrcode":
            screenshot = _capture_publish_dialog_screenshot(page, "qrcode")
            step_logs.append("step4 已到达二维码")
            state = {
                "reached_qrcode": True,
                "requires_human_scan": True,
                "url": page_url(page),
                "screenshot": screenshot,
                "publish_dialog": dialog_state,
                "publish_step_logs": structured_logs,
                "continue_clicks": continue_clicks,
                "confirm_clicks": confirm_clicks,
                "preflight": preflight.state,
            }
            return _operation_result(
                "ok",
                "已到达微信验证二维码，请人工扫码确认发表。",
                state=state,
                step_logs=step_logs,
                artifacts=[screenshot] if screenshot else [],
            )

        if dialog_type == "publish_confirm":
            if confirm_clicks >= 2:
                screenshot = _capture_publish_dialog_screenshot(page, "publish_confirm_stuck")
                return _publish_failed_result(
                    "二次确认发表后仍停留在发表确认弹窗，已停止避免重复点击。",
                    dialog_state=dialog_state,
                    step_logs=step_logs,
                    structured_logs=structured_logs,
                    screenshot=screenshot,
                    preflight=preflight.state,
                )
            try:
                click_info = _click_visible_dialog_button_exact(page, _PUBLISH_CONFIRM_TEXT)
            except Exception as exc:
                screenshot = _capture_publish_dialog_screenshot(page, "publish_confirm_click_failed")
                return _publish_failed_result(
                    f"二次确认发表按钮点击失败：{type(exc).__name__}: {exc}",
                    dialog_state=dialog_state,
                    step_logs=step_logs,
                    structured_logs=structured_logs,
                    screenshot=screenshot,
                    preflight=preflight.state,
                )
            confirm_clicks += 1
            structured_logs.append(
                {
                    "step": "confirm_publish_modal",
                    "dialog_type": dialog_type,
                    "action": "click",
                    "button_text": _PUBLISH_CONFIRM_TEXT,
                    "button": click_info,
                }
            )
            step_logs.append(f"step2 已二次确认发表 (button_text={_PUBLISH_CONFIRM_TEXT})")
            continue

        if dialog_type == "publish_no_notify":
            if continue_clicks >= max(0, max_continue_clicks):
                screenshot = _capture_publish_dialog_screenshot(page, "publish_no_notify_limit")
                return _publish_failed_result(
                    f"未开启群发通知确认次数达到上限 {max_continue_clicks}，已停止。",
                    dialog_state=dialog_state,
                    step_logs=step_logs,
                    structured_logs=structured_logs,
                    screenshot=screenshot,
                    preflight=preflight.state,
                )
            try:
                click_info = _click_visible_dialog_button_exact(page, _CONTINUE_PUBLISH_TEXT)
            except Exception as exc:
                screenshot = _capture_publish_dialog_screenshot(page, "publish_no_notify_click_failed")
                return _publish_failed_result(
                    f"未开启群发通知确认按钮点击失败：{type(exc).__name__}: {exc}",
                    dialog_state=dialog_state,
                    step_logs=step_logs,
                    structured_logs=structured_logs,
                    screenshot=screenshot,
                    preflight=preflight.state,
                )
            continue_clicks += 1
            structured_logs.append(
                {
                    "step": "confirm_publish_no_notify",
                    "dialog_type": dialog_type,
                    "action": "click",
                    "button_text": _CONTINUE_PUBLISH_TEXT,
                    "button": click_info,
                }
            )
            step_logs.append("step3A 已确认未开启群发通知并继续发表")
            continue

        if dialog_type == "continue_publish":
            if continue_clicks >= max(0, max_continue_clicks):
                screenshot = _capture_publish_dialog_screenshot(page, "continue_limit")
                return _publish_failed_result(
                    f"继续发表次数达到上限 {max_continue_clicks}，已停止。",
                    dialog_state=dialog_state,
                    step_logs=step_logs,
                    structured_logs=structured_logs,
                    screenshot=screenshot,
                    preflight=preflight.state,
                )
            try:
                click_info = _click_visible_dialog_button_exact(page, _CONTINUE_PUBLISH_TEXT)
            except Exception as exc:
                screenshot = _capture_publish_dialog_screenshot(page, "continue_click_failed")
                return _publish_failed_result(
                    f"继续发表按钮点击失败：{type(exc).__name__}: {exc}",
                    dialog_state=dialog_state,
                    step_logs=step_logs,
                    structured_logs=structured_logs,
                    screenshot=screenshot,
                    preflight=preflight.state,
                )
            continue_clicks += 1
            structured_logs.append(
                {
                    "step": "continue_publish",
                    "dialog_type": dialog_type,
                    "action": "click",
                    "button_text": _CONTINUE_PUBLISH_TEXT,
                    "button": click_info,
                }
            )
            step_logs.append(f"step3 已点击继续发表 {continue_clicks} 次")
            continue

        if dialog_type == "account_auth_error":
            screenshot = _capture_publish_dialog_screenshot(page, "account_auth_error")
            return _publish_failed_result(
                "微信账号授权错误：未授权使用切换账号能力。请退出后重新扫码登录，并允许切换登录其他公众号/服务号。",
                dialog_state=dialog_state,
                step_logs=step_logs,
                structured_logs=structured_logs,
                screenshot=screenshot,
                preflight=preflight.state,
            )

        if dialog_type == "login_required":
            screenshot = _capture_publish_dialog_screenshot(page, "login_required")
            return _publish_failed_result(
                "微信登录态失效或需要重新扫码登录，发表流程已停止。",
                dialog_state=dialog_state,
                step_logs=step_logs,
                structured_logs=structured_logs,
                screenshot=screenshot,
                preflight=preflight.state,
            )

        if dialog_type == "unknown_dialog":
            screenshot = _capture_publish_dialog_screenshot(page, "unknown_dialog")
            return _publish_failed_result(
                "出现未知发表弹窗，未做任何猜测点击。请先调用 wechat.inspect_publish_dialog 查看弹窗结构。",
                dialog_state=dialog_state,
                step_logs=step_logs,
                structured_logs=structured_logs,
                screenshot=screenshot,
                preflight=preflight.state,
            )

        page.wait_for_timeout(2000)

    screenshot = _capture_publish_dialog_screenshot(page, "timeout")
    return _publish_failed_result(
        "等待发表状态变化超时，未检测到二维码或可识别弹窗。",
        dialog_state=last_state or {"dialog_type": "none", "url": page_url(page)},
        step_logs=step_logs,
        structured_logs=structured_logs,
        screenshot=screenshot,
        preflight=preflight.state,
    )


@operation(
    name="wechat.save_as_draft",
    category="save_publish",
    description="点击保存为草稿。要求已在编辑页且正文已填。",
    params={},
)
def save_as_draft(ctx) -> OperationResult:
    def _run(_context, page):
        return _save_current_editor_as_draft(page)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"save_as_draft 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.inspect_body_word_count",
    category="save_publish",
    description="只读：读取微信底部“正文字数”计数；为 0 时保存草稿和发表都会被拦截。",
    params={},
)
def inspect_body_word_count(ctx) -> OperationResult:
    def _run(_context, page):
        return _body_word_count_result(page)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"inspect_body_word_count 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.save_current_editor_as_draft",
    category="save_publish",
    description=(
        "意图级动作 1：当前已在编辑页时，直接点击保存为草稿。"
        "等价于 wechat.save_as_draft，供 Agent 按用户口令选择。"
    ),
    params={},
)
def save_current_editor_as_draft(ctx) -> OperationResult:
    return save_as_draft(ctx)


@operation(
    name="wechat.publish_preflight",
    category="save_publish",
    description=(
        "只读：发表前必填项校验。默认检查标题、作者、正文、封面、原创声明、合集、创作来源。"
        "赞赏默认不硬卡；缺项时返回 missing，不点击发表。"
    ),
    params={
        "require_author": "默认 True",
        "require_cover": "默认 True",
        "require_original": "默认 True",
        "require_reward": "默认 False；账号支持且用户要求开启赞赏时才设为 True",
        "require_collection": "默认 True",
        "require_claim_source": "默认 True",
    },
)
def publish_preflight(
    ctx,
    require_author: bool = True,
    require_cover: bool = True,
    require_original: bool = True,
    require_reward: bool = False,
    require_collection: bool = True,
    require_claim_source: bool = True,
) -> OperationResult:
    def _run(_context, page):
        return _publish_preflight_result(
            page,
            require_author=require_author,
            require_cover=require_cover,
            require_original=require_original,
            require_reward=require_reward,
            require_collection=require_collection,
            require_claim_source=require_claim_source,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"publish_preflight 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.click_publish",
    category="save_publish",
    description="发表流程 step 1：点击发表按钮（article_publish_button）。",
    params={},
)
def click_publish(ctx) -> OperationResult:
    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        body_count = _body_word_count_result(page)
        if body_count.status == "failed":
            return body_count
        try:
            selector = click_required_selector_once(
                page, _selectors("article_publish_button"),
                step_name="click_publish", timeout=6000, settle_ms=1800,
            )
            return OperationResult.success(message="已点击发表", step=1, selector=selector)
        except Exception as e:
            return OperationResult.failure(message=f"未找到或点击发表失败: {e}")

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"click_publish 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.inspect_publish_dialog",
    category="save_publish",
    description=(
        "只读：识别发表确认/继续发表/二维码/账号授权错误/登录态/未知弹窗。"
        "账号授权错误会返回 failed 且 requires_relogin=True。"
    ),
    params={},
)
def inspect_publish_dialog(ctx) -> OperationResult:
    def _run(_context, page):
        state = _inspect_publish_dialog_state(page)
        dialog_type = str(state.get("dialog_type") or "none")
        if dialog_type == "account_auth_error":
            return OperationResult.failure(
                message="检测到微信账号授权错误，请退出后重新扫码登录并允许切换登录其他公众号/服务号。",
                **state,
            )
        return OperationResult.success(
            message=f"当前发表弹窗状态：{dialog_type}",
            **state,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"inspect_publish_dialog 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.confirm_publish_modal",
    category="save_publish",
    description="发表流程 step 2：仅当弹窗状态为 publish_confirm 时，点击文本精确为“发表”的按钮。",
    params={},
)
def confirm_publish_modal(ctx) -> OperationResult:
    def _run(_context, page):
        state = _inspect_publish_dialog_state(page)
        dialog_type = str(state.get("dialog_type") or "none")
        if dialog_type != "publish_confirm":
            return OperationResult.failure(
                message=f"当前不是发表确认弹窗，已停止点击（dialog_type={dialog_type}）",
                publish_dialog=state,
                requires_relogin=bool(state.get("requires_relogin")),
            )
        try:
            click_info = _click_visible_dialog_button_exact(page, _PUBLISH_CONFIRM_TEXT)
            return OperationResult.success(
                message="已二次确认发表",
                step=2,
                button_text=_PUBLISH_CONFIRM_TEXT,
                button=click_info,
                publish_dialog=state,
            )
        except Exception as e:
            return OperationResult.failure(
                message=f"二次确认发表按钮点击失败: {e}",
                publish_dialog=state,
                requires_relogin=bool(state.get("requires_relogin")),
            )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"confirm_publish_modal 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.confirm_publish_no_notify",
    category="save_publish",
    description=(
        "发表流程 step 3A：仅当弹窗状态为 publish_no_notify 时，点击文本精确为“继续发表”的按钮。"
    ),
    params={},
)
def confirm_publish_no_notify(ctx) -> OperationResult:
    def _run(_context, page):
        state = _inspect_publish_dialog_state(page)
        dialog_type = str(state.get("dialog_type") or "none")
        if dialog_type != "publish_no_notify":
            return OperationResult.failure(
                message=f"当前不是未开启群发通知确认弹窗，已停止点击（dialog_type={dialog_type}）",
                publish_dialog=state,
                requires_relogin=bool(state.get("requires_relogin")),
            )
        try:
            click_info = _click_visible_dialog_button_exact(page, _CONTINUE_PUBLISH_TEXT)
            return OperationResult.success(
                message="已确认未开启群发通知并继续发表",
                step="3a",
                button_text=_CONTINUE_PUBLISH_TEXT,
                button=click_info,
                publish_dialog=state,
                publish_no_notify_confirmed=True,
            )
        except Exception as e:
            return OperationResult.failure(
                message=f"未开启群发通知确认按钮点击失败: {e}",
                publish_dialog=state,
                requires_relogin=bool(state.get("requires_relogin")),
            )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"confirm_publish_no_notify 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.continue_publish",
    category="save_publish",
    description=(
        "发表流程 step 3：循环点击继续发表（continue_publish_button），最多 max_clicks 次。"
        "兼容 continue_publish 与 publish_no_notify 两种确认面板。"
    ),
    params={"max_clicks": "最多点击次数，默认 3"},
)
def continue_publish(ctx, max_clicks: int = 3) -> OperationResult:
    def _run(_context, page):
        clicks = 0
        step_logs: list[str] = []
        last_state: dict | None = None
        for _ in range(max(0, max_clicks)):
            state = _inspect_publish_dialog_state(page)
            last_state = state
            dialog_type = str(state.get("dialog_type") or "none")
            step_logs.append(f"dialog_type={dialog_type}")
            if dialog_type == "qrcode":
                return _operation_result(
                    "ok",
                    f"已到达二维码，停止继续发表点击（此前点击 {clicks} 次）",
                    state={
                        "step": 3,
                        "clicks": clicks,
                        "reached_qrcode": True,
                        "requires_human_scan": True,
                        "publish_dialog": state,
                    },
                    step_logs=step_logs,
                )
            if dialog_type in {"account_auth_error", "login_required", "unknown_dialog"}:
                return OperationResult.failure(
                    message=f"继续发表前检测到异常弹窗，已停止（dialog_type={dialog_type}）",
                    publish_dialog=state,
                    requires_relogin=bool(state.get("requires_relogin")),
                    clicks=clicks,
                )
            if dialog_type not in {"continue_publish", "publish_no_notify"}:
                break
            _click_visible_dialog_button_exact(page, _CONTINUE_PUBLISH_TEXT)
            clicks += 1
        msg = "已点击继续发表 " + str(clicks) + " 次（无更多按钮）" if clicks else "未出现继续发表按钮"
        return _operation_result(
            "ok",
            msg,
            state={"step": 3, "clicks": clicks, "publish_dialog": last_state or {}},
            step_logs=step_logs,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"continue_publish 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.wait_qrcode",
    category="save_publish",
    description=(
        "发表流程 step 4：轮询微信验证二维码（wechat_verify_qrcode），出现即截图返回。"
        "到达二维码不等于发表成功，需人工扫码。"
    ),
    params={"max_checks": "最多检查次数，默认 12", "retry_wait_ms": "每次检查间隔毫秒，默认 5000"},
)
def wait_qrcode(ctx, max_checks: int = 12, retry_wait_ms: int = 5000) -> OperationResult:
    from datetime import datetime, timezone
    from ...config import get_settings

    settings = get_settings()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    screenshot_path = settings.runtime_dir / f"publish_qrcode_{ts}.png"

    def _run(_context, page):
        qrcode_selector = None
        last_state = None
        for _ in range(max(1, max_checks)):
            state = _inspect_publish_dialog_state(page)
            last_state = state
            dialog_type = str(state.get("dialog_type") or "none")
            if dialog_type == "qrcode":
                qrcode_selector = state.get("qrcode_selector") or "wechat_verify_qrcode"
                break
            if dialog_type in {"account_auth_error", "login_required", "unknown_dialog"}:
                return OperationResult.failure(
                    message=f"等待二维码前检测到异常发表状态（dialog_type={dialog_type}）",
                    reached_qrcode=False,
                    publish_dialog=state,
                    requires_relogin=bool(state.get("requires_relogin")),
                    url=page_url(page),
                )
            if retry_wait_ms > 0:
                page.wait_for_timeout(retry_wait_ms)
        if not qrcode_selector:
            return OperationResult.failure(
                message="检查 " + str(max_checks) + " 次后仍未检测到微信验证二维码",
                reached_qrcode=False, url=page_url(page), publish_dialog=last_state or {},
            )
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass
        shot = str(screenshot_path) if screenshot_path.exists() else None
        return OperationResult.success(
            message="已到达微信验证二维码，请人工扫码确认发表。",
            reached_qrcode=True, requires_human_scan=True,
            url=page_url(page), screenshot=shot, qrcode_selector=qrcode_selector,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"wait_qrcode 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.publish_to_qrcode",
    category="save_publish",
    description=(
        "完整发表流程：发表 -> 二次确认 -> 继续发表循环 -> 等二维码。"
        "一步到位，到达二维码即停止。需人工扫码才算发表成功。"
    ),
    params={
        "max_continue_clicks": "继续发表最多点击次数，默认 3",
        "require_author": "默认 True",
        "require_cover": "默认 True",
        "require_original": "默认 True",
        "require_reward": "默认 False；账号支持且用户要求开启赞赏时才设为 True",
        "require_collection": "默认 True",
        "require_claim_source": "默认 True",
    },
)
def publish_to_qrcode(
    ctx,
    max_continue_clicks: int = 3,
    require_author: bool = True,
    require_cover: bool = True,
    require_original: bool = True,
    require_reward: bool = False,
    require_collection: bool = True,
    require_claim_source: bool = True,
) -> OperationResult:
    def _run(_context, page):
        return _publish_current_editor_to_qrcode(
            page,
            max_continue_clicks=max_continue_clicks,
            require_author=require_author,
            require_cover=require_cover,
            require_original=require_original,
            require_reward=require_reward,
            require_collection=require_collection,
            require_claim_source=require_claim_source,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"publish_to_qrcode 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.publish_current_editor_to_qrcode",
    category="save_publish",
    description=(
        "意图级动作 2：当前已在编辑页时，直接点击发表并走到微信验证二维码。"
        "到二维码即停止，requires_human_scan=True，不代表发布成功。"
    ),
    params={
        "max_continue_clicks": "继续发表最多点击次数，默认 3",
        "require_author": "默认 True",
        "require_cover": "默认 True",
        "require_original": "默认 True",
        "require_reward": "默认 False；账号支持且用户要求开启赞赏时才设为 True",
        "require_collection": "默认 True",
        "require_claim_source": "默认 True",
    },
)
def publish_current_editor_to_qrcode(
    ctx,
    max_continue_clicks: int = 3,
    require_author: bool = True,
    require_cover: bool = True,
    require_original: bool = True,
    require_reward: bool = False,
    require_collection: bool = True,
    require_claim_source: bool = True,
) -> OperationResult:
    return publish_to_qrcode(
        ctx,
        max_continue_clicks=max_continue_clicks,
        require_author=require_author,
        require_cover=require_cover,
        require_original=require_original,
        require_reward=require_reward,
        require_collection=require_collection,
        require_claim_source=require_claim_source,
    )


@operation(
    name="wechat.publish_existing_draft_to_qrcode",
    category="save_publish",
    description=(
        "意图级动作 3：用户已审核/修改过草稿后，按标题打开该草稿编辑页，"
        "再点击发表并走到微信验证二维码。到二维码即停止，不代表发布成功。"
    ),
    params={
        "title": "必填，目标草稿标题（部分匹配即可，18字以上模糊匹配）",
        "max_continue_clicks": "继续发表最多点击次数，默认 3",
        "require_author": "默认 True",
        "require_cover": "默认 True",
        "require_original": "默认 True",
        "require_reward": "默认 False；账号支持且用户要求开启赞赏时才设为 True",
        "require_collection": "默认 True",
        "require_claim_source": "默认 True",
    },
)
def publish_existing_draft_to_qrcode(
    ctx,
    title: str,
    max_continue_clicks: int = 3,
    require_author: bool = True,
    require_cover: bool = True,
    require_original: bool = True,
    require_reward: bool = False,
    require_collection: bool = True,
    require_claim_source: bool = True,
) -> OperationResult:
    if not title:
        return OperationResult.failure(message="title 为空，无法定位草稿")

    def _run(context, page):
        from .drafts import _open_existing_draft_editor_on_page

        open_result = _open_existing_draft_editor_on_page(context, page, title)
        if open_result.status == "failed":
            return open_result
        editor_page = BROWSER_MANAGER._page  # noqa: SLF001
        publish_result = _publish_current_editor_to_qrcode(
            editor_page,
            max_continue_clicks=max_continue_clicks,
            require_author=require_author,
            require_cover=require_cover,
            require_original=require_original,
            require_reward=require_reward,
            require_collection=require_collection,
            require_claim_source=require_claim_source,
        )
        publish_result.step_logs.insert(0, open_result.message)
        publish_result.state["opened_draft_title"] = open_result.state.get("title")
        publish_result.state["open_editor_url"] = open_result.state.get("url")
        return publish_result

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"publish_existing_draft_to_qrcode 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.check_publish_done",
    category="save_publish",
    description="检测是否已离开二维码页回到首页（发表成功的标志）。",
    params={},
)
def check_publish_done(ctx) -> OperationResult:
    def _run(_context, page):
        url = page_url(page)
        qrcode_gone = pick_selector(page, _selectors("wechat_verify_qrcode"), timeout=2000) is None
        on_home = "cgi-bin/home" in url or "cgi-bin/frame" in url
        if qrcode_gone and on_home:
            return OperationResult.success(message="已离开二维码页回到首页，发表完成", published=True, url=url)
        return OperationResult(
            status="failed",
            message=f"仍在二维码页或未回到首页（url={url}）",
            published=False, qrcode_gone=qrcode_gone, on_home=on_home, url=url,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"check_publish_done 失败: {type(e).__name__}: {e}")
