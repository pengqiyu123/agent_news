"""WeChat publish-precheck operations.

Every step is parameterized and skippable.

- set_original(enabled): original declaration. enabled=False skips.
- set_reward(enabled): reward. enabled=False skips.
- set_collection(name): ANY collection name (not hardcoded AI新闻).
  Uses a scoped collection dropdown trigger + readback pattern.
- set_claim_source(name): ANY claim source (not hardcoded 个人观点，仅供参考).

Each returns its own OperationResult; failure of one never touches the others.
"""

from __future__ import annotations

import re

from ...browser import BROWSER_MANAGER, default_wechat_channel, get_selectors
from ...browser.dom import (
    click_first_visible,
    page_url,
    pick_selector,
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
            message="当前不在编辑页——发布前设置要求先打开编辑器", url=url
        )
    return None


def _settle_setting_layers(page, *, max_clicks: int = 3) -> list[str]:
    """Best-effort close/confirm setting dialogs left by previous atomic ops."""
    logs: list[str] = []
    for _ in range(max(0, max_clicks)):
        target = page.evaluate(
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
                const rectArea = (el) => {
                    const rect = el.getBoundingClientRect();
                    return rect.width * rect.height;
                };
                const rectOf = (el) => {
                    if (!el) return null;
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
                    '.weui-desktop-dialog__wrp, #js_original_edit_box, .step_panel, .weui-desktop-popover__wrp'
                ))
                    .filter(visible)
                    .sort((a, b) => rectArea(b) - rectArea(a));
                const root = roots.find((node) => {
                    const body = text(node);
                    return body.includes('原创') || body.includes('声明') || body.includes('确认') || body.includes('确定');
                }) || roots[0];
                if (!root) return { clicked: false, reason: 'no_visible_layer' };

                const rootRect = rectOf(root);
                const agreementRoot = Array.from(root.querySelectorAll('label, .frm_checkbox_label, .weui-desktop-form__check-label, p, div, span'))
                    .filter(visible)
                    .map((node) => ({ node, body: text(node), rect: rectOf(node), area: rectArea(node) }))
                    .filter((item) => item.body.includes('我已阅读') || item.body.includes('同意《'))
                    .sort((a, b) => a.area - b.area)[0]?.node || null;
                const agreementCheckbox = agreementRoot?.querySelector?.('input[type="checkbox"]')
                    || Array.from(root.querySelectorAll('input[type="checkbox"]')).find((input) => {
                        const label = input.closest('label, .frm_checkbox_label, .weui-desktop-form__check-label, div');
                        const body = text(label);
                        return body.includes('我已阅读') || body.includes('同意《');
                    });
                let agreement = null;
                if (agreementCheckbox && !agreementCheckbox.checked) {
                    const checkboxRect = rectOf(agreementCheckbox);
                    const label = agreementCheckbox.closest('label, .frm_checkbox_label, .weui-desktop-form__check-label, div');
                    const labelRect = rectOf(label);
                    const checkboxVisibleOnScreen = checkboxRect
                        && checkboxRect.width > 2
                        && checkboxRect.height > 2
                        && checkboxRect.x >= 0
                        && checkboxRect.y >= 0
                        && checkboxRect.x < window.innerWidth
                        && checkboxRect.y < window.innerHeight;
                    agreement = checkboxVisibleOnScreen
                        ? { x: checkboxRect.cx, y: checkboxRect.cy, reason: 'checkbox_rect' }
                        : labelRect
                            ? { x: Math.max(labelRect.x + 12, (rootRect?.x || 0) + 24), y: labelRect.cy, reason: 'label_left' }
                            : null;
                } else if (!agreementCheckbox && agreementRoot) {
                    const labelRect = rectOf(agreementRoot);
                    if (labelRect) {
                        agreement = { x: Math.max(labelRect.x + 14, (rootRect?.x || 0) + 24), y: labelRect.cy, reason: 'agreement_text_left' };
                    }
                }
                const buttons = Array.from(root.querySelectorAll('button, .weui-desktop-btn, a')).filter(visible);
                const target = buttons.find((node) => {
                        const label = text(node);
                        const cls = String(node.className || '');
                        return ['确认', '确定', '我知道了'].includes(label) && cls.includes('primary');
                    })
                    || buttons.find((node) => ['确认', '确定', '我知道了'].includes(text(node)))
                    || buttons.find((node) => text(node).includes('确认') || text(node).includes('确定'));
                if (!target) return { clicked: false, reason: 'no_confirm_button', rootText: text(root).slice(0, 120) };
                const buttonRect = rectOf(target);
                if (!buttonRect) return { clicked: false, reason: 'no_button_rect', rootText: text(root).slice(0, 120) };
                return {
                    clicked: true,
                    agreement,
                    button: { x: buttonRect.cx, y: buttonRect.cy },
                    buttonText: text(target),
                    rootText: text(root).slice(0, 120),
                };
            }"""
        )
        if not isinstance(target, dict) or not target.get("clicked"):
            break
        agreement = target.get("agreement")
        if isinstance(agreement, dict):
            try:
                page.mouse.click(float(agreement["x"]), float(agreement["y"]))
                page.wait_for_timeout(500)
            except Exception:
                pass
        button = target.get("button") or {}
        try:
            page.mouse.click(float(button["x"]), float(button["y"]))
        except Exception:
            break
        logs.append(str(target.get("buttonText") or "confirm"))
        page.wait_for_timeout(900)
    return logs


def _click_setting_entry_by_selectors(page, selectors: list[str], *, step_name: str) -> dict:
    """Click a settings entry with DOM events, avoiding overlay pointer interception."""
    for selector in selectors:
        try:
            result = page.evaluate(
                """(selector) => {
                    const target = document.querySelector(selector);
                    const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                    if (!target) return { ok: false, selector, reason: "not_found" };
                    target.scrollIntoView({ block: "center", inline: "center" });
                    const options = { bubbles: true, cancelable: true, view: window };
                    for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                        target.dispatchEvent(new MouseEvent(eventName, options));
                    }
                    return { ok: true, selector, text: normalize(target.textContent).slice(0, 120) };
                }""",
                selector,
            )
            if isinstance(result, dict) and result.get("ok"):
                page.wait_for_timeout(800)
                return result
        except Exception:
            continue
    return {"ok": False, "step": step_name, "selectors": selectors}


def _trigger_collection_picker_dropdown(page) -> dict:
    """Open the collection dropdown scoped to the collection picker.

    Kept dynamic so any collection name can be selected by the agent.
    """
    state = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const resolveCollectionPicker = () => {
                const area = document.querySelector("#js_article_tags_area");
                const roots = [];
                let node = area;
                for (let i = 0; node && i < 8; i += 1, node = node.parentElement) roots.push(node);
                if (area) {
                    let sibling = area.nextElementSibling;
                    for (let i = 0; sibling && i < 8; i += 1, sibling = sibling.nextElementSibling) roots.push(sibling);
                }
                roots.push(document);
                for (const root of roots) {
                    const input = root.querySelector?.(
                        "input.weui-desktop-form__input[placeholder='请选择合集'], input[placeholder*='请选择合集']"
                    );
                    if (!input) continue;
                    const settingCon = input.closest(".setting-con") || root.querySelector?.(".setting-con") || root;
                    const settingSelect = input.closest(".setting-select") || settingCon.querySelector?.(".setting-select");
                    const dropdown = settingSelect?.querySelector(".select-opts-con")
                        || settingCon.querySelector?.(".select-opts-con");
                    return { area, input, settingCon, settingSelect, dropdown };
                }
                return { area, input: null, settingCon: null, settingSelect: null, dropdown: null };
            };
            const { area, input, dropdown } = resolveCollectionPicker();
            const fire = (node, eventName) => {
                if (!node) return;
                const options = { bubbles: true, cancelable: true, view: window };
                const event = eventName.startsWith("pointer") || eventName.startsWith("mouse")
                    ? new MouseEvent(eventName, options)
                    : new Event(eventName, { bubbles: true, cancelable: true });
                node.dispatchEvent(event);
            };
            const wrapper = input?.closest(".weui-desktop-form__input-wrp") || input;
            for (const node of [wrapper, input]) {
                for (const eventName of ["pointerdown", "mousedown", "mouseup", "click", "focus", "input"]) {
                    fire(node, eventName);
                }
            }
            if (input) input.focus();

            const beforeDisplay = dropdown ? dropdown.style.display || window.getComputedStyle(dropdown).display : "";
            if (dropdown && window.getComputedStyle(dropdown).display === "none") {
                dropdown.style.display = "block";
            }
            if (dropdown) {
                dropdown.style.visibility = "visible";
                dropdown.style.opacity = "1";
                dropdown.style.pointerEvents = "auto";
                dropdown.style.zIndex = "999999";
            }
            const options = dropdown ? Array.from(dropdown.querySelectorAll(".select-opt-li, li")) : [];
            return {
                inputFound: Boolean(input),
                areaFound: Boolean(area),
                dropdownFound: Boolean(dropdown),
                beforeDisplay,
                afterDisplay: dropdown ? dropdown.style.display || window.getComputedStyle(dropdown).display : "",
                optionTexts: options.map((node) => normalize(node.textContent)).filter(Boolean),
            };
        }"""
    )
    page.wait_for_timeout(500)
    return state if isinstance(state, dict) else {"raw": state}


