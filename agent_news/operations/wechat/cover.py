"""WeChat cover operations + dynamic option discovery.

- generate_ai_cover(prompt): parameterized, skippable (empty prompt -> skip).
  Walks cover_button -> ai_image_button -> fill prompt -> send -> poll
  ai_image_generated_tip -> use -> confirm. The AI decides whether to run it.
- list_collections / list_claim_sources: let the AI SEE what options exist
  before choosing. This directly solves "合集/创作来源需要 AI 自己识别": the agent
  calls list_* first, reads the options, then calls set_collection /
  set_claim_source with the right name. No hardcoding needed.
"""

from __future__ import annotations

from ...browser import BROWSER_MANAGER, default_wechat_channel, get_selectors
from ...browser.dom import (
    clipboard_paste_into_element,
    click_first_visible,
    click_required_selector_once,
    page_url,
    pick_required_selector,
    pick_selector,
)
from ...models.operation import OperationResult
from ..base import operation
from .publish_settings import (
    _claim_source_dialog_open,
    _list_claim_source_options,
    _list_collection_options,
    _open_claim_source_setting,
    _trigger_collection_picker_dropdown,
)

_CHANNEL = default_wechat_channel()


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


def _require_editor(page) -> OperationResult | None:
    url = page_url(page)
    if "action=edit" not in url and "appmsg_edit" not in url:
        return OperationResult.failure(
            message="当前不在编辑页——封面/设置要求先打开编辑器", url=url
        )
    return None


