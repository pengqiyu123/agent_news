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


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


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
    from datetime import datetime, timezone
    from ...config import get_settings

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

    settings = get_settings()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    screenshot_path = settings.runtime_dir / f"publish_qrcode_{ts}.png"
    step_logs = []

    # step 1: 发表
    try:
        click_required_selector_once(
            page, _selectors("article_publish_button"),
            step_name="click_publish", timeout=6000, settle_ms=1800,
        )
        step_logs.append("step1 已点击发表")
    except Exception as e:
        return OperationResult.failure(message=f"step1 点击发表失败: {e}", step_logs=step_logs)

    # step 2: 二次确认
    pre_click_url = page_url(page)
    try:
        selector = click_required_selector_once(
            page, _selectors("publish_modal_button"),
            step_name="confirm_publish_modal", timeout=6000, settle_ms=1800,
        )
        step_logs.append(f"step2 已二次确认 (selector={selector})")
        # 检查是否真的跳转了（排除点击了 h3 标题等非确认元素的情况）
        page.wait_for_timeout(2000)
        post_click_url = page_url(page)
        if post_click_url == pre_click_url:
            # URL 没变，说明点击了错误的元素（如 h3 标题）
            step_logs.append("step2b URL未变化，尝试JS点击确认按钮")
            clicked = page.evaluate("""() => {
                const dialog = document.querySelector('.weui-desktop-dialog__wrp');
                if (!dialog) return 'no dialog';
                const ft = dialog.querySelector('.weui-desktop-dialog__ft');
                if (ft) {
                    const btn = ft.querySelector('button, [role="button"], .weui-desktop-btn_primary, a');
                    if (btn) { btn.click(); return 'clicked ft btn: ' + btn.textContent?.trim(); }
                }
                const candidates = dialog.querySelectorAll('.weui-desktop-btn_primary, button.weui-desktop-btn');
                for (const c of candidates) {
                    const rect = c.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) { c.click(); return 'clicked candidate'; }
                }
                return 'no confirm btn found';
            }""")
            step_logs.append(f"step2c JS结果: {clicked}")
            page.wait_for_timeout(2000)
    except Exception as e:
        step_logs.append(f"step2 二次确认未出现: {e}")
        # 弹窗可能是发表选项面板而非确认弹窗
        # 策略：用 JS 获取弹窗结构并尝试点击确认按钮
        try:
            result = page.evaluate("""() => {
                const dialog = document.querySelector('.weui-desktop-dialog__wrp');
                if (!dialog) return JSON.stringify({status: 'no dialog'});
                const info = [];
                const allEls = dialog.querySelectorAll('button, [role="button"], a, input[type="submit"], .weui-desktop-btn, .weui-desktop-dialog__ft, .weui-desktop-dialog__bd');
                for (const el of allEls) {
                    const rect = el.getBoundingClientRect();
                    const visible = rect.width > 0 && rect.height > 0;
                    info.push({
                        tag: el.tagName,
                        class: el.className?.substring(0, 80),
                        text: el.textContent?.trim()?.substring(0, 50),
                        visible: visible,
                        rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)}
                    });
                }
                const ft = dialog.querySelector('.weui-desktop-dialog__ft');
                if (ft) {
                    info.push({tag: 'FT-AREA', class: ft.className, text: ft.textContent?.trim()?.substring(0, 100), html: ft.innerHTML?.substring(0, 500)});
                }
                return JSON.stringify({status: 'found', elements: info});
            }""")
            step_logs.append(f"step2b 弹窗结构: {result[:300]}")
            # 尝试点击弹窗 footer 区域的确认按钮
            clicked = page.evaluate("""() => {
                const dialog = document.querySelector('.weui-desktop-dialog__wrp');
                if (!dialog) return 'no dialog';
                // 优先找 footer 区域的按钮
                const ft = dialog.querySelector('.weui-desktop-dialog__ft');
                if (ft) {
                    const btn = ft.querySelector('button, [role="button"], .weui-desktop-btn_primary, a');
                    if (btn) { btn.click(); return 'clicked ft btn'; }
                }
                // 找弹窗内任何可见的确认类按钮
                const candidates = dialog.querySelectorAll('.weui-desktop-btn_primary, button.weui-desktop-btn, [class*="confirm"], [class*="submit"]');
                for (const c of candidates) {
                    const rect = c.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) { c.click(); return 'clicked candidate'; }
                }
                return 'no confirm btn';
            }""")
            step_logs.append(f"step2c 点击结果: {clicked}")
            page.wait_for_timeout(2000)
        except Exception as js_e:
            step_logs.append(f"step2b/c 失败: {js_e}")

    # step 3: 继续发表循环
    continue_clicks = 0
    for _ in range(max(0, max_continue_clicks)):
        selector = pick_selector(page, _selectors("continue_publish_button"), timeout=3000)
        if not selector:
            break
        page.locator(selector).first.click(timeout=3000)
        continue_clicks += 1
        page.wait_for_timeout(1200)
    step_logs.append(f"step3 继续发表点击 {continue_clicks} 次")

    # step 4: 等二维码
    qrcode_selector = None
    for _ in range(12):
        qrcode_selector = pick_selector(page, _selectors("wechat_verify_qrcode"), timeout=5000)
        if qrcode_selector:
            break
        page.wait_for_timeout(5000)
    if not qrcode_selector:
        return OperationResult.failure(
            message="已走完发表流程但未检测到二维码", step_logs=step_logs, url=page_url(page),
        )
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        pass
    shot = str(screenshot_path) if screenshot_path.exists() else None
    step_logs.append("step4 已到达二维码")
    return OperationResult.success(
        message="已到达微信验证二维码，请人工扫码确认发表。",
        reached_qrcode=True, requires_human_scan=True,
        url=page_url(page), screenshot=shot, step_logs=step_logs,
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
    name="wechat.confirm_publish_modal",
    category="save_publish",
    description="发表流程 step 2：点击弹窗里的发表二次确认（publish_modal_button）。",
    params={},
)
def confirm_publish_modal(ctx) -> OperationResult:
    def _run(_context, page):
        try:
            selector = click_required_selector_once(
                page, _selectors("publish_modal_button"),
                step_name="confirm_publish_modal", timeout=6000, settle_ms=1800,
            )
            return OperationResult.success(message="已二次确认发表", step=2, selector=selector)
        except Exception as e:
            return OperationResult.failure(message=f"二次确认弹窗未出现或点击失败: {e}")

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"confirm_publish_modal 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.continue_publish",
    category="save_publish",
    description=(
        "发表流程 step 3：循环点击继续发表（continue_publish_button），最多 max_clicks 次。"
        "微信会多次弹出继续发表按钮（每个合规检查一次），本操作全部吸收。"
    ),
    params={"max_clicks": "最多点击次数，默认 3"},
)
def continue_publish(ctx, max_clicks: int = 3) -> OperationResult:
    def _run(_context, page):
        clicks = 0
        for _ in range(max(0, max_clicks)):
            selector = pick_selector(page, _selectors("continue_publish_button"), timeout=3000)
            if not selector:
                break
            page.locator(selector).first.click(timeout=3000)
            clicks += 1
            page.wait_for_timeout(1200)
        msg = "已点击继续发表 " + str(clicks) + " 次（无更多按钮）" if clicks else "未出现继续发表按钮"
        return OperationResult.success(message=msg, step=3, clicks=clicks)

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
        for _ in range(max(1, max_checks)):
            qrcode_selector = pick_selector(page, _selectors("wechat_verify_qrcode"), timeout=5000)
            if qrcode_selector:
                break
            if retry_wait_ms > 0:
                page.wait_for_timeout(retry_wait_ms)
        if not qrcode_selector:
            return OperationResult.failure(
                message="检查 " + str(max_checks) + " 次后仍未检测到微信验证二维码",
                reached_qrcode=False, url=page_url(page),
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