def _list_collection_options(page) -> list[str]:
    """Return only options inside the collection dropdown, never global menu text."""
    items = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const resolveCollectionPicker = () => {
                const area = document.querySelector("#js_article_tags_area");
                const roots = [];
                let node = area;
                for (let i = 0; node && i < 8; i += 1, node = node.parentElement) roots.push(node);
                if (area) {
                    let sibling = area.nextElementSibling;
                    for (let i = 0; sibling && i < 8; i += 1, sibling = sibling.nextElementSibling) roots.push(sibling);
                }
                roots.push(document);
                for (const root of roots) {
                    const input = root.querySelector?.(
                        "input.weui-desktop-form__input[placeholder='请选择合集'], input[placeholder*='请选择合集']"
                    );
                    if (!input) continue;
                    const settingCon = input.closest(".setting-con") || root.querySelector?.(".setting-con") || root;
                    const settingSelect = input.closest(".setting-select") || settingCon.querySelector?.(".setting-select");
                    const dropdown = settingSelect?.querySelector(".select-opts-con")
                        || settingCon.querySelector?.(".select-opts-con");
                    return { dropdown };
                }
                return { dropdown: null };
            };
            const { dropdown } = resolveCollectionPicker();
            const seen = new Set();
            const out = [];
            const options = dropdown ? Array.from(dropdown.querySelectorAll(".select-opt-li, li")) : [];
            for (const opt of options) {
                const text = normalize(opt.innerText || opt.textContent || "");
                if (!text || seen.has(text)) continue;
                seen.add(text);
                out.push(text);
            }
            return out;
        }"""
    )
    return items if isinstance(items, list) else []


def _select_collection_option_by_text(page, name: str) -> dict:
    """Select a collection option by text within the scoped dropdown."""
    selected = page.evaluate(
        """({ name }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const resolveCollectionPicker = () => {
                const area = document.querySelector("#js_article_tags_area");
                const roots = [];
                let node = area;
                for (let i = 0; node && i < 8; i += 1, node = node.parentElement) roots.push(node);
                if (area) {
                    let sibling = area.nextElementSibling;
                    for (let i = 0; sibling && i < 8; i += 1, sibling = sibling.nextElementSibling) roots.push(sibling);
                }
                roots.push(document);
                for (const root of roots) {
                    const input = root.querySelector?.(
                        "input.weui-desktop-form__input[placeholder='请选择合集'], input[placeholder*='请选择合集']"
                    );
                    if (!input) continue;
                    const settingCon = input.closest(".setting-con") || root.querySelector?.(".setting-con") || root;
                    const settingSelect = input.closest(".setting-select") || settingCon.querySelector?.(".setting-select");
                    const dropdown = settingSelect?.querySelector(".select-opts-con")
                        || settingCon.querySelector?.(".select-opts-con");
                    return { dropdown };
                }
                return { dropdown: null };
            };
            const { dropdown } = resolveCollectionPicker();
            const candidates = dropdown ? Array.from(dropdown.querySelectorAll(".select-opt-li, li")) : [];
            const target = candidates.find((node) => normalize(node.textContent).includes(name));
            if (!target) {
                return { ok: false, reason: "not_found", options: candidates.map((node) => normalize(node.textContent)).filter(Boolean) };
            }
            target.scrollIntoView({ block: "center", inline: "center" });
            const mouseOptions = { bubbles: true, cancelable: true, view: window };
            for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                target.dispatchEvent(new MouseEvent(eventName, mouseOptions));
            }
            target.dispatchEvent(new Event("change", { bubbles: true }));
            return { ok: true, reason: "dispatched", text: normalize(target.textContent) };
        }""",
        {"name": name},
    )
    page.wait_for_timeout(800)
    return selected if isinstance(selected, dict) else {"ok": False, "raw": selected}


def _read_collection_selection(page, name: str) -> dict:
    selected = page.evaluate(
        """({ name }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const area = document.querySelector("#js_article_tags_area");
            const selectedText = normalize(area?.querySelector(".js_article_tags_content")?.textContent);
            const checkbox = area?.querySelector("input.js_article_tags, input.frm_checkbox");
            const input = document.querySelector(
                "input.weui-desktop-form__input[placeholder='请选择合集'], input[placeholder*='请选择合集']"
            );
            const inputValue = normalize(input?.value);
            return {
                ok: selectedText.includes(name) || inputValue.includes(name),
                selectedText,
                inputValue,
                checkboxChecked: Boolean(checkbox?.checked),
            };
        }""",
        {"name": name},
    )
    return selected if isinstance(selected, dict) else {"ok": False, "raw": selected}


def _select_claim_source_option_by_text(page, name: str) -> dict:
    """Select the claim-source radio label by text.

    Parameterized for any source text.
    """
    selected = page.evaluate(
        """({ name }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const label = Array.from(document.querySelectorAll("label, .weui-desktop-form__check-label"))
                .find((node) => normalize(node.textContent).includes(name));
            const radio = label?.querySelector("input[type='radio']");
            if (!label) return { ok: false, reason: "not_found", options: [] };
            label.scrollIntoView({ block: "center", inline: "center" });
            const options = { bubbles: true, cancelable: true, view: window };
            for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                label.dispatchEvent(new MouseEvent(eventName, options));
            }
            if (radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event("input", { bubbles: true }));
                radio.dispatchEvent(new Event("change", { bubbles: true }));
            }
            return { ok: normalize(label.textContent).includes(name), reason: "dispatched", text: normalize(label.textContent) };
        }""",
        {"name": name},
    )
    page.wait_for_timeout(800)
    return selected if isinstance(selected, dict) else {"ok": False, "raw": selected}


def _claim_source_dialog_open(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => Boolean(
                    document.querySelector(".claim-source-con .weui-desktop-radio-group")
                )"""
            )
        )
    except Exception:
        return False