def _inspect_cover_picker_state(page) -> dict:
    try:
        return dict(page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const text = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
                const rectOf = (el) => {
                    if (!el) return null;
                    const rect = el.getBoundingClientRect();
                    return {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                    };
                };
                const menu = document.querySelector('#js_cover_null, .js_cover_null_pop, .js_cover_opr');
                const aiButtons = Array.from(document.querySelectorAll('a.js_aiImage, .js_aiImage, a.pop-opr__button, button, a, span'))
                    .filter((node) => String(node.className || '').includes('js_aiImage') || text(node) === 'AI 配图')
                    .map((node) => ({
                        text: text(node),
                        tag: node.tagName.toLowerCase(),
                        className: String(node.className || ''),
                        visible: visible(node),
                        rect: rectOf(node),
                    }));
                const aiButton = aiButtons.find((node) => node.visible) || aiButtons[0];
                const visibleOptions = Array.from(document.querySelectorAll('#js_cover_null a, .js_cover_null_pop a, .js_cover_opr a, a.pop-opr__button'))
                    .filter(visible)
                    .map((node) => ({
                        text: text(node),
                        tag: node.tagName.toLowerCase(),
                        className: String(node.className || ''),
                        rect: rectOf(node),
                    }));
                return {
                    menuFound: Boolean(menu),
                    menuVisible: visible(menu),
                    menuText: text(menu),
                    menuClassName: menu ? String(menu.className || '') : '',
                    menuRect: rectOf(menu),
                    aiButtonFound: Boolean(aiButton),
                    aiButtonVisible: Boolean(aiButton && aiButton.visible),
                    aiButtonText: aiButton ? aiButton.text : '',
                    aiButtonClassName: aiButton ? aiButton.className : '',
                    aiButtonRect: aiButton ? aiButton.rect : null,
                    aiButtons,
                    visibleOptions,
                };
            }"""
        ))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _open_cover_picker(page) -> dict:
    state = _inspect_cover_picker_state(page)
    if state.get("aiButtonVisible") or (state.get("menuVisible") and state.get("aiButtonVisible")):
        state["openedBy"] = "already_open"
        return state

    click_required_selector_once(
        page,
        _selectors("cover_button"),
        step_name="open_cover_picker",
        timeout=6000,
        settle_ms=1200,
    )
    state = _inspect_cover_picker_state(page)
    state["openedBy"] = "cover_button"
    return state


def _click_ai_image_button(page) -> tuple[bool, dict]:
    # WeChat keeps hidden historical cover menus in the DOM. A Playwright
    # locator's .first can resolve to a hidden js_aiImage copy, so click the
    # visible DOM node explicitly.
    clicked = bool(page.evaluate(
        """() => {
            const text = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const nodes = Array.from(document.querySelectorAll('a.js_aiImage, .js_aiImage, a.pop-opr__button, button, a, span'));
            const target = nodes.find((node) => visible(node) && (text(node) === 'AI 配图' || String(node.className || '').includes('js_aiImage')));
            if (!target) return false;
            target.scrollIntoView({ block: 'center', inline: 'center' });
            for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            return true;
        }"""
    ))
    page.wait_for_timeout(1200)
    return clicked, {"clickedBy": "dom_event" if clicked else "not_found", "pickerState": _inspect_cover_picker_state(page)}


def _fill_ai_cover_prompt(page, selector: str, prompt: str) -> dict:
    loc = page.locator(selector).first
    try:
        loc.fill(prompt, timeout=4000)
    except Exception:
        clipboard_paste_into_element(page, selector, prompt)
    page.wait_for_timeout(500)
    try:
        value = loc.input_value(timeout=2000)
    except Exception:
        value = ""
    return {"selector": selector, "value": value, "matched": value == prompt}


def _click_ai_cover_send(page) -> dict:
    clicked = bool(page.evaluate(
        """() => {
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const buttons = Array.from(document.querySelectorAll('button.send-btn, .send-btn'));
            const states = buttons.map((node) => ({
                className: String(node.className || ''),
                disabled: Boolean(node.disabled),
                visible: visible(node),
            }));
            const target = buttons.find((node) => visible(node)
                && !Boolean(node.disabled)
                && !String(node.className || '').includes('disabled'));
            if (!target) return { clicked: false, states };
            for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            return { clicked: true, states };
        }"""
    ).get("clicked"))
    state = page.evaluate(
        """() => Array.from(document.querySelectorAll('button.send-btn, .send-btn')).map((node) => ({
            className: String(node.className || ''),
            disabled: Boolean(node.disabled),
        }))"""
    )
    return {"clicked": clicked, "buttons": state}


def _read_ai_generation_state(page, prompt: str) -> dict:
    try:
        return dict(page.evaluate(
            """(prompt) => {
                const text = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const dialog = Array.from(document.querySelectorAll('.weui-desktop-dialog__wrp, .weui-desktop-dialog')).find(visible);
                const root = dialog || document;
                const items = Array.from(root.querySelectorAll('.chat-ai-item')).map((item, index) => {
                    const body = text(item);
                    const tipNode = item.querySelector('.ai-image__tips');
                    return {
                        index,
                        text: body.slice(0, 240),
                        hasPrompt: body.includes(prompt),
                        tip: tipNode ? text(tipNode) : '',
                        useCount: Array.from(item.querySelectorAll('.ai-image-op-btn')).filter((btn) => text(btn) === '使用').length,
                    };
                });
                const matching = items.filter((item) => item.hasPrompt);
                return {
                    itemsCount: items.length,
                    matching,
                    lastMatch: matching.length ? matching[matching.length - 1] : null,
                    last: items.length ? items[items.length - 1] : null,
                };
            }""",
            prompt,
        ))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _click_generated_image_use_button(page, prompt: str) -> dict:
    try:
        target = dict(page.evaluate(
            """(prompt) => {
                const text = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const rectOf = (el) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        cx: Math.round(rect.x + rect.width / 2),
                        cy: Math.round(rect.y + rect.height / 2),
                    };
                };
                const items = Array.from(document.querySelectorAll('.chat-ai-item')).filter((item) => text(item).includes(prompt));
                const item = items.length ? items[items.length - 1] : null;
                if (!item) return { clicked: false, reason: 'no matching prompt' };
                const target = Array.from(item.querySelectorAll('.ai-image-op-btn, button, a'))
                    .find((btn) => {
                        if (!visible(btn)) return false;
                        const label = text(btn);
                        return label === '使用' || label === '使用 AI 图片' || label.includes('使用');
                    });
                if (!target) return { clicked: false, reason: 'no use button', itemText: text(item).slice(0, 240) };
                target.scrollIntoView({ block: 'center', inline: 'center' });
                return { clicked: true, buttonText: text(target), buttonRect: rectOf(target), itemText: text(item).slice(0, 240) };
            }""",
            prompt,
        ))
        if target.get("clicked") and isinstance(target.get("buttonRect"), dict):
            rect = target["buttonRect"]
            page.mouse.click(float(rect["cx"]), float(rect["cy"]))
            page.wait_for_timeout(1800)
        return target
    except Exception as exc:
        return {"clicked": False, "reason": f"{type(exc).__name__}: {exc}"}


def _click_cover_edit_confirm(page) -> dict:
    try:
        try:
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(500)
        except Exception:
            pass
        target = dict(page.evaluate(
            """() => {
                const text = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const rectOf = (el) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        cx: Math.round(rect.x + rect.width / 2),
                        cy: Math.round(rect.y + rect.height / 2),
                    };
                };
                const roots = Array.from(document.querySelectorAll(
                    '.weui-desktop-dialog__wrp, .weui-desktop-dialog, [role="dialog"], .cropper-container, body'
                )).filter(visible);
                const root = roots.find((node) => {
                    const body = text(node);
                    return body.includes('编辑封面') || body.includes('裁剪') || body.includes('封面') || body.includes('确认');
                }) || roots[0] || document.body;
                const buttons = Array.from(root.querySelectorAll('button.weui-desktop-btn_primary, button, .weui-desktop-btn, a'))
                    .filter(visible);
                const target = buttons.find((node) => text(node) === '确认' && String(node.className || '').includes('primary'))
                    || buttons.find((node) => ['确认', '确定', '完成', '下一步', '使用'].includes(text(node)))
                    || buttons.find((node) => text(node).includes('确认') || text(node).includes('确定'));
                if (!target) {
                    return {
                        clicked: false,
                        reason: 'no confirm button',
                        rootText: text(root).slice(0, 300),
                        buttons: buttons.map((node) => ({
                            text: text(node),
                            className: String(node.className || ''),
                            rect: rectOf(node),
                        })).filter((item) => item.text || item.className),
                    };
                }
                return { clicked: true, buttonText: text(target), buttonRect: rectOf(target), rootText: text(root).slice(0, 160) };
            }"""
        ))
        if target.get("clicked") and isinstance(target.get("buttonRect"), dict):
            rect = target["buttonRect"]
            page.mouse.click(float(rect["cx"]), float(rect["cy"]))
        else:
            return target
        page.wait_for_timeout(2500)
        return target
    except Exception as exc:
        return {"clicked": False, "reason": f"{type(exc).__name__}: {exc}"}


def _read_cover_preview_state(page) -> dict:
    try:
        return dict(page.evaluate(
            """() => {
                const nodes = Array.from(document.querySelectorAll(
                    '.js_cover_preview_new, .select-cover__preview, .first_appmsg_cover, .js_appmsg_thumb_new'
                ));
                const hasRealUrl = (value) => {
                    const text = String(value || '').trim();
                    if (!text || text === 'none') return false;
                    if (text === 'url("")' || text === "url('')" || text === 'url()') return false;
                    return /url\\((?!["']?["']?\\))/.test(text) || /^https?:\\/\\//.test(text) || text.startsWith('data:');
                };
                const items = nodes.map((node) => {
                    const style = getComputedStyle(node);
                    const image = node.querySelector && node.querySelector('img[src]');
                    const backgroundImage = style.backgroundImage || '';
                    const dataUrl = node.getAttribute('data-url') || node.getAttribute('data-src') || '';
                    const imgSrc = image ? String(image.getAttribute('src') || '') : '';
                    const hasImage = hasRealUrl(backgroundImage) || hasRealUrl(dataUrl) || hasRealUrl(imgSrc);
                    return {
                        className: String(node.className || ''),
                        id: node.id || '',
                        backgroundImage,
                        dataUrl,
                        imgSrc,
                        hasImage,
                    };
                });
                return { hasCover: items.some((item) => item.hasImage), items };
            }"""
        ))
    except Exception as exc:
        return {"hasCover": False, "error": f"{type(exc).__name__}: {exc}"}


@operation(
    name="wechat.generate_ai_cover",
    category="publish_settings",
    description=(
        "生成 AI 封面。prompt 为封面描述（如 一个iPhone图标）；"
        "传 prompt 为空则跳过封面生成。"
        "AI 可自由决定要不要封面。"
    ),
    params={
        "prompt": "封面描述文本；空则跳过",
        "wait_seconds": "等待生成秒数，默认 30",
    },
)
def generate_ai_cover(ctx, prompt: str = "", wait_seconds: int = 30) -> OperationResult:
    if not prompt:
        return OperationResult.skip(message="按指令跳过封面生成", cover=None)

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard

        # 1. open cover area. If the menu is already open, do not click again:
        # clicking the cover button can close the menu in the real WeChat UI.
        cover_state = _open_cover_picker(page)
        if not cover_state.get("aiButtonVisible"):
            return OperationResult.failure(message="未能打开封面选择菜单", cover_picker=cover_state)

        # 2. click AI image button
        clicked_ai, ai_click_state = _click_ai_image_button(page)
        if not clicked_ai:
            return OperationResult.failure(
                message="未找到 AI 生成封面按钮——可能该入口未开放",
                cover_picker=cover_state,
                ai_click=ai_click_state,
            )

        # 3. fill prompt + send
        prompt_sel = pick_required_selector(
            page, _selectors("ai_image_prompt"), step_name="generate_ai_cover"
        )
        prompt_state = _fill_ai_cover_prompt(page, prompt_sel, prompt)
        if not prompt_state.get("matched"):
            return OperationResult.failure(
                message="AI 封面提示词写入后回读不一致",
                prompt_state=prompt_state,
                cover_picker=cover_state,
                ai_click=ai_click_state,
            )
        send_state = _click_ai_cover_send(page)
        if not send_state.get("clicked"):
            return OperationResult.failure(
                message="AI 封面发送按钮不可用",
                prompt_state=prompt_state,
                send_state=send_state,
            )

        # 4. wait for generation (poll for the result/use button)
        import time
        deadline = time.time() + wait_seconds
        generation_state = {}
        while time.time() < deadline:
            generation_state = _read_ai_generation_state(page, prompt)
            last_match = generation_state.get("lastMatch") or {}
            if last_match.get("useCount", 0) > 0 and "已为你生成图片" in (
                str(last_match.get("tip") or "") + str(last_match.get("text") or "")
            ):
                break
            page.wait_for_timeout(3000)

        last_match = generation_state.get("lastMatch") or {}
        if not (last_match.get("useCount", 0) > 0):
            return OperationResult.failure(
                message=f"等待 {wait_seconds}s 后封面仍未生成完成",
                cover_prompt=prompt,
                generation_state=generation_state,
            )
        use_state = _click_generated_image_use_button(page, prompt)
        if not use_state.get("clicked"):
            return OperationResult.failure(
                message="AI 封面已生成，但未能点击当前提示词对应的使用按钮",
                cover_prompt=prompt,
                generation_state=generation_state,
                use_state=use_state,
            )
        cover_preview_after_use = _read_cover_preview_state(page)
        if cover_preview_after_use.get("hasCover"):
            return OperationResult.success(
                message="已生成并应用 AI 封面（prompt=" + prompt[:30] + "）",
                cover=prompt,
                cover_picker=cover_state,
                ai_click=ai_click_state,
                prompt_state=prompt_state,
                send_state=send_state,
                generation_state=generation_state,
                use_state=use_state,
                confirm_state={"skipped": True, "reason": "cover_preview_ready_after_use"},
                cover_preview=cover_preview_after_use,
            )
        confirm_state = _click_cover_edit_confirm(page)
        if not confirm_state.get("clicked"):
            return OperationResult.failure(
                message="AI 封面已选择，但编辑封面确认失败",
                cover_prompt=prompt,
                generation_state=generation_state,
                use_state=use_state,
                confirm_state=confirm_state,
            )
        cover_preview = _read_cover_preview_state(page)
        if not cover_preview.get("hasCover"):
            return OperationResult.failure(
                message="AI 封面确认后未检测到封面预览图片",
                cover_prompt=prompt,
                generation_state=generation_state,
                use_state=use_state,
                confirm_state=confirm_state,
                cover_preview=cover_preview,
            )
        return OperationResult.success(
            message="已生成并应用 AI 封面（prompt=" + prompt[:30] + "）",
            cover=prompt,
            cover_picker=cover_state,
            ai_click=ai_click_state,
            prompt_state=prompt_state,
            send_state=send_state,
            generation_state=generation_state,
            use_state=use_state,
            confirm_state=confirm_state,
            cover_preview=cover_preview,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"generate_ai_cover 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.inspect_cover_picker",
    category="publish_settings",
    description=(
        "只读诊断：打开封面选择菜单，返回当前可见的封面/AI 配图入口状态。"
        "用于校准封面生成选择器，不生成图片、不保存、不发表。"
    ),
    params={},
)
def inspect_cover_picker(ctx) -> OperationResult:
    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        try:
            state = _open_cover_picker(page)
        except Exception as exc:
            state = _inspect_cover_picker_state(page)
            return OperationResult.failure(
                message=f"封面菜单诊断失败: {type(exc).__name__}: {exc}",
                cover_picker=state,
            )
        return OperationResult.success(
            message="已检查封面选择菜单",
            cover_picker=state,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"inspect_cover_picker 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.list_collections",
    category="publish_settings",
    description=(
        "只读：打开合集选择器，列出当前账号可选的所有合集名称。"
        "AI 先调这个看清有哪些合集，再决定用 set_collection 选哪个——"
        "不要硬编码 AI新闻。"
    ),
    params={},
)
def list_collections(ctx) -> OperationResult:
    """List available collections.

    Uses a scoped selector chain:
    1. Open #js_article_tags_area (collection_setting)
    2. Click input[placeholder='请选择合集'] (collection_picker_input)
    3. Read ONLY from .setting-con .select-opts-con li.select-opt-li
    This avoids noise from global [role=option] matches.
    """
    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard

        # 1. open collection setting area
        setting_sel = pick_selector(page, _selectors("collection_setting"), timeout=4000)
        if setting_sel is None:
            return OperationResult.failure(message="未找到合集设置入口")
        page.locator(setting_sel).first.click()
        page.wait_for_timeout(1000)

        dropdown_state = _trigger_collection_picker_dropdown(page)
        if not dropdown_state.get("inputFound"):
            return OperationResult.failure(message="未找到合集选择输入框", dropdown_state=dropdown_state)
        if not dropdown_state.get("dropdownFound"):
            return OperationResult.failure(message="未找到合集下拉容器", dropdown_state=dropdown_state)
        items = _list_collection_options(page)
        # close the picker
        click_first_visible(page, _selectors("option_confirm_button"), timeout=1500)
        return OperationResult.success(
            message=f"发现 {len(items)} 个可选合集",
            items=items, count=len(items), dropdown_state=dropdown_state,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"list_collections 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.list_claim_sources",
    category="publish_settings",
    description=(
        "只读：打开创作来源选择器，列出所有可选来源名称。"
        "AI 先调这个看清有哪些来源，再决定用 set_claim_source 选哪个——"
        "不要硬编码 个人观点，仅供参考。"
    ),
    params={},
)
def list_claim_sources(ctx) -> OperationResult:
    """List available claim sources. Uses the local radio label selectors:
    #js_claim_source_area label.weui-desktop-form__check-label + input[type=radio].
    """
    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard

        open_state = {"dialogOpen": _claim_source_dialog_open(page)}
        if not open_state.get("dialogOpen"):
            open_state = _open_claim_source_setting(page)
        if not open_state.get("dialogOpen"):
            return OperationResult.failure(message="未能打开创作来源选择弹窗", open_state=open_state)

        items = _list_claim_source_options(page)
        return OperationResult.success(
            message=f"发现 {len(items)} 个可选创作来源",
            items=items, count=len(items),
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run)
    except Exception as e:
        return OperationResult.failure(message=f"list_claim_sources 失败: {type(e).__name__}: {e}")