def _open_claim_source_setting(page) -> dict:
    opened = page.evaluate(
        """() => {
            const target = document.querySelector("div.js_claim_source_desc")
                || document.querySelector("div.allow_click_opr.js_claim_source_desc")
                || document.querySelector("label.claim_source_label_wrapper");
            if (!target) return { ok: false, reason: "entry_not_found" };
            target.scrollIntoView({ block: "center", inline: "center" });
            const options = { bubbles: true, cancelable: true, view: window };
            for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                target.dispatchEvent(new MouseEvent(eventName, options));
            }
            return { ok: true, reason: "dispatched" };
        }"""
    )
    page.wait_for_timeout(800)
    if isinstance(opened, dict):
        opened["dialogOpen"] = _claim_source_dialog_open(page)
        return opened
    return {"ok": False, "raw": opened, "dialogOpen": _claim_source_dialog_open(page)}


def _list_claim_source_options(page) -> list[str]:
    items = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const seen = new Set();
            const out = [];
            const group = document.querySelector(".claim-source-con .weui-desktop-radio-group");
            const labels = group ? Array.from(group.querySelectorAll("label.weui-desktop-form__check-label, .weui-desktop-form__check-label")) : [];
            for (const label of labels) {
                if (!label.querySelector("input[type='radio']")) continue;
                const text = normalize(label.textContent);
                if (!text || seen.has(text)) continue;
                seen.add(text);
                out.push(text);
            }
            return out;
        }"""
    )
    return items if isinstance(items, list) else []


def _read_claim_source_selection(page, name: str) -> dict:
    selected = page.evaluate(
        """({ name }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const area = document.querySelector("#js_claim_source_area, label.claim_source_label_wrapper");
            const selectedText = normalize(area?.querySelector(".js_claim_source_selected")?.textContent);
            const defaultText = normalize(area?.querySelector(".lbl_content_desc_default")?.textContent);
            const group = document.querySelector(".claim-source-con .weui-desktop-radio-group");
            const checkedLabel = Array.from((group || document).querySelectorAll("label, .weui-desktop-form__check-label"))
                .find((node) => {
                    if (!normalize(node.textContent).includes(name)) return false;
                    const radio = node.querySelector("input[type='radio']");
                    return Boolean(radio?.checked);
                });
            return {
                ok: selectedText.includes(name) || Boolean(checkedLabel),
                selectedText,
                defaultText,
                radioChecked: Boolean(checkedLabel),
            };
        }""",
        {"name": name},
    )
    return selected if isinstance(selected, dict) else {"ok": False, "raw": selected}


def _read_original_author_state(page) -> dict:
    state = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== "none"
                    && style.visibility !== "hidden"
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const textAfterColon = (value) => normalize(value).replace(/^·?\\s*作者\\s*[:：]\\s*/u, "");
            const dialog = document.querySelector("#js_original_edit_box");
            const visibleDialog = visible(dialog);
            const summaryAuthor = textAfterColon(
                document.querySelector("#js_original_open .js_author_explicit")?.textContent
                    || document.querySelector(".js_author_explicit")?.textContent
                    || ""
            );
            const previewAuthor = normalize(
                document.querySelector("#js_original_open .js_ori_info .js_author")?.textContent
                    || document.querySelector(".js_ori_info .js_author")?.textContent
                    || ""
            );
            const input = Array.from((dialog || document).querySelectorAll("input.js_author, input[placeholder='请输入作者']"))
                .find(visible) || null;
            const errorNode = Array.from((dialog || document).querySelectorAll(".js_author_error, .frm_msg.fail"))
                .find(visible) || null;
            const counter = dialog?.querySelector(".frm_counter") || null;
            const fastReprintText = normalize(
                document.querySelector("#js_original_open .js_fast_reprint_tips_explicit")?.textContent
                    || document.querySelector(".js_fast_reprint_tips_explicit")?.textContent
                    || document.querySelector(".js_fast_reprint_tips")?.textContent
                    || ""
            );
            return {
                dialog_open: visibleDialog,
                summary_author: summaryAuthor,
                preview_author: previewAuthor,
                input_value: normalize(input?.value || ""),
                input_found: Boolean(input),
                counter_text: normalize(counter?.innerText || counter?.textContent || ""),
                error_text: normalize(errorNode?.innerText || errorNode?.textContent || ""),
                fast_reprint_text: fastReprintText,
            };
        }"""
    )
    return state if isinstance(state, dict) else {"raw": state}


def _open_original_author_dialog(page) -> dict:
    opened = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== "none"
                    && style.visibility !== "hidden"
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const fire = (node, eventName) => {
                const event = new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window });
                node.dispatchEvent(event);
            };
            if (visible(document.querySelector("#js_original_edit_box"))) {
                return { ok: true, already_open: true, reason: "dialog_already_open" };
            }
            const selectors = [
                "#js_original_open .js_edit_ori",
                "#js_original_open .setting-group__switch",
                "#js_original_open",
                ".setting-group__switch.js_original_apply.js_edit_ori",
                ".js_original_apply.js_edit_ori",
                "#js_original",
                ".js_original_apply_cell",
                ".appmsg-editor__setting-group.origined__setting-group"
            ];
            for (const selector of selectors) {
                const target = document.querySelector(selector);
                if (!target || !visible(target)) continue;
                target.scrollIntoView({ block: "center", inline: "center" });
                for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                    fire(target, eventName);
                }
                return { ok: true, selector, text: normalize(target.innerText || target.textContent).slice(0, 160) };
            }
            return { ok: false, reason: "original_entry_not_found" };
        }"""
    )
    page.wait_for_timeout(900)
    state = _read_original_author_state(page)
    result = opened if isinstance(opened, dict) else {"ok": False, "raw": opened}
    result["dialog_open"] = bool(state.get("dialog_open"))
    result["state"] = state
    return result


def _write_original_author_in_dialog(page, author: str) -> dict:
    result = page.evaluate(
        """({ author }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== "none"
                    && style.visibility !== "hidden"
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const dialog = document.querySelector("#js_original_edit_box");
            if (!visible(dialog)) return { ok: false, reason: "dialog_not_open" };
            const input = Array.from(dialog.querySelectorAll("input.js_author, input[placeholder='请输入作者']"))
                .find(visible);
            if (!input) return { ok: false, reason: "author_input_not_found" };
            input.focus();
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
            if (setter) setter.call(input, author);
            else input.value = author;
            for (const eventName of ["input", "change", "keyup", "blur"]) {
                input.dispatchEvent(new Event(eventName, { bubbles: true, cancelable: true }));
            }
            const counter = dialog.querySelector(".frm_counter");
            return {
                ok: true,
                value: normalize(input.value),
                counter_text: normalize(counter?.innerText || counter?.textContent || ""),
            };
        }""",
        {"author": author},
    )
    page.wait_for_timeout(500)
    return result if isinstance(result, dict) else {"ok": False, "raw": result}


def _confirm_original_author_dialog(page) -> dict:
    result = page.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== "none"
                    && style.visibility !== "hidden"
                    && Number(style.opacity || 1) > 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const clickNode = (node) => {
                node.scrollIntoView({ block: "center", inline: "center" });
                for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                    node.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
                }
            };
            const dialog = document.querySelector("#js_original_edit_box");
            const root = dialog?.closest(".weui-desktop-dialog") || dialog;
            if (!visible(root)) return { ok: false, reason: "dialog_not_open" };
            const agreementInput = Array.from(root.querySelectorAll("input[type='checkbox']"))
                .find((input) => normalize(input.closest("label, div")?.innerText || "").includes("我已阅读"));
            if (agreementInput && !agreementInput.checked) {
                const label = agreementInput.closest("label") || agreementInput;
                clickNode(label);
            }
            const buttons = Array.from(root.querySelectorAll("button, .weui-desktop-btn")).filter(visible);
            const button = buttons.find((node) => {
                const text = normalize(node.innerText || node.textContent);
                const cls = String(node.className || "");
                return ["确定", "确认"].includes(text) && cls.includes("primary");
            }) || buttons.find((node) => ["确定", "确认"].includes(normalize(node.innerText || node.textContent)));
            if (!button) return { ok: false, reason: "confirm_button_not_found" };
            const buttonText = normalize(button.innerText || button.textContent);
            clickNode(button);
            return { ok: true, button_text: buttonText, agreement_checked: Boolean(agreementInput?.checked) };
        }"""
    )
    page.wait_for_timeout(1000)
    return result if isinstance(result, dict) else {"ok": False, "raw": result}


def _original_author_counter_over_limit(counter_text: str) -> bool:
    text = str(counter_text or "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return False
    return float(match.group(1)) > float(match.group(2))


def _set_original_author_on_page(page, author: str) -> OperationResult:
    guard = _require_editor(page)
    if guard is not None:
        return guard
    requested = str(author or "").strip()
    if not requested:
        return OperationResult.failure(message="原创作者为空，无法修改", author=requested)
    before = _read_original_author_state(page)
    open_state = _open_original_author_dialog(page)
    if not open_state.get("dialog_open"):
        return OperationResult.failure(
            message="未能打开原创声明编辑弹窗",
            author=requested,
            before=before,
            open_state=open_state,
        )
    written = _write_original_author_in_dialog(page, requested)
    if not written.get("ok"):
        return OperationResult.failure(
            message="原创声明作者输入失败",
            author=requested,
            before=before,
            open_state=open_state,
            write_state=written,
        )
    after_write = _read_original_author_state(page)
    counter_text = str(written.get("counter_text") or after_write.get("counter_text") or "")
    error_text = str(after_write.get("error_text") or "")
    if not counter_text or error_text or _original_author_counter_over_limit(counter_text):
        return OperationResult.failure(
            message=f"原创声明作者未通过微信计数器校验：{counter_text or error_text or 'counter_missing'}",
            author=requested,
            before=before,
            open_state=open_state,
            write_state=written,
            after_write=after_write,
            counter_text=counter_text,
            error_text=error_text,
        )
    confirmed = _confirm_original_author_dialog(page)
    after = _read_original_author_state(page)
    if not confirmed.get("ok"):
        return OperationResult.failure(
            message="原创声明作者已输入但未能确认",
            author=requested,
            before=before,
            open_state=open_state,
            write_state=written,
            confirm_state=confirmed,
            after=after,
        )
    readback = str(after.get("summary_author") or after.get("preview_author") or "").strip()
    if requested not in readback and readback != requested:
        return OperationResult.failure(
            message=f"原创声明作者确认后回读未命中：expected={requested} actual={readback}",
            author=requested,
            before=before,
            open_state=open_state,
            write_state=written,
            confirm_state=confirmed,
            after=after,
        )
    return OperationResult.success(
        message=f"原创声明作者已修改为：{readback}",
        author=requested,
        readback=readback,
        counter_text=counter_text,
        before=before,
        after=after,
        write_state=written,
        confirm_state=confirmed,
    )


@operation(
    name="wechat.set_original",
    category="publish_settings",
    description=(
        "原创声明：开启(enabled=True)或跳过(enabled=False)。"
        "AI 可自由控制。"
    ),
    params={"enabled": "bool，默认 True；False 则跳过本步"},
)
def set_original(ctx, enabled: bool = True) -> OperationResult:
    if not enabled:
        return OperationResult.skip(message="按指令跳过原创声明", original=False)

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        setting_sel = pick_selector(page, _selectors("original_setting"), timeout=4000)
        if setting_sel is None:
            return OperationResult.failure(message="未找到原创声明开关——可能该账号无原创权限")
        page.locator(setting_sel).first.click()
        page.wait_for_timeout(600)
        click_first_visible(page, _selectors("primary_confirm_button"), timeout=2000)
        page.wait_for_timeout(800)
        settle_logs = _settle_setting_layers(page)
        return OperationResult.success(message="已开启原创声明", original=True, settle_logs=settle_logs)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"set_original 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.set_original_author",
    category="publish_settings",
    description=(
        "修改原创声明里的作者。用于已填写过作者/已开启原创后，需要在原创弹窗内改作者的场景。"
        "以输入框右侧计数器为准，不本地估算；超过上限会失败且不会静默截断。"
    ),
    params={"author": "必填，原创声明作者；以输入框右侧 counter_text 为准"},
)
def set_original_author(ctx, author: str = "") -> OperationResult:
    def _run(_context, page):
        return _set_original_author_on_page(page, author)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"set_original_author 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.inspect_publish_settings",
    category="publish_settings",
    description="只读：检查发布设置区、可见弹层和按钮状态，不点击、不修改。",
    params={},
)
def inspect_publish_settings(ctx) -> OperationResult:
    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        state = page.evaluate(
            """() => {
                const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && Number(style.opacity || 1) > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const rectOf = (el) => {
                    if (!el) return null;
                    const rect = el.getBoundingClientRect();
                    return { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) };
                };
                const area = document.querySelector("#js_article_tags_area");
                const claimArea = document.querySelector("#js_claim_source_area, label.claim_source_label_wrapper");
                const layers = Array.from(document.querySelectorAll(
                    ".weui-desktop-dialog__wrp, .weui-desktop-popover__wrp, #js_original_edit_box, .step_panel"
                )).filter(visible).map((node) => ({
                    tag: node.tagName,
                    id: node.id || "",
                    className: String(node.className || ""),
                    text: normalize(node.innerText || node.textContent).slice(0, 300),
                    rect: rectOf(node),
                    buttons: Array.from(node.querySelectorAll("button, .weui-desktop-btn, a"))
                        .filter(visible)
                        .map((btn) => ({ text: normalize(btn.innerText || btn.textContent), className: String(btn.className || ""), rect: rectOf(btn) })),
                }));
                const buttons = Array.from(document.querySelectorAll("button, .weui-desktop-btn"))
                    .filter(visible)
                    .map((btn) => ({ text: normalize(btn.innerText || btn.textContent), className: String(btn.className || ""), rect: rectOf(btn) }))
                    .slice(0, 80);
                const checkboxes = Array.from(document.querySelectorAll("input[type='checkbox'], .frm_checkbox, .weui-desktop-form__checkbox"))
                    .map((input) => {
                        const label = input.closest("label, .frm_checkbox_label, .weui-desktop-form__check-label, div");
                        return {
                            checked: Boolean(input.checked),
                            className: String(input.className || ""),
                            rect: rectOf(input),
                            labelText: normalize(label?.innerText || label?.textContent).slice(0, 220),
                            labelRect: rectOf(label),
                        };
                    });
                return {
                    url: location.href,
                    originalChecked: Array.from(document.querySelectorAll(".js_original_apply, .js_ori_setting_checkbox, #js_original input[type='checkbox'], .origined__setting-group input[type='checkbox']"))
                        .some((input) => Boolean(input.checked)),
                    collectionText: normalize(area?.querySelector(".js_article_tags_content")?.textContent),
                    collectionChecked: Boolean(area?.querySelector("input.js_article_tags, input.frm_checkbox")?.checked),
                    claimSelectedText: normalize(claimArea?.querySelector(".js_claim_source_selected")?.textContent),
                    claimDefaultText: normalize(claimArea?.querySelector(".lbl_content_desc_default")?.textContent),
                    layers,
                    buttons,
                    checkboxes,
                };
            }"""
        )
        return OperationResult.success(message="已读取发布设置状态", **(state if isinstance(state, dict) else {"raw": state}))

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"inspect_publish_settings 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.settle_publish_settings",
    category="publish_settings",
    description="清理/确认发布设置弹层：点击可见设置弹窗里的主按钮“确定/确认”。",
    params={"max_clicks": "最多点击次数，默认 5"},
)
def settle_publish_settings(ctx, max_clicks: int = 5) -> OperationResult:
    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        logs = _settle_setting_layers(page, max_clicks=max_clicks)
        return OperationResult.success(message=f"已处理发布设置弹层 {len(logs)} 次", settle_logs=logs)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"settle_publish_settings 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.set_reward",
    category="publish_settings",
    description=(
        "赞赏：开启(enabled=True)或跳过(enabled=False)。"
        "AI 可自由控制。"
    ),
    params={"enabled": "bool，默认 True；False 则跳过本步"},
)
def set_reward(ctx, enabled: bool = True) -> OperationResult:
    if not enabled:
        return OperationResult.skip(message="按指令跳过赞赏", reward=False)

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        setting_sel = pick_selector(page, _selectors("reward_setting"), timeout=4000)
        if setting_sel is None:
            return OperationResult.failure(message="未找到赞赏开关——可能该账号未开通赞赏")
        page.locator(setting_sel).first.click()
        page.wait_for_timeout(600)
        click_first_visible(page, _selectors("primary_confirm_button"), timeout=2000)
        page.wait_for_timeout(800)
        return OperationResult.success(message="已开启赞赏", reward=True)

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"set_reward 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.set_collection",
    category="publish_settings",
    description=(
        "选择合集。name 参数为合集名称（如 AI新闻），由 AI 决定。"
        "完全参数化，不硬编码合集名。"
        "若 name 为空则跳过本步。"
    ),
    params={"name": "合集名称，如 AI新闻；空则跳过"},
)
def set_collection(ctx, name: str = "") -> OperationResult:
    if not name:
        return OperationResult.skip(message="未指定合集名，跳过", collection=None)

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        _settle_setting_layers(page)

        entry = _click_setting_entry_by_selectors(
            page,
            _selectors("collection_setting"),
            step_name="open_collection_setting",
        )
        if not entry.get("ok"):
            return OperationResult.failure(message="未找到合集设置入口", entry=entry)

        dropdown_state = _trigger_collection_picker_dropdown(page)
        if not dropdown_state.get("inputFound"):
            return OperationResult.failure(
                message="未找到合集选择输入框",
                collection=name,
                dropdown_state=dropdown_state,
            )
        if not dropdown_state.get("dropdownFound"):
            return OperationResult.failure(
                message="未找到合集下拉容器",
                collection=name,
                dropdown_state=dropdown_state,
            )
        selected = _select_collection_option_by_text(page, name)
        if not selected.get("ok"):
            return OperationResult.failure(
                message=f"合集下拉中未找到匹配「{name}」的选项",
                collection=name,
                options=selected.get("options") or dropdown_state.get("optionTexts") or [],
            )
        page.wait_for_timeout(600)
        if not click_first_visible(page, _selectors("option_confirm_button"), timeout=3000):
            return OperationResult.failure(message="合集已选择但未找到确认按钮", collection=name)
        page.wait_for_timeout(800)
        readback = _read_collection_selection(page, name)
        if not readback.get("ok"):
            return OperationResult.failure(
                message=f"合集选择后回读未命中「{name}」",
                collection=name,
                readback=readback,
            )
        return OperationResult.success(
            message=f"已选择合集「{name}」",
            collection=name,
            readback=readback,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"set_collection 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.set_claim_source",
    category="publish_settings",
    description=(
        "选择创作来源。name 参数为来源名称（如 个人观点，仅供参考），由 AI 决定。"
        "完全参数化，不硬编码创作来源。"
        "若 name 为空则跳过本步。"
    ),
    params={"name": "创作来源名称，如 个人观点，仅供参考；空则跳过"},
)
def set_claim_source(ctx, name: str = "") -> OperationResult:
    if not name:
        return OperationResult.skip(message="未指定创作来源，跳过", claim_source=None)

    def _run(_context, page):
        guard = _require_editor(page)
        if guard is not None:
            return guard
        _settle_setting_layers(page)

        open_state = {"dialogOpen": _claim_source_dialog_open(page)}
        if not open_state.get("dialogOpen"):
            open_state = _open_claim_source_setting(page)
        if not open_state.get("dialogOpen"):
            return OperationResult.failure(
                message="未能打开创作来源选择弹窗",
                claim_source=name,
                open_state=open_state,
            )

        selected = _select_claim_source_option_by_text(page, name)
        if not selected.get("ok"):
            return OperationResult.failure(
                message=f"创作来源下拉中未找到匹配「{name}」的选项",
                claim_source=name,
                options=_list_claim_source_options(page),
            )
        page.wait_for_timeout(600)
        if not click_first_visible(page, _selectors("option_confirm_button"), timeout=3000):
            return OperationResult.failure(message="创作来源已选择但未找到确认按钮", claim_source=name)
        page.wait_for_timeout(800)
        readback = _read_claim_source_selection(page, name)
        if not readback.get("ok"):
            return OperationResult.failure(
                message=f"创作来源选择后回读未命中「{name}」",
                claim_source=name,
                readback=readback,
            )
        return OperationResult.success(
            message=f"已选择创作来源「{name}」",
            claim_source=name,
            readback=readback,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"set_claim_source 失败: {type(e).__name__}: {e}")
