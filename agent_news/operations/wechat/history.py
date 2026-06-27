"""WeChat publish-history review and metrics operations.

These operations are read-only: they navigate to 内容管理 -> 发表记录, scrape
remote publish records, and optionally compute engagement metrics. They never
click publish or edit content.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ...browser import BROWSER_MANAGER, WECHAT_HOME_URL, default_wechat_channel, get_selectors
from ...browser.dom import page_url, pick_selector
from ...config import get_settings
from ...content.publish_performance import (
    build_content_performance_review,
    build_publish_metrics_analysis,
    latest_content_strategy_profile,
    build_title_history_hint,
    summarize_task_snapshots,
)
from ...db import get_repository
from ...models.operation import OperationResult
from ..base import operation

_CHANNEL = default_wechat_channel()
_DELETE_DIALOG_TEXT_LIMIT = 500
_DELETE_DIALOG_MARKERS = (
    "删除后用户将无法访问此页面",
    "确定删除",
    "不能删除已经成功发送到用户的消息",
)
_DELETE_CONFIRM_TEXTS = ("确定", "确认")
_PUBLISH_RECORD_MENU_OPTIONS = (
    "置顶",
    "仅自己可见",
    "删除",
    "关闭推荐",
    "复制链接",
    "修改合集",
    "声明创作来源",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selectors(key: str) -> list[str]:
    return get_selectors(key)


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip().lower()


def _title_matches(left: str, right: str) -> bool:
    left_norm = _normalize_title(left).replace("：", ":")
    right_norm = _normalize_title(right).replace("：", ":")
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    shorter, longer = (left_norm, right_norm) if len(left_norm) <= len(right_norm) else (right_norm, left_norm)
    return len(shorter) >= 18 and (longer.startswith(shorter) or shorter in longer)


def _find_item_by_title(items: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    if not title:
        return None
    for item in items:
        if _title_matches(str(item.get("title") or ""), title):
            return item
    needle = _normalize_title(title)
    for item in items:
        item_title = _normalize_title(str(item.get("title") or ""))
        if needle and item_title and (needle in item_title or item_title in needle):
            return item
    return None


def _truncate_dialog_text(value: str, limit: int = _DELETE_DIALOG_TEXT_LIMIT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _delete_dialog_root_js() -> str:
    return """
                const dialogSelectors = [
                    ".weui-desktop-dialog",
                    ".weui-dialog",
                    "[role='dialog']",
                    ".weui-desktop-dialog__wrp",
                    ".weui-desktop-popover",
                    ".weui-desktop-popover__wrp",
                    ".delect_content"
                ];
                const seen = new Set();
                const candidates = [];
                const hasClass = (node, className) => Boolean(node?.classList?.contains?.(className));
                for (const selector of dialogSelectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (seen.has(node) || !visible(node)) continue;
                        seen.add(node);
                        const rect = node.getBoundingClientRect();
                        const text = normalize(node.innerText || node.textContent || "");
                        let priority = 0;
                        if (hasClass(node, "delect_content")) priority = 80;
                        else if (hasClass(node, "weui-desktop-popover")) priority = 70;
                        else if (hasClass(node, "weui-desktop-popover__wrp")) priority = 65;
                        if (hasClass(node, "weui-desktop-dialog")) priority = 60;
                        else if (hasClass(node, "weui-dialog")) priority = 50;
                        else if (node.getAttribute("role") === "dialog") priority = 40;
                        else if (hasClass(node, "weui-desktop-dialog__wrp")) priority = 10;
                        candidates.push({
                            node,
                            priority,
                            area: rect.width * rect.height,
                            textLength: text.length,
                            hasDeleteMarker: ["删除后用户将无法访问此页面", "确定删除", "不能删除已经成功发送到用户的消息"]
                                .some((marker) => text.includes(marker)),
                        });
                    }
                }
                candidates.sort(
                    (a, b) =>
                        Number(b.hasDeleteMarker) - Number(a.hasDeleteMarker) ||
                        b.priority - a.priority ||
                        b.area - a.area ||
                        b.textLength - a.textLength
                );
                const dialog = candidates.length ? candidates[0].node : null;
    """


def _click_publish_record_menu_option(
    page,
    title: str,
    option_text: str,
    target_url: str = "",
) -> dict[str, Any]:
    """Open one publish-history record's more menu and click an exact option."""
    result = page.evaluate(
        """async ({ title, targetUrl, optionText }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const normalizeTitle = (value) => normalize(value).replace(/：/g, ":").toLowerCase();
            const cleanTitleLabel = (value) => normalize(value).replace(/\\s*原创\\s*$/u, "").trim();
            const normalizeUrl = (value) => {
                const raw = normalize(value);
                if (!raw || raw.startsWith("javascript:")) return "";
                try {
                    const parsed = new URL(raw, window.location.origin);
                    parsed.hash = "";
                    return parsed.href;
                } catch (_) {
                    return raw;
                }
            };
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
            const dispatchClick = (node) => {
                for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
                    node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                }
                if (typeof node.click === "function") node.click();
            };
            const titleMatches = (left, right) => {
                const leftNorm = normalizeTitle(left);
                const rightNorm = normalizeTitle(right);
                if (!leftNorm || !rightNorm) return false;
                if (leftNorm === rightNorm) return true;
                const shorter = leftNorm.length <= rightNorm.length ? leftNorm : rightNorm;
                const longer = leftNorm.length <= rightNorm.length ? rightNorm : leftNorm;
                return shorter.length >= 18 && (longer.startsWith(shorter) || longer.includes(shorter));
            };
            const targetUrlNorm = normalizeUrl(targetUrl);
            const targetOptionText = normalize(optionText);
            if (!targetOptionText) {
                return { ok: false, reason: "empty_option_text", title };
            }
            const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            const cardSelector = [
                ".publish_hover_content",
                ".weui-desktop-mass-media",
                ".weui-desktop-mass-appmsg",
                ".publish_card_container",
                ".weui-desktop-card.weui-desktop-publish",
                ".weui-desktop-media__list-col .weui-desktop-card",
                ".publish_list .publish_item"
            ].join(", ");
            const findBestContainer = (node) => {
                if (!node) return null;
                const candidates = [];
                let current = node;
                while (current && current !== document.body) {
                    if (current.querySelector) {
                        const hasOperationArea = current.querySelector(".weui-desktop-mass-media__opr, .more_icon");
                        if (hasOperationArea) {
                            const rect = current.getBoundingClientRect();
                            candidates.push({
                                node: current,
                                area: rect.width * rect.height,
                                textLength: normalize(current.innerText || current.textContent || "").length,
                            });
                        }
                    }
                    current = current.parentElement;
                }
                candidates.sort((a, b) => a.textLength - b.textLength || a.area - b.area);
                if (candidates.length) return candidates[0].node;
                return node.closest(cardSelector) || node.parentElement || node;
            };
            const titleAnchors = Array.from(document.querySelectorAll(
                "a.weui-desktop-mass-appmsg__title, a.weui-desktop-publish__title, " +
                "a[href*='mp.weixin.qq.com/s/'], a[href*='s?__biz='], " +
                ".weui-desktop-mass-appmsg__bd a[href], .weui-desktop-mass-media a[href]"
            ));
            const sampledTitles = titleAnchors
                .map((anchor) => cleanTitleLabel(anchor.textContent || anchor.getAttribute("title") || ""))
                .filter(Boolean)
                .slice(0, 8);
            const matches = [];
            const seenContainers = new Set();
            for (const anchor of titleAnchors) {
                const recordTitle =
                    cleanTitleLabel(anchor.textContent || "") ||
                    cleanTitleLabel(anchor.getAttribute("title") || "") ||
                    cleanTitleLabel(anchor.querySelector("span")?.textContent || "");
                if (!titleMatches(recordTitle, title)) continue;
                const href = anchor.getAttribute("href") || "";
                if (targetUrlNorm && normalizeUrl(href) !== targetUrlNorm) continue;
                const container = findBestContainer(anchor);
                if (!container || seenContainers.has(container)) continue;
                seenContainers.add(container);
                matches.push({
                    title: recordTitle,
                    href,
                    container,
                });
            }
            if (matches.length === 0) {
                return {
                    ok: false,
                    reason: "target_not_found",
                    title,
                    target_url: targetUrlNorm,
                    sampled_titles: sampledTitles,
                };
            }
            if (matches.length > 1) {
                return {
                    ok: false,
                    reason: "ambiguous_title",
                    title,
                    target_url: targetUrlNorm,
                    matches: matches.map((item) => ({ title: item.title, href: item.href })).slice(0, 8),
                };
            }

            const match = matches[0];
            const container = match.container;
            container.scrollIntoView({ block: "center", inline: "nearest" });
            for (const type of ["mouseover", "mouseenter", "mousemove"]) {
                container.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            const hoverTargets = [
                container.querySelector(".weui-desktop-mass-appmsg__ft"),
                container.querySelector(".weui-desktop-mass-media__opr"),
                container.querySelector(".more_icon"),
                container,
            ].filter(Boolean);
            for (const target of hoverTargets) {
                for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter", "mousemove"]) {
                    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                }
            }
            await wait(200);
            const moreSelectors = [
                ".weui-desktop-mass-media__opr .more_icon .weui-desktop-popover__target button",
                ".more_icon .weui-desktop-popover__target button",
                ".more_icon button",
                ".weui-desktop-icon__more"
            ];
            let moreButton = null;
            let moreButtonVisible = false;
            for (const selector of moreSelectors) {
                const nodes = Array.from(container.querySelectorAll(selector));
                for (const node of nodes) {
                    const candidate = node?.closest?.("button") || node;
                    if (!candidate) continue;
                    if (visible(candidate)) {
                        moreButton = candidate;
                        moreButtonVisible = true;
                        break;
                    }
                    if (!moreButton) moreButton = candidate;
                }
                if (moreButtonVisible) break;
            }
            if (!moreButton) {
                return {
                    ok: false,
                    reason: "more_button_not_found",
                    matched_title: match.title,
                    title,
                    debug: {
                        container_class: String(container.getAttribute("class") || "").slice(0, 240),
                        container_text: normalize(container.innerText || container.textContent || "").slice(0, 300),
                        more_icon_count: container.querySelectorAll(".more_icon").length,
                        operation_area_count: container.querySelectorAll(".weui-desktop-mass-media__opr").length,
                    },
                };
            }
            for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter", "mousemove"]) {
                moreButton.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            await wait(150);
            dispatchClick(moreButton);
            await wait(900);

            const findMenuOption = (root) => {
                if (!root) return null;
                const allowHiddenChild = root !== document.body && visible(root);
                const nodes = Array.from(root.querySelectorAll(
                    "li, button, a, [role='button'], .weui-desktop-dropdown__list-ele-contain, .select_option li"
                ));
                const structured = nodes.find((node) => {
                    const exactText = normalize(node.innerText || node.textContent || "") === targetOptionText;
                    return exactText && (visible(node) || allowHiddenChild);
                });
                if (structured) return structured;
                const shortTextCandidate = Array.from(root.querySelectorAll("*"))
                    .map((node) => ({
                        node,
                        text: normalize(node.innerText || node.textContent || ""),
                    }))
                    .filter((item) => item.text === targetOptionText || (
                        item.text.includes(targetOptionText) && item.text.length <= targetOptionText.length + 16
                    ))
                    .sort((a, b) => a.text.length - b.text.length)[0];
                if (shortTextCandidate && (visible(shortTextCandidate.node) || allowHiddenChild)) {
                    return shortTextCandidate.node;
                }
                if (!allowHiddenChild) return null;
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                let textNode = walker.nextNode();
                while (textNode) {
                    if (normalize(textNode.nodeValue || "") === targetOptionText) {
                        const parent = textNode.parentElement;
                        return parent?.closest?.("li, button, a, [role='button'], div, span") || parent;
                    }
                    textNode = walker.nextNode();
                }
                return null;
            };
            const searchRoots = [];
            const localWrapper = moreButton.closest(".more_icon") || moreButton.closest(".weui-desktop-popover__wrp");
            if (localWrapper) searchRoots.push(localWrapper);
            for (const popover of document.querySelectorAll(".weui-desktop-popover, .weui-desktop-dropdown-menu")) {
                if (visible(popover) && normalize(popover.innerText || popover.textContent || "").includes(targetOptionText)) {
                    searchRoots.push(popover);
                }
            }
            searchRoots.push(document.body);

            let optionNode = null;
            for (const root of searchRoots) {
                optionNode = findMenuOption(root);
                if (optionNode) break;
            }
            const visibleOptionTexts = Array.from(document.querySelectorAll(".select_option li, .weui-desktop-popover li"))
                .filter((node) => visible(node))
                .map((node) => normalize(node.innerText || node.textContent || ""))
                .filter(Boolean);
            if (!optionNode) {
                return {
                    ok: false,
                    reason: "option_not_found",
                    matched_title: match.title,
                    href: match.href,
                    title,
                    option_text: targetOptionText,
                    visible_option_texts: visibleOptionTexts,
                    visible_popover_text: searchRoots
                        .map((root) => normalize(root.innerText || root.textContent || ""))
                        .filter(Boolean)
                        .slice(0, 3),
                };
            }
            dispatchClick(optionNode);
            await wait(500);
            return {
                ok: true,
                matched_title: match.title,
                href: match.href,
                action: "menu_option_clicked",
                option_text: normalize(optionNode.innerText || optionNode.textContent || ""),
                visible_option_texts: visibleOptionTexts,
                more_button_visible: moreButtonVisible,
            };
        }""",
        {"title": title, "targetUrl": target_url, "optionText": option_text},
    )
    return result if isinstance(result, dict) else {"ok": False, "reason": "unexpected_result", "raw": result}


def _open_delete_publish_record_dialog(page, title: str, target_url: str = "") -> dict[str, Any]:
    """Open the delete confirmation dialog for one publish-history record."""
    result = _click_publish_record_menu_option(page, title, "删除", target_url=target_url)
    if result.get("reason") == "option_not_found":
        result = {**result, "reason": "delete_option_not_found"}
    if result.get("action") == "menu_option_clicked":
        result = {**result, "action": "delete_option_clicked", "delete_option_text": result.get("option_text")}
    return result


def _inspect_delete_publish_record_dialog(page) -> dict[str, Any]:
    """Read the publish-record delete confirmation dialog without clicking it."""
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
                """ + _delete_dialog_root_js() + """
                const root = dialog || document.body;
                const buttonNodes = dialog
                    ? Array.from(dialog.querySelectorAll(
                        "button, [role='button'], a.weui-desktop-btn, " +
                        "input[type='button'], input[type='submit']"
                    ))
                    : [];
                const closestPopover = dialog
                    ? dialog.closest(".weui-desktop-popover, .weui-desktop-popover__wrp, .weui-desktop-dialog, [role='dialog']")
                    : null;
                const buttons = buttonNodes.map((node, index) => {
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
                        outer_html: String(node.outerHTML || "").slice(0, 3000),
                    };
                });
                const dialogClass = dialog
                    ? (typeof dialog.className === "string" ? dialog.className : String(dialog.getAttribute("class") || ""))
                    : "";
                const popoverClass = closestPopover
                    ? (
                        typeof closestPopover.className === "string"
                            ? closestPopover.className
                            : String(closestPopover.getAttribute("class") || "")
                    )
                    : "";
                return {
                    dialog_found: Boolean(dialog),
                    dialog_tag: dialog?.tagName || "",
                    dialog_class: dialogClass.slice(0, 240),
                    dialog_text: normalize(root?.innerText || root?.textContent || "").slice(0, 1000),
                    dialog_outer_html: String(dialog?.outerHTML || "").slice(0, 20000),
                    closest_popover_tag: closestPopover?.tagName || "",
                    closest_popover_class: popoverClass.slice(0, 240),
                    closest_popover_outer_html: String(closestPopover?.outerHTML || "").slice(0, 24000),
                    page_text: normalize(document.body?.innerText || document.body?.textContent || "").slice(0, 1000),
                    buttons,
                };
            }"""
        )
    except Exception as exc:
        return {
            "dialog_type": "unknown_dialog",
            "dialog_text": "",
            "buttons": [],
            "matched_reason": f"delete dialog inspect failed: {type(exc).__name__}: {exc}",
        }
    if not isinstance(snapshot, dict):
        snapshot = {}
    dialog_text = _truncate_dialog_text(str(snapshot.get("dialog_text") or ""))
    page_text = _truncate_dialog_text(str(snapshot.get("page_text") or ""))
    buttons = [button for button in snapshot.get("buttons") or [] if isinstance(button, dict)]
    visible_buttons = [
        button for button in buttons
        if button.get("visible") and not button.get("disabled") and str(button.get("text") or "").strip()
    ]
    combined_text = f"{dialog_text} {page_text}"
    has_delete_marker = any(marker in combined_text for marker in _DELETE_DIALOG_MARKERS)
    state = {
        "dialog_type": "none",
        "dialog_found": bool(snapshot.get("dialog_found")),
        "dialog_tag": str(snapshot.get("dialog_tag") or ""),
        "dialog_class": str(snapshot.get("dialog_class") or ""),
        "dialog_text": dialog_text,
        "dialog_outer_html": str(snapshot.get("dialog_outer_html") or ""),
        "closest_popover_tag": str(snapshot.get("closest_popover_tag") or ""),
        "closest_popover_class": str(snapshot.get("closest_popover_class") or ""),
        "closest_popover_outer_html": str(snapshot.get("closest_popover_outer_html") or ""),
        "buttons": visible_buttons,
        "matched_reason": "",
    }
    if has_delete_marker:
        confirm_button = next(
            (
                button for button in visible_buttons
                if str(button.get("text") or "").strip() in _DELETE_CONFIRM_TEXTS
            ),
            None,
        )
        state.update(
            {
                "dialog_type": "delete_confirm",
                "matched_reason": "matched publish-record delete confirmation text",
                "matched_button": confirm_button,
            }
        )
        return state
    if state["dialog_found"]:
        state.update(
            {
                "dialog_type": "unknown_dialog",
                "matched_reason": "visible dialog without publish-record delete confirmation text",
            }
        )
    return state


def _inspect_publish_record_action_dialog(page) -> dict[str, Any]:
    """Read visible popover/dialog state after a publish-history menu action."""
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
                const candidates = [];
                const selectors = [
                    ".weui-desktop-dialog",
                    ".weui-desktop-dialog__bd",
                    ".weui-desktop-qrcheck",
                    ".weui-dialog",
                    "[role='dialog']",
                    ".weui-desktop-dialog__wrp",
                    ".weui-desktop-popover",
                    ".weui-desktop-popover__wrp"
                ];
                const seen = new Set();
                for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (seen.has(node) || !visible(node)) continue;
                        seen.add(node);
                        const rect = node.getBoundingClientRect();
                        const text = normalize(node.innerText || node.textContent || "");
                        if (!text) continue;
                        const hasMenu = Boolean(node.querySelector(".select_option"));
                        const buttons = Array.from(node.querySelectorAll(
                            "button, [role='button'], a.weui-desktop-btn, " +
                            "input[type='button'], input[type='submit']"
                        ));
                        candidates.push({
                            node,
                            text,
                            hasMenu,
                            buttonCount: buttons.length,
                            area: rect.width * rect.height,
                        });
                    }
                }
                candidates.sort(
                    (a, b) =>
                        Number(b.buttonCount > 0) - Number(a.buttonCount > 0) ||
                        Number(a.hasMenu) - Number(b.hasMenu) ||
                        b.area - a.area
                );
                const dialog = candidates.length ? candidates[0].node : null;
                const buttonNodes = dialog
                    ? Array.from(dialog.querySelectorAll(
                        "button, [role='button'], a.weui-desktop-btn, " +
                        "input[type='button'], input[type='submit']"
                    ))
                    : [];
                const buttons = buttonNodes.map((node, index) => {
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
                    };
                });
                return {
                    dialog_found: Boolean(dialog),
                    dialog_text: normalize(dialog?.innerText || dialog?.textContent || "").slice(0, 1000),
                    has_menu: Boolean(dialog?.querySelector?.(".select_option")),
                    buttons,
                };
            }"""
        )
    except Exception as exc:
        return {
            "dialog_type": "unknown_dialog",
            "dialog_text": "",
            "buttons": [],
            "matched_reason": f"publish record action dialog inspect failed: {type(exc).__name__}: {exc}",
        }
    if not isinstance(snapshot, dict):
        snapshot = {}
    buttons = [
        button for button in snapshot.get("buttons") or []
        if isinstance(button, dict)
        and button.get("visible")
        and not button.get("disabled")
        and str(button.get("text") or "").strip()
    ]
    dialog_text = _truncate_dialog_text(str(snapshot.get("dialog_text") or ""))
    confirm_button = next(
        (
            button for button in buttons
            if str(button.get("text") or "").strip() in _DELETE_CONFIRM_TEXTS
        ),
        None,
    )
    if not snapshot.get("dialog_found"):
        dialog_type = "none"
    elif snapshot.get("has_menu"):
        dialog_type = "menu"
    elif confirm_button:
        dialog_type = "confirm"
    else:
        dialog_type = "dialog"
    return {
        "dialog_type": dialog_type,
        "dialog_found": bool(snapshot.get("dialog_found")),
        "dialog_text": dialog_text,
        "buttons": buttons,
        "matched_button": confirm_button,
        "requires_confirmation": bool(confirm_button),
    }


def _click_publish_record_action_confirm_button(page, expected_texts: tuple[str, ...] = _DELETE_CONFIRM_TEXTS) -> dict[str, Any]:
    """Click an exact visible confirm button in the current publish-history dialog."""
    result = page.evaluate(
        """({ expectedTexts }) => {
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
                ".weui-desktop-dialog, .weui-dialog, [role='dialog'], " +
                ".weui-desktop-dialog__wrp, .weui-desktop-popover, .weui-desktop-popover__wrp"
            ))
                .filter((node) => visible(node) && !node.querySelector(".select_option"))
                .sort((a, b) => {
                    const ab = a.querySelectorAll("button, [role='button'], a.weui-desktop-btn").length;
                    const bb = b.querySelectorAll("button, [role='button'], a.weui-desktop-btn").length;
                    return bb - ab;
                });
            for (const dialog of dialogs) {
                const buttons = Array.from(dialog.querySelectorAll(
                    "button, [role='button'], a.weui-desktop-btn, " +
                    "input[type='button'], input[type='submit']"
                ));
                for (const node of buttons) {
                    const text = normalize(node.innerText || node.textContent || node.value || "");
                    const disabled = Boolean(node.disabled || node.getAttribute("aria-disabled") === "true");
                    if (!expectedTexts.includes(text) || !visible(node) || disabled) continue;
                    const className = typeof node.className === "string"
                        ? node.className
                        : String(node.getAttribute("class") || "");
                    for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
                        node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof node.click === "function") node.click();
                    return {
                        clicked: true,
                        text,
                        tag: node.tagName,
                        class: className.slice(0, 160),
                    };
                }
            }
            return { clicked: false, reason: "no exact visible enabled confirm button", expected_texts: expectedTexts };
        }""",
        {"expectedTexts": list(expected_texts)},
    )
    if not isinstance(result, dict) or not result.get("clicked"):
        raise RuntimeError(f"未找到可点击的确认按钮：{result}")
    page.wait_for_timeout(1600)
    return result


def _click_delete_confirm_button(page) -> dict[str, Any]:
    selectors = [
        ".weui-desktop-popover:visible .delect_content button.weui-desktop-btn_primary:has-text('确定')",
        ".weui-desktop-popover:visible .delect_content button:has-text('确定')",
        ".weui-desktop-popover:visible button.weui-desktop-btn_primary:has-text('确定')",
        ".weui-desktop-popover:visible button:has-text('确定')",
        ".weui-desktop-dialog:visible button.weui-desktop-btn_primary:has-text('确定')",
        ".weui-desktop-dialog:visible button:has-text('确定')",
    ]
    last_error = ""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=1500):
                continue
            text = " ".join(str(locator.inner_text(timeout=1200) or "").split())
            if text not in _DELETE_CONFIRM_TEXTS:
                continue
            locator.click(timeout=4000)
            page.wait_for_timeout(2500)
            return {
                "clicked": True,
                "text": text,
                "method": "playwright",
                "selector": selector,
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    result = page.evaluate(
        """({ expectedTexts, markers }) => {
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
            """ + _delete_dialog_root_js() + """
            if (!dialog) return { clicked: false, reason: "no dialog" };
            const dialogText = normalize(dialog.innerText || dialog.textContent || "");
            if (!markers.some((marker) => dialogText.includes(marker))) {
                return {
                    clicked: false,
                    reason: "delete confirmation text not matched",
                    dialog_text: dialogText.slice(0, 500),
                };
            }
            const buttons = Array.from(dialog.querySelectorAll(
                "button, [role='button'], a.weui-desktop-btn, " +
                "input[type='button'], input[type='submit']"
            ));
            for (const node of buttons) {
                const text = normalize(node.innerText || node.textContent || node.value || "");
                const disabled = Boolean(node.disabled || node.getAttribute("aria-disabled") === "true");
                if (expectedTexts.includes(text) && visible(node) && !disabled) {
                    const className = typeof node.className === "string"
                        ? node.className
                        : String(node.getAttribute("class") || "");
                    for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
                        node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    if (typeof node.click === "function") node.click();
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
                reason: "no exact visible enabled confirm button",
                expected_texts: expectedTexts,
                visible_buttons: buttons
                    .filter(visible)
                    .map((node) => normalize(node.innerText || node.textContent || node.value || "")),
            };
        }""",
        {"expectedTexts": list(_DELETE_CONFIRM_TEXTS), "markers": list(_DELETE_DIALOG_MARKERS)},
    )
    if not isinstance(result, dict) or not result.get("clicked"):
        raise RuntimeError(f"未找到可点击的删除确认按钮：{result}; playwright_error={last_error}")
    result = {**result, "method": "js_dispatch_fallback"}
    page.wait_for_timeout(2500)
    return result


def _inspect_delete_verification_dialog(page) -> dict[str, Any]:
    """Detect WeChat scan verification after confirming publish-record deletion."""
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
                const selectors = [
                    ".weui-desktop-dialog",
                    ".weui-dialog",
                    "[role='dialog']",
                    ".weui-desktop-dialog__wrp",
                    ".weui-desktop-popover",
                    ".weui-desktop-popover__wrp"
                ];
                const candidates = [];
                const seen = new Set();
                for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (seen.has(node) || !visible(node)) continue;
                        seen.add(node);
                        const text = normalize(node.innerText || node.textContent || "");
                        if (!text) continue;
                        const rect = node.getBoundingClientRect();
                        candidates.push({
                            node,
                            text,
                            area: rect.width * rect.height,
                            matched: text.includes("扫码验证")
                                || Boolean(node.querySelector(".weui-desktop-qrcheck, .js_qr_img img, img[src*='safeqrcode']"))
                                || text.includes("管理员微信号与运营者微信号")
                                || text.includes("非管理员微信号扫码后需要管理员验证通过"),
                        });
                    }
                }
                candidates.sort((a, b) => Number(b.matched) - Number(a.matched) || b.area - a.area);
                const dialog = candidates.length ? candidates[0].node : null;
                const text = normalize(dialog?.innerText || dialog?.textContent || "");
                const qrImg = dialog?.querySelector?.(".weui-desktop-qrcheck__img, .js_qr_img img, img[src*='safeqrcode']");
                const guideLink = dialog?.querySelector?.("a[href*='safe-operation-guide'], a[href*='setting/safe-operation-guide']");
                const dialogClass = dialog
                    ? (typeof dialog.className === "string" ? dialog.className : String(dialog.getAttribute("class") || ""))
                    : "";
                const sanitizedOuterHTML = (() => {
                    if (!dialog) return "";
                    const clone = dialog.cloneNode(true);
                    clone.querySelectorAll("img[src]").forEach((img) => img.setAttribute("src", "[redacted-qrcode]"));
                    clone.querySelectorAll("a[href]").forEach((anchor) => {
                        const href = String(anchor.getAttribute("href") || "");
                        if (href.includes("token=") || href.includes("ticket=") || href.includes("qrcheck_ticket=")) {
                            anchor.setAttribute("href", "[redacted-sensitive-link]");
                        }
                    });
                    return String(clone.outerHTML || "");
                })();
                return {
                    dialog_found: Boolean(dialog),
                    dialog_text: text.slice(0, 1200),
                    dialog_class: dialogClass.slice(0, 240),
                    dialog_outer_html: sanitizedOuterHTML.slice(0, 24000),
                    has_qrcode: Boolean(qrImg),
                    qrcode_src_redacted: qrImg ? "[redacted-qrcode]" : "",
                    help_link_present: Boolean(guideLink),
                    page_text: normalize(document.body?.innerText || document.body?.textContent || "").slice(0, 1600),
                };
            }"""
        )
    except Exception as exc:
        return {
            "dialog_type": "unknown_dialog",
            "requires_human_scan": False,
            "matched_reason": f"delete verification inspect failed: {type(exc).__name__}: {exc}",
        }
    if not isinstance(snapshot, dict):
        snapshot = {}
    dialog_text = str(snapshot.get("dialog_text") or "")
    page_text = str(snapshot.get("page_text") or "")
    combined_text = f"{dialog_text} {page_text}"
    requires_human_scan = any(
        marker in combined_text
        for marker in (
            "扫码验证",
            "管理员微信号与运营者微信号",
            "非管理员微信号扫码后需要管理员验证通过",
        )
    )
    return {
        "dialog_type": "scan_verification" if requires_human_scan else "none",
        "dialog_found": bool(snapshot.get("dialog_found")),
        "dialog_class": str(snapshot.get("dialog_class") or ""),
        "dialog_text": _truncate_dialog_text(dialog_text, limit=1000),
        "dialog_outer_html": str(snapshot.get("dialog_outer_html") or ""),
        "has_qrcode": bool(snapshot.get("has_qrcode")),
        "qrcode_src_redacted": str(snapshot.get("qrcode_src_redacted") or ""),
        "help_link_present": bool(snapshot.get("help_link_present")),
        "requires_human_scan": requires_human_scan,
        "matched_reason": "matched delete scan verification text" if requires_human_scan else "",
    }


def _click_visible_delete_option_fallback(page) -> dict[str, Any]:
    """Use Playwright text selectors after the target record's more menu is open."""
    def _dispatch_locator_click(locator, selector: str) -> dict[str, Any]:
        locator.evaluate(
            """(node) => {
                for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
                    node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                }
                if (typeof node.click === "function") node.click();
            }"""
        )
        page.wait_for_timeout(800)
        return {"clicked": True, "selector": selector, "text": "删除", "method": "dispatch"}

    selectors = [
        ".weui-desktop-popover:visible li:text-is('删除')",
        ".weui-desktop-popover:visible :text-is('删除')",
        ".select_option :text-is('删除')",
    ]
    last_error = ""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            return _dispatch_locator_click(locator, selector)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    try:
        popover = page.locator(".weui-desktop-popover").filter(has_text="删除").first
        option = popover.get_by_text("删除", exact=True).first
        return _dispatch_locator_click(option, "popover.get_by_text('删除', exact=True)")
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"Playwright fallback 未能点击删除菜单项：{last_error}")


def _click_publish_record_option_with_mouse_fallback(
    page,
    title: str,
    option_text: str,
    target_url: str = "",
) -> dict[str, Any]:
    """Use real mouse movement for hover-revealed publish-record more menus."""
    locator_info = page.evaluate(
        """({ title, targetUrl }) => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const normalizeTitle = (value) => normalize(value).replace(/：/g, ":").toLowerCase();
            const cleanTitleLabel = (value) => normalize(value).replace(/\\s*原创\\s*$/u, "").trim();
            const normalizeUrl = (value) => {
                const raw = normalize(value);
                if (!raw || raw.startsWith("javascript:")) return "";
                try {
                    const parsed = new URL(raw, window.location.origin);
                    parsed.hash = "";
                    return parsed.href;
                } catch (_) {
                    return raw;
                }
            };
            const visibleEnoughRect = (node) => {
                if (!node) return null;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 ? rect : null;
            };
            const titleMatches = (left, right) => {
                const leftNorm = normalizeTitle(left);
                const rightNorm = normalizeTitle(right);
                if (!leftNorm || !rightNorm) return false;
                if (leftNorm === rightNorm) return true;
                const shorter = leftNorm.length <= rightNorm.length ? leftNorm : rightNorm;
                const longer = leftNorm.length <= rightNorm.length ? rightNorm : leftNorm;
                return shorter.length >= 18 && (longer.startsWith(shorter) || longer.includes(shorter));
            };
            const findBestContainer = (node) => {
                const candidates = [];
                let current = node;
                while (current && current !== document.body) {
                    if (current.querySelector) {
                        const hasOperationArea = current.querySelector(".weui-desktop-mass-media__opr, .more_icon");
                        if (hasOperationArea) {
                            const rect = current.getBoundingClientRect();
                            candidates.push({
                                node: current,
                                area: rect.width * rect.height,
                                textLength: normalize(current.innerText || current.textContent || "").length,
                            });
                        }
                    }
                    current = current.parentElement;
                }
                candidates.sort((a, b) => a.textLength - b.textLength || a.area - b.area);
                return candidates.length ? candidates[0].node : null;
            };
            const targetUrlNorm = normalizeUrl(targetUrl);
            const anchors = Array.from(document.querySelectorAll(
                "a.weui-desktop-mass-appmsg__title, a.weui-desktop-publish__title, " +
                "a[href*='mp.weixin.qq.com/s/'], a[href*='s?__biz='], " +
                ".weui-desktop-mass-appmsg__bd a[href], .weui-desktop-mass-media a[href]"
            ));
            const matches = [];
            const seenContainers = new Set();
            for (const anchor of anchors) {
                const recordTitle =
                    cleanTitleLabel(anchor.textContent || "") ||
                    cleanTitleLabel(anchor.getAttribute("title") || "") ||
                    cleanTitleLabel(anchor.querySelector("span")?.textContent || "");
                if (!titleMatches(recordTitle, title)) continue;
                const href = anchor.getAttribute("href") || "";
                if (targetUrlNorm && normalizeUrl(href) !== targetUrlNorm) continue;
                const container = findBestContainer(anchor);
                if (!container || seenContainers.has(container)) continue;
                seenContainers.add(container);
                matches.push({ title: recordTitle, href, container });
            }
            if (matches.length === 0) {
                return { ok: false, reason: "target_not_found", title, target_url: targetUrlNorm };
            }
            if (matches.length > 1) {
                return {
                    ok: false,
                    reason: "ambiguous_title",
                    title,
                    target_url: targetUrlNorm,
                    matches: matches.map((item) => ({ title: item.title, href: item.href })).slice(0, 8),
                };
            }
            const match = matches[0];
            const container = match.container;
            container.scrollIntoView({ block: "center", inline: "nearest" });
            const moreButton = container.querySelector(
                ".weui-desktop-mass-media__opr .more_icon .weui-desktop-popover__target button, " +
                ".more_icon .weui-desktop-popover__target button, .more_icon button, .weui-desktop-icon__more"
            );
            const containerRect = visibleEnoughRect(container);
            const moreRect = visibleEnoughRect(moreButton?.closest?.("button") || moreButton);
            return {
                ok: true,
                matched_title: match.title,
                href: match.href,
                container_rect: containerRect
                    ? { x: containerRect.x, y: containerRect.y, width: containerRect.width, height: containerRect.height }
                    : null,
                more_rect: moreRect
                    ? { x: moreRect.x, y: moreRect.y, width: moreRect.width, height: moreRect.height }
                    : null,
            };
        }""",
        {"title": title, "targetUrl": target_url},
    )
    if not isinstance(locator_info, dict) or not locator_info.get("ok"):
        return locator_info if isinstance(locator_info, dict) else {"ok": False, "reason": "unexpected_result", "raw": locator_info}

    rect = locator_info.get("more_rect") or locator_info.get("container_rect")
    if not isinstance(rect, dict):
        return {**locator_info, "ok": False, "reason": "more_button_rect_not_found"}
    x = float(rect.get("x") or 0) + float(rect.get("width") or 0) / 2
    y = float(rect.get("y") or 0) + float(rect.get("height") or 0) / 2
    page.mouse.move(x, y)
    page.wait_for_timeout(500)
    page.mouse.click(x, y)
    page.wait_for_timeout(700)

    selectors = [
        f".weui-desktop-popover:visible li:text-is('{option_text}')",
        f".weui-desktop-popover:visible :text-is('{option_text}')",
        f".select_option :text-is('{option_text}')",
    ]
    last_error = ""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.click(timeout=2500)
            page.wait_for_timeout(800)
            return {
                **locator_info,
                "ok": True,
                "action": "menu_option_clicked_mouse_fallback",
                "option_text": option_text,
                "selector": selector,
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return {
        **locator_info,
        "ok": False,
        "reason": "option_not_found_after_mouse_hover",
        "option_text": option_text,
        "fallback_error": last_error,
    }


def _click_next_publish_history_page(page, step_logs: list[str], page_num: int) -> bool:
    try:
        next_btn = page.locator("a.weui-desktop-btn:has-text('下一页')")
        if next_btn.count() == 0 or not next_btn.first.is_enabled():
            return False
        next_btn.first.click()
        step_logs.append(f"点击下一页，进入第 {page_num + 1} 页。")
        page.wait_for_timeout(1200)
        return True
    except Exception as exc:
        step_logs.append(f"点击下一页失败：{exc}")
    return False


def _execute_publish_record_menu_operation(
    *,
    title: str,
    option_text: str,
    url: str = "",
    target_url: str = "",
    max_pages: int = 3,
    confirmed: bool = False,
    copy_url: bool = False,
) -> OperationResult:
    """Run a single action from a publish-history record's more menu."""
    title = str(title or "").strip()
    record_url = str(url or target_url or "").strip()
    option_text = str(option_text or "").strip()
    if not title:
        return OperationResult.failure(message="title 为空，无法定位发表记录")
    if option_text not in _PUBLISH_RECORD_MENU_OPTIONS:
        return OperationResult.failure(message=f"不支持的发表记录菜单项：{option_text}")

    def _run(_context, page):
        nav_logs: list[str] = []
        if not _open_publish_history_on_page(page, nav_logs):
            return OperationResult.failure(message=f"未能进入发表记录页（URL={page_url(page)}）", step_logs=nav_logs)

        step_logs = list(nav_logs)
        last_locate_result: dict[str, Any] = {}
        max_page_count = max(1, int(max_pages or 1))
        for page_num in range(1, max_page_count + 1):
            page.wait_for_timeout(1200)
            locate_result = _click_publish_record_menu_option(
                page,
                title,
                option_text,
                target_url=record_url,
            )
            last_locate_result = locate_result
            reason = str(locate_result.get("reason") or "")
            if (
                not locate_result.get("ok")
                and reason == "option_not_found"
                and str(locate_result.get("matched_title") or "")
                and any(option_text in str(text) for text in locate_result.get("visible_popover_text") or [])
            ):
                try:
                    fallback_click = _click_publish_record_option_with_mouse_fallback(
                        page,
                        title,
                        option_text,
                        target_url=record_url,
                    )
                    if not fallback_click.get("ok"):
                        raise RuntimeError(str(fallback_click))
                    locate_result = {
                        **locate_result,
                        "ok": True,
                        "reason": "",
                        "action": "menu_option_clicked_mouse_fallback",
                        "fallback_click": fallback_click,
                        "matched_title": fallback_click.get("matched_title") or locate_result.get("matched_title"),
                        "href": fallback_click.get("href") or locate_result.get("href"),
                    }
                    reason = ""
                    step_logs.append(f"已用鼠标 hover fallback 点击菜单项「{option_text}」 selector={fallback_click.get('selector')}")
                except Exception as exc:
                    locate_result = {
                        **locate_result,
                        "fallback_error": f"{type(exc).__name__}: {exc}",
                    }
            step_logs.append(
                f"第 {page_num} 页发表记录菜单动作：option={option_text} "
                f"ok={bool(locate_result.get('ok'))} reason={reason or 'ok'}"
            )

            if locate_result.get("ok"):
                page.wait_for_timeout(1000)
                action_dialog = _inspect_publish_record_action_dialog(page)
                common_state = {
                    "title": title,
                    "target_url": record_url,
                    "target_found": True,
                    "matched_title": locate_result.get("matched_title"),
                    "href": locate_result.get("href"),
                    "option_text": option_text,
                    "locate_result": locate_result,
                    "action_dialog": action_dialog,
                    "confirmed": bool(confirmed),
                    "url": page_url(page),
                }
                if copy_url:
                    common_state["copied_url"] = locate_result.get("href") or record_url

                if action_dialog.get("requires_confirmation"):
                    if not confirmed:
                        return OperationResult(
                            status="skipped",
                            message=f"已点击「{option_text}」并出现确认弹窗；未传 confirmed=true，未点击最终确认。",
                            state={
                                **common_state,
                                "requires_confirmation": True,
                                "changed": False,
                            },
                            step_logs=step_logs,
                        )
                    try:
                        button = _click_publish_record_action_confirm_button(page)
                    except Exception as exc:
                        return OperationResult(
                            status="failed",
                            message=f"「{option_text}」确认按钮点击失败：{exc}",
                            state={
                                **common_state,
                                "requires_confirmation": True,
                                "changed": False,
                            },
                            step_logs=step_logs,
                        )
                    return OperationResult(
                        status="ok",
                        message=f"已确认执行发表记录菜单动作「{option_text}」",
                        state={
                            **common_state,
                            "button": button,
                            "requires_confirmation": False,
                            "changed": True,
                        },
                        step_logs=step_logs,
                    )

                return OperationResult(
                    status="ok",
                    message=f"已执行发表记录菜单动作「{option_text}」",
                    state={
                        **common_state,
                        "requires_confirmation": False,
                        "changed": option_text != "复制链接",
                    },
                    step_logs=step_logs,
                )

            if reason != "target_not_found":
                return OperationResult(
                    status="failed",
                    message=f"发表记录菜单动作失败，已停止（option={option_text}, reason={reason or 'unknown'}）",
                    state={
                        "title": title,
                        "target_url": record_url,
                        "target_found": reason not in {"target_not_found", ""},
                        "option_text": option_text,
                        "locate_result": locate_result,
                        "confirmed": bool(confirmed),
                        "changed": False,
                        "url": page_url(page),
                    },
                    step_logs=step_logs,
                )

            if page_num >= max_page_count or not _click_next_publish_history_page(page, step_logs, page_num):
                break

        return OperationResult(
            status="failed",
            message=f"发表记录未找到标题「{title}」，未执行「{option_text}」",
            state={
                "title": title,
                "target_url": record_url,
                "target_found": False,
                "option_text": option_text,
                "locate_result": last_locate_result,
                "confirmed": bool(confirmed),
                "changed": False,
                "url": page_url(page),
            },
            step_logs=step_logs,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"发表记录菜单动作失败: {type(e).__name__}: {e}")


def _open_publish_history_on_page(page, step_logs: list[str]) -> bool:
    """Navigate to publish history page with click and href fallback."""
    page.goto(WECHAT_HOME_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    content_manage_selector = pick_selector(page, _selectors("content_manage"), timeout=2500)
    if content_manage_selector:
        try:
            page.locator(content_manage_selector).first.click()
            page.wait_for_timeout(1200)
            step_logs.append(f"已展开内容管理 selector={content_manage_selector}")
        except Exception:
            step_logs.append(f"尝试展开内容管理失败 selector={content_manage_selector}")

    failed_selectors: list[str] = []
    for selector in _selectors("publish_history"):
        try:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=4000)
            except Exception:
                href = ""
                try:
                    href = str(locator.get_attribute("href", timeout=1200) or "").strip()
                except Exception:
                    href = ""
                if href and "appmsgpublish" in href:
                    target_url = href if href.startswith("http") else f"https://mp.weixin.qq.com{href}"
                    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1200)
                else:
                    raise
            else:
                try:
                    locator.click(timeout=2000)
                except Exception:
                    href = ""
                    try:
                        href = str(locator.get_attribute("href", timeout=1200) or "").strip()
                    except Exception:
                        href = ""
                    if href and "appmsgpublish" in href:
                        target_url = href if href.startswith("http") else f"https://mp.weixin.qq.com{href}"
                        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(1200)
                    else:
                        locator.click(timeout=2000, force=True)
            try:
                page.wait_for_url("**appmsgpublish**", timeout=8000)
            except Exception:
                page.wait_for_timeout(2500)
            current_url = page_url(page)
            if "appmsgpublish" not in current_url:
                failed_selectors.append(selector)
                step_logs.append(f"发表记录入口未跳转 selector={selector} url={current_url}")
                continue
            step_logs.append(f"已进入发表记录页面 url={current_url}")
            return True
        except Exception as exc:
            failed_selectors.append(selector)
            step_logs.append(f"发表记录入口点击失败 selector={selector} error={exc}")
            continue
    if failed_selectors:
        step_logs.append(f"发表记录入口全部尝试失败：{', '.join(failed_selectors)}")
    return False


def _inspect_publish_history_document(target) -> dict[str, object]:
    result = target.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const titleAnchors = Array.from(document.querySelectorAll(
                'a.weui-desktop-mass-appmsg__title, a.weui-desktop-publish__title, ' +
                'a[href*="mp.weixin.qq.com/s/"], a[href*="s?__biz="], ' +
                '.weui-desktop-mass-appmsg__bd a[href], .weui-desktop-mass-media a[href]'
            ));
            const timeNodes = Array.from(document.querySelectorAll(
                '.weui-desktop-mass__time, .weui-desktop-publish__time, .publish_time, ' +
                'em.weui-desktop-mass__time, .weui-desktop-card__time'
            ));
            const hoverCards = Array.from(document.querySelectorAll('.publish_hover_content'));
            const massCards = Array.from(document.querySelectorAll('.weui-desktop-mass-media, .weui-desktop-mass-appmsg'));
            const dataListNodes = Array.from(document.querySelectorAll('.weui-desktop-mass-media__data-list'));
            return {
                href: window.location.href,
                title: document.title || '',
                readyState: document.readyState || '',
                title_anchor_count: titleAnchors.length,
                time_count: timeNodes.length,
                hover_card_count: hoverCards.length,
                mass_card_count: massCards.length,
                data_list_count: dataListNodes.length,
                sample_titles: titleAnchors
                    .map((node) => normalize(node.textContent || node.getAttribute('title') || ''))
                    .filter(Boolean)
                    .slice(0, 5),
                sample_times: timeNodes
                    .map((node) => normalize(node.textContent || ''))
                    .filter(Boolean)
                    .slice(0, 5),
                body_text_head: normalize((document.body && document.body.innerText) || '').slice(0, 240),
            };
        }"""
    )
    return result if isinstance(result, dict) else {}


def _scrape_publish_history_from_target(target) -> list[dict[str, Any]]:
    rows = target.evaluate(
        """() => {
            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
            const cleanTitleLabel = (value) => normalize(value).replace(/\\s*原创\\s*$/u, '').trim();
            const results = [];
            const seenStable = new Set();
            const cardSelector = '.publish_hover_content, .weui-desktop-mass-media, .weui-desktop-mass-appmsg, .publish_card_container, .weui-desktop-card.weui-desktop-publish, .weui-desktop-media__list-col .weui-desktop-card, .publish_list .publish_item';

            const absolutize = (value) => {
                const raw = normalize(value);
                if (!raw || raw.startsWith('javascript:')) return '';
                if (raw.startsWith('//')) return `${window.location.protocol}${raw}`;
                if (raw.startsWith('/')) return `${window.location.origin}${raw}`;
                return raw;
            };

            const extractThumbnail = (container) => {
                if (!container) return '';
                const thumb = container.querySelector('.weui-desktop-mass-appmsg__thumb');
                if (!thumb) return '';
                const bg = thumb.style?.backgroundImage || '';
                const m = bg.match(/url\\(["']?([^"')]+)["']?\\)/);
                return m ? absolutize(m[1]) : '';
            };

            const extractMetrics = (container) => {
                const zero = {
                    read_count: 0, like_count: 0, share_count: 0, recommend_count: 0,
                    comment_count: 0, highlight_count: 0, tip_amount: '0.00', reprint_count: 0
                };
                if (!container) return zero;
                const dataList = container.querySelector('.weui-desktop-mass-media__data-list');
                if (!dataList) return zero;
                const parseNum = (el) => {
                    const t = normalize(el?.textContent || '0');
                    const n = parseInt(t.replace(/[^0-9]/g, ''), 10);
                    return isNaN(n) ? 0 : n;
                };
                const parseMoney = (el) => {
                    const t = normalize(el?.textContent || '0');
                    return t.replace(/[^0-9.]/g, '') || '0.00';
                };
                const findDataInner = (className) => {
                    const direct = dataList.querySelector(`${className} .weui-desktop-mass-media__data__inner`);
                    if (direct) return direct;
                    const viaWrapper = dataList.querySelector(`.weui-desktop-tooltip__wrp ${className} .weui-desktop-mass-media__data__inner`);
                    if (viaWrapper) return viaWrapper;
                    const dataNode = dataList.querySelector(className);
                    if (dataNode) return dataNode.querySelector('.weui-desktop-mass-media__data__inner');
                    return null;
                };
                return {
                    read_count: parseNum(findDataInner('.appmsg-view')),
                    like_count: parseNum(findDataInner('.appmsg-like')),
                    share_count: parseNum(findDataInner('.appmsg-share')),
                    recommend_count: parseNum(findDataInner('.appmsg-haokan')),
                    comment_count: parseNum(findDataInner('.appmsg-comment')),
                    highlight_count: parseNum(findDataInner('.appmsg-underline')),
                    tip_amount: parseMoney(findDataInner('.appmsg-reward')),
                    reprint_count: parseNum(findDataInner('.appmsg-forward')),
                };
            };

            const pushItem = (title, url, publishedAt, occurrence, metricsContainer) => {
                const cleanTitle = cleanTitleLabel(title);
                const normalizedUrl = absolutize(url);
                if (cleanTitle.startsWith('¥') || cleanTitle.length < 2) return;
                if (normalizedUrl.includes('merchant/reward')) return;
                let appmsgId = null;
                try {
                    if (normalizedUrl) {
                        const parsed = new URL(normalizedUrl, window.location.origin);
                        appmsgId = parsed.searchParams.get('appmsgid');
                    }
                } catch (_) {}
                const stableKey = appmsgId
                    ? `appmsg:${appmsgId}`
                    : normalizedUrl
                        ? `url:${normalizedUrl}`
                        : `publish:${cleanTitle}|${normalize(publishedAt)}|${occurrence}`;
                if (seenStable.has(stableKey)) return;
                seenStable.add(stableKey);
                const metrics = extractMetrics(metricsContainer);
                results.push({
                    title: cleanTitle,
                    url: normalizedUrl,
                    appmsg_id: appmsgId,
                    published_at: normalize(publishedAt),
                    remote_key: stableKey,
                    read_count: metrics.read_count,
                    like_count: metrics.like_count,
                    share_count: metrics.share_count,
                    recommend_count: metrics.recommend_count,
                    comment_count: metrics.comment_count,
                    highlight_count: metrics.highlight_count,
                    tip_amount: metrics.tip_amount,
                    reprint_count: metrics.reprint_count,
                    thumbnail: extractThumbnail(metricsContainer),
                });
            };

            const extractPublishedAt = (container) => {
                const dateNode =
                    container?.querySelector('.weui-desktop-mass__time') ||
                    container?.querySelector('.weui-desktop-publish__time') ||
                    container?.querySelector('.publish_time') ||
                    container?.querySelector('.weui-desktop-card__time');
                let publishedAt = normalize(dateNode ? dateNode.textContent : '');
                if (!publishedAt) {
                    const text = normalize(container?.innerText || '');
                    const match = text.match(/((?:昨天|前天|星期[一二三四五六日天])?\\s*[0-9]{1,2}:[0-9]{2}|[0-9]{1,2}月[0-9]{1,2}日|[0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2})/);
                    publishedAt = match ? normalize(match[1]) : '';
                }
                return publishedAt;
            };

            const findBestContainer = (node) => {
                if (!node) return null;
                const directPublish = node.closest('.publish_hover_content');
                if (directPublish) return directPublish;
                let current = node;
                while (current && current !== document.body) {
                    if (current.matches && current.matches(cardSelector)) {
                        const hasTimeNode = current.querySelector('.weui-desktop-mass__time, .weui-desktop-publish__time, .publish_time, .weui-desktop-card__time');
                        if (hasTimeNode) return current;
                    }
                    current = current.parentElement;
                }
                return node.closest(cardSelector) || node.parentElement || node;
            };

            const titleAnchors = Array.from(
                document.querySelectorAll(
                    'a.weui-desktop-mass-appmsg__title, a.weui-desktop-publish__title, ' +
                    'a[href*="mp.weixin.qq.com/s/"], a[href*="s?__biz="], ' +
                    '.weui-desktop-mass-appmsg__bd a[href], .weui-desktop-mass-media a[href]'
                )
            );
            titleAnchors.forEach((anchor, index) => {
                const container = findBestContainer(anchor);
                const href = anchor.getAttribute('href') || '';
                const title =
                    cleanTitleLabel(anchor.textContent || '') ||
                    cleanTitleLabel(anchor.getAttribute('title') || '') ||
                    cleanTitleLabel(anchor.querySelector('span')?.textContent || '');
                const publishedAt = extractPublishedAt(container);
                pushItem(title, href, publishedAt, index, container);
            });

            if (!results.length) {
                const containers = Array.from(document.querySelectorAll(cardSelector));
                containers.forEach((container, index) => {
                    const titleNode =
                        container.querySelector('.weui-desktop-mass-appmsg__title span') ||
                        container.querySelector('.weui-desktop-mass-appmsg__title') ||
                        container.querySelector('.weui-desktop-publish__title span') ||
                        container.querySelector('.weui-desktop-publish__title') ||
                        container.querySelector('.weui-desktop-publish__cover__title span') ||
                        container.querySelector('.weui-desktop-publish__cover__title') ||
                        container.querySelector('.weui-desktop-card__title') ||
                        container.querySelector('a[title]') ||
                        container.querySelector('a.weui-desktop-mass-appmsg__title span') ||
                        container.querySelector('a span') ||
                        container.querySelector('h3');
                    const linkNode =
                        container.querySelector('a.weui-desktop-mass-appmsg__title') ||
                        container.querySelector('a.weui-desktop-publish__title') ||
                        container.querySelector('a[href*="mp.weixin.qq.com/s/"]') ||
                        container.querySelector('a[href*="s?__biz="]') ||
                        container.querySelector('a[href]');
                    const title = cleanTitleLabel(titleNode ? titleNode.textContent : '');
                    const href = linkNode ? linkNode.getAttribute('href') || '' : '';
                    const publishedAt = extractPublishedAt(container);
                    pushItem(title, href, publishedAt, index, container);
                });
            }

            return results.slice(0, 80);
        }"""
    )
    if not isinstance(rows, list):
        raise RuntimeError("发表记录抓取结果格式异常。")
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "title": str(row.get("title") or "").strip(),
                "url": str(row.get("url") or "").strip(),
                "appmsg_id": str(row.get("appmsg_id") or "").strip() or None,
                "published_at": str(row.get("published_at") or "").strip() or None,
                "remote_key": str(row.get("remote_key") or "").strip() or None,
                "read_count": int(row.get("read_count") or 0),
                "like_count": int(row.get("like_count") or 0),
                "share_count": int(row.get("share_count") or 0),
                "recommend_count": int(row.get("recommend_count") or 0),
                "comment_count": int(row.get("comment_count") or 0),
                "highlight_count": int(row.get("highlight_count") or 0),
                "tip_amount": str(row.get("tip_amount") or "0.00"),
                "reprint_count": int(row.get("reprint_count") or 0),
                "thumbnail": str(row.get("thumbnail") or "").strip(),
            }
        )
    return items


def _scrape_publish_history_items(page, step_logs: list[str] | None = None) -> list[dict[str, Any]]:
    diagnostic_logs = step_logs if step_logs is not None else []
    targets = [("page", page)]
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for index, frame in enumerate(frames):
        if frame is page.main_frame:
            continue
        targets.append((f"frame[{index}]", frame))

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, target in targets:
        try:
            diag = _inspect_publish_history_document(target)
            if diag:
                diagnostic_logs.append(
                    "发表记录DOM "
                    f"{label} url={diag.get('href') or ''} "
                    f"titleAnchors={diag.get('title_anchor_count', 0)} "
                    f"timeNodes={diag.get('time_count', 0)} "
                    f"hoverCards={diag.get('hover_card_count', 0)} "
                    f"massCards={diag.get('mass_card_count', 0)} "
                    f"dataLists={diag.get('data_list_count', 0)} "
                    f"samples={','.join(str(item) for item in (diag.get('sample_titles') or [])[:3]) or 'none'}"
                )
            rows = _scrape_publish_history_from_target(target)
            diagnostic_logs.append(f"发表记录抽取 {label} rows={len(rows)}")
        except Exception as exc:
            diagnostic_logs.append(f"发表记录抽取 {label} 失败：{exc}")
            continue
        for row in rows:
            stable_key = (
                str(row.get("remote_key") or "").strip()
                or str(row.get("url") or "").strip()
                or f"{str(row.get('title') or '').strip()}|{str(row.get('published_at') or '').strip()}"
            )
            if not stable_key or stable_key in seen:
                continue
            seen.add(stable_key)
            merged.append(row)
    return merged


def _scrape_publish_history_pages(page, *, max_pages: int = 3, limit: int = 20) -> tuple[list[dict[str, Any]], list[str]]:
    step_logs: list[str] = []
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for page_num in range(1, max(1, max_pages) + 1):
        page.wait_for_timeout(1500)
        page_items = _scrape_publish_history_items(page, step_logs)
        new_count = 0
        for row in page_items:
            key = str(row.get("remote_key") or row.get("url") or "").strip()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            items.append(row)
            new_count += 1
            if len(items) >= max(1, limit):
                break
        step_logs.append(f"第 {page_num} 页抓取 {len(page_items)} 条，新增 {new_count} 条，累计 {len(items)} 条。")
        if len(items) >= max(1, limit) or new_count == 0:
            break
        next_btn = page.locator("a.weui-desktop-btn:has-text('下一页')")
        try:
            if next_btn.count() == 0 or not next_btn.first.is_enabled():
                break
            next_btn.first.click()
            step_logs.append(f"点击下一页，进入第 {page_num + 1} 页。")
        except Exception:
            break
    return items, step_logs


def _to_float_money(value: Any) -> float:
    try:
        cleaned = re.sub(r"[^0-9.]", "", str(value or "0"))
        return float(cleaned or 0)
    except Exception:
        return 0.0


def _metric_score(item: dict[str, Any]) -> float:
    return (
        int(item.get("read_count") or 0)
        + int(item.get("like_count") or 0) * 8
        + int(item.get("share_count") or 0) * 10
        + int(item.get("recommend_count") or 0) * 6
        + int(item.get("comment_count") or 0) * 12
        + int(item.get("highlight_count") or 0) * 4
        + int(item.get("reprint_count") or 0) * 15
        + _to_float_money(item.get("tip_amount")) * 20
    )


def _analyze_publish_metrics(
    items: list[dict[str, Any]],
    *,
    title: str = "",
    url: str = "",
    snapshot_at: str | None = None,
) -> dict[str, Any]:
    return build_publish_metrics_analysis(items, title=title, url=url, snapshot_at=snapshot_at)


@operation(
    name="wechat.review_publish_history",
    category="review",
    description=(
        "只读：进入发表记录页，复核远端已发表文章列表。"
        "可传 title 做命中校验；返回后 Agent 应询问用户是否继续触发 analyze_publish_metrics。"
    ),
    params={
        "title": "可选，目标文章标题；传入时会校验发表记录是否命中",
        "limit": "最多返回多少条，默认 20",
        "max_pages": "最多翻页数，默认 3",
    },
)
def review_publish_history(ctx, title: str = "", limit: int = 20, max_pages: int = 3) -> OperationResult:
    def _run(_context, page):
        nav_logs: list[str] = []
        if not _open_publish_history_on_page(page, nav_logs):
            return OperationResult.failure(message=f"未能进入发表记录页（URL={page_url(page)}）", step_logs=nav_logs)
        items, scrape_logs = _scrape_publish_history_pages(page, max_pages=max_pages, limit=limit)
        matched = _find_item_by_title(items, title) if title else None
        try:
            settings = get_settings()
            settings.ensure_runtime_dirs()
            screenshot_path = settings.runtime_dir / f"review_publish_history_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            screenshot = str(screenshot_path)
        except Exception:
            screenshot = None
        state = {
            "items": items,
            "count": len(items),
            "url": page_url(page),
            "title": title,
            "target_found": bool(matched) if title else None,
            "matched_item": matched,
            "should_offer_metrics_analysis": True,
            "suggested_next_operation": "wechat.analyze_publish_metrics",
            "ask_user_prompt": "是否要基于发表记录触发全维度数据指标分析？",
            "screenshot": screenshot,
        }
        logs = nav_logs + scrape_logs
        if title and not matched:
            return OperationResult.failure(
                message=f"发表记录未命中标题「{title}」",
                step_logs=logs,
                **state,
            )
        return OperationResult.success(
            message=f"发表记录复核完成，共读取 {len(items)} 条" + (f"，已命中「{title}」" if title else ""),
            step_logs=logs,
            **state,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"review_publish_history 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.delete_publish_record",
    category="review",
    description=(
        "危险写操作：进入发表记录，按标题定位已发表文章，点击更多菜单里的删除。"
        "默认 confirmed=False 只打开并识别删除确认弹窗，不点击最终“确认”；"
        "只有 confirmed=True 才执行真实删除。"
    ),
    params={
        "title": "必填，目标已发表文章标题（部分匹配需 18 字以上）",
        "url": "可选，目标文章链接；同标题多篇时必须传，用于精确定位",
        "target_url": "url 的别名",
        "confirmed": "默认 False；为 True 时才点击删除确认弹窗里的“确认”按钮",
        "max_pages": "最多翻页数，默认 3",
    },
)
def delete_publish_record(
    ctx,
    title: str,
    confirmed: bool = False,
    max_pages: int = 3,
    url: str = "",
    target_url: str = "",
) -> OperationResult:
    """Delete one publish-history record by title, with explicit confirmation."""
    title = str(title or "").strip()
    record_url = str(url or target_url or "").strip()
    if not title:
        return OperationResult.failure(message="title 为空，无法定位要删除的发表记录")

    def _run(_context, page):
        nav_logs: list[str] = []
        if not _open_publish_history_on_page(page, nav_logs):
            return OperationResult.failure(message=f"未能进入发表记录页（URL={page_url(page)}）", step_logs=nav_logs)

        step_logs = list(nav_logs)
        last_locate_result: dict[str, Any] = {}
        max_page_count = max(1, int(max_pages or 1))
        for page_num in range(1, max_page_count + 1):
            page.wait_for_timeout(1200)
            locate_result = _open_delete_publish_record_dialog(page, title, target_url=record_url)
            last_locate_result = locate_result
            reason = str(locate_result.get("reason") or "")
            if (
                not locate_result.get("ok")
                and reason == "delete_option_not_found"
                and str(locate_result.get("matched_title") or "")
                and any("删除" in str(text) for text in locate_result.get("visible_popover_text") or [])
            ):
                try:
                    fallback_click = _click_publish_record_option_with_mouse_fallback(
                        page,
                        title,
                        "删除",
                        target_url=record_url,
                    )
                    if not fallback_click.get("ok"):
                        raise RuntimeError(str(fallback_click))
                    locate_result = {
                        **locate_result,
                        "ok": True,
                        "reason": "",
                        "action": "delete_option_clicked_mouse_fallback",
                        "fallback_click": fallback_click,
                        "matched_title": fallback_click.get("matched_title") or locate_result.get("matched_title"),
                        "href": fallback_click.get("href") or locate_result.get("href"),
                    }
                    reason = ""
                    step_logs.append(f"已用鼠标 hover fallback 点击删除菜单项 selector={fallback_click.get('selector')}")
                except Exception as exc:
                    locate_result = {
                        **locate_result,
                        "fallback_error": f"{type(exc).__name__}: {exc}",
                    }
            step_logs.append(f"第 {page_num} 页删除定位结果：ok={bool(locate_result.get('ok'))} reason={reason or 'ok'}")

            if locate_result.get("ok"):
                try:
                    page.wait_for_function(
                        """(markers) => {
                            const text = String(document.body?.innerText || document.body?.textContent || "");
                            return markers.some((marker) => text.includes(marker));
                        }""",
                        list(_DELETE_DIALOG_MARKERS),
                        timeout=2500,
                    )
                except Exception:
                    page.wait_for_timeout(1500)
                delete_dialog = _inspect_delete_publish_record_dialog(page)
                dialog_type = str(delete_dialog.get("dialog_type") or "none")
                step_logs.append(f"删除确认弹窗识别：dialog_type={dialog_type}")
                common_state = {
                    "title": title,
                    "target_url": record_url,
                    "target_found": True,
                    "matched_title": locate_result.get("matched_title"),
                    "locate_result": locate_result,
                    "delete_dialog": delete_dialog,
                    "url": page_url(page),
                }
                if dialog_type != "delete_confirm":
                    return OperationResult(
                        status="failed",
                        message=f"未识别到发表记录删除确认弹窗，已停止（dialog_type={dialog_type}）",
                        state={
                            **common_state,
                            "requires_confirmation": True,
                            "confirmed": bool(confirmed),
                            "deleted": False,
                        },
                        step_logs=step_logs,
                    )
                if not confirmed:
                    return OperationResult(
                        status="skipped",
                        message="已定位目标发表记录并打开删除确认弹窗；未传 confirmed=true，未点击最终确认。",
                        state={
                            **common_state,
                            "requires_confirmation": True,
                            "confirmed": False,
                            "deleted": False,
                            "suggested_next_operation": "wechat.delete_publish_record",
                        },
                        step_logs=step_logs,
                    )
                try:
                    click_info = _click_delete_confirm_button(page)
                except Exception as exc:
                    return OperationResult(
                        status="failed",
                        message=f"删除确认按钮点击失败：{exc}",
                        state={
                            **common_state,
                            "requires_confirmation": True,
                            "confirmed": True,
                            "deleted": False,
                        },
                        step_logs=step_logs,
                    )
                post_dialog = _inspect_delete_publish_record_dialog(page)
                post_dialog_type = str(post_dialog.get("dialog_type") or "none")
                step_logs.append(f"删除确认点击后复核：dialog_type={post_dialog_type}")
                verification_dialog = _inspect_delete_verification_dialog(page)
                if verification_dialog.get("requires_human_scan"):
                    step_logs.append("删除确认后进入扫码验证，等待人工扫码。")
                    return OperationResult(
                        status="skipped",
                        message="已点击删除确认按钮，微信要求扫码验证；删除尚未完成。",
                        state={
                            **common_state,
                            "button": click_info,
                            "post_delete_dialog": post_dialog,
                            "verification_dialog": verification_dialog,
                            "requires_confirmation": False,
                            "requires_human_scan": True,
                            "confirmed": True,
                            "deleted": False,
                            "suggested_next_operation": "wechat.review_publish_history",
                        },
                        step_logs=step_logs,
                    )
                if post_dialog_type == "delete_confirm":
                    return OperationResult(
                        status="failed",
                        message="已点击删除确认按钮，但删除确认弹窗仍存在；平台未确认删除，已停止。",
                        state={
                            **common_state,
                            "button": click_info,
                            "post_delete_dialog": post_dialog,
                            "requires_confirmation": True,
                            "confirmed": True,
                            "deleted": False,
                        },
                        step_logs=step_logs,
                    )
                post_locate = _click_publish_record_menu_option(page, title, "删除", target_url=record_url)
                post_reason = str(post_locate.get("reason") or "")
                step_logs.append(
                    f"删除确认后目标复核：ok={bool(post_locate.get('ok'))} reason={post_reason or 'ok'}"
                )
                if post_locate.get("ok") or post_reason != "target_not_found":
                    return OperationResult(
                        status="failed",
                        message=(
                            "已点击删除确认按钮，但目标发表记录仍可定位或仍可再次打开删除入口；"
                            "平台未确认删除生效。"
                        ),
                        state={
                            **common_state,
                            "button": click_info,
                            "post_delete_dialog": post_dialog,
                            "post_locate_result": post_locate,
                            "requires_confirmation": False,
                            "confirmed": True,
                            "deleted": False,
                        },
                        step_logs=step_logs,
                    )
                return OperationResult(
                    status="ok",
                    message=f"已确认删除发表记录「{locate_result.get('matched_title') or title}」",
                    state={
                        **common_state,
                        "button": click_info,
                        "requires_confirmation": False,
                        "confirmed": True,
                        "deleted": True,
                    },
                    step_logs=step_logs,
                )

            if reason != "target_not_found":
                return OperationResult(
                    status="failed",
                    message=f"发表记录删除定位失败，已停止（reason={reason or 'unknown'}）",
                    state={
                        "title": title,
                        "target_url": record_url,
                        "target_found": reason not in {"target_not_found", ""},
                        "locate_result": locate_result,
                        "confirmed": bool(confirmed),
                        "deleted": False,
                        "url": page_url(page),
                    },
                    step_logs=step_logs,
                )

            if page_num >= max_page_count or not _click_next_publish_history_page(page, step_logs, page_num):
                break

        return OperationResult(
            status="failed",
            message=f"发表记录未找到标题「{title}」，未执行删除",
            state={
                "title": title,
                "target_url": record_url,
                "target_found": False,
                "locate_result": last_locate_result,
                "confirmed": bool(confirmed),
                "deleted": False,
                "url": page_url(page),
            },
            step_logs=step_logs,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"delete_publish_record 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.pin_publish_record",
    category="review",
    description="发表记录菜单动作：置顶目标记录。默认只点击并观测弹层；如有确认弹窗，confirmed=true 才继续。",
    params={
        "title": "必填，目标已发表文章标题",
        "url": "可选，目标文章链接；同标题多篇时建议传入",
        "target_url": "url 的别名",
        "confirmed": "默认 False；如出现确认弹窗，传 True 才继续确认",
        "max_pages": "最多翻页数，默认 3",
    },
)
def pin_publish_record(ctx, title: str, confirmed: bool = False, max_pages: int = 3, url: str = "", target_url: str = "") -> OperationResult:
    return _execute_publish_record_menu_operation(
        title=title,
        option_text="置顶",
        url=url,
        target_url=target_url,
        max_pages=max_pages,
        confirmed=confirmed,
    )


@operation(
    name="wechat.set_publish_record_private",
    category="review",
    description="发表记录菜单动作：仅自己可见目标记录。",
    params={
        "title": "必填，目标已发表文章标题",
        "url": "可选，目标文章链接；同标题多篇时建议传入",
        "target_url": "url 的别名",
        "confirmed": "默认 False；如出现确认弹窗，传 True 才继续确认",
        "max_pages": "最多翻页数，默认 3",
    },
)
def set_publish_record_private(
    ctx,
    title: str,
    confirmed: bool = False,
    max_pages: int = 3,
    url: str = "",
    target_url: str = "",
) -> OperationResult:
    return _execute_publish_record_menu_operation(
        title=title,
        option_text="仅自己可见",
        url=url,
        target_url=target_url,
        max_pages=max_pages,
        confirmed=confirmed,
    )


@operation(
    name="wechat.close_publish_record_recommendation",
    category="review",
    description="发表记录菜单动作：关闭推荐目标记录。",
    params={
        "title": "必填，目标已发表文章标题",
        "url": "可选，目标文章链接；同标题多篇时建议传入",
        "target_url": "url 的别名",
        "confirmed": "默认 False；如出现确认弹窗，传 True 才继续确认",
        "max_pages": "最多翻页数，默认 3",
    },
)
def close_publish_record_recommendation(
    ctx,
    title: str,
    confirmed: bool = False,
    max_pages: int = 3,
    url: str = "",
    target_url: str = "",
) -> OperationResult:
    return _execute_publish_record_menu_operation(
        title=title,
        option_text="关闭推荐",
        url=url,
        target_url=target_url,
        max_pages=max_pages,
        confirmed=confirmed,
    )


@operation(
    name="wechat.copy_publish_record_link",
    category="review",
    description="发表记录菜单动作：复制目标记录链接到剪贴板，并在状态中返回 copied_url。",
    params={
        "title": "必填，目标已发表文章标题",
        "url": "可选，目标文章链接；同标题多篇时建议传入",
        "target_url": "url 的别名",
        "max_pages": "最多翻页数，默认 3",
    },
)
def copy_publish_record_link(ctx, title: str, max_pages: int = 3, url: str = "", target_url: str = "") -> OperationResult:
    return _execute_publish_record_menu_operation(
        title=title,
        option_text="复制链接",
        url=url,
        target_url=target_url,
        max_pages=max_pages,
        copy_url=True,
    )


@operation(
    name="wechat.change_publish_record_collection",
    category="review",
    description="发表记录菜单动作：修改目标记录合集。",
    params={
        "title": "必填，目标已发表文章标题",
        "url": "可选，目标文章链接；同标题多篇时建议传入",
        "target_url": "url 的别名",
        "confirmed": "默认 False；如出现确认弹窗，传 True 才继续确认",
        "max_pages": "最多翻页数，默认 3",
    },
)
def change_publish_record_collection(
    ctx,
    title: str,
    confirmed: bool = False,
    max_pages: int = 3,
    url: str = "",
    target_url: str = "",
) -> OperationResult:
    return _execute_publish_record_menu_operation(
        title=title,
        option_text="修改合集",
        url=url,
        target_url=target_url,
        max_pages=max_pages,
        confirmed=confirmed,
    )


@operation(
    name="wechat.change_publish_record_claim_source",
    category="review",
    description="发表记录菜单动作：声明创作来源。默认只点击并观测弹层。",
    params={
        "title": "必填，目标已发表文章标题",
        "url": "可选，目标文章链接；同标题多篇时建议传入",
        "target_url": "url 的别名",
        "confirmed": "默认 False；如出现确认弹窗，传 True 才继续确认",
        "max_pages": "最多翻页数，默认 3",
    },
)
def change_publish_record_claim_source(
    ctx,
    title: str,
    confirmed: bool = False,
    max_pages: int = 3,
    url: str = "",
    target_url: str = "",
) -> OperationResult:
    return _execute_publish_record_menu_operation(
        title=title,
        option_text="声明创作来源",
        url=url,
        target_url=target_url,
        max_pages=max_pages,
        confirmed=confirmed,
    )


@operation(
    name="wechat.analyze_publish_metrics",
    category="review",
    description=(
        "只读：基于发表记录抓取阅读、点赞、分享、推荐、留言、划线、赞赏、转载等指标，"
        "量化稿件质量和受众喜爱程度。"
    ),
    params={
        "title": "可选，目标文章标题；传入时优先分析该文章，未命中则分析全部抓取记录",
        "url": "可选，目标文章链接；同标题多篇时优先使用",
        "limit": "最多分析多少条，默认 20",
        "max_pages": "最多翻页数，默认 3",
    },
)
def analyze_publish_metrics(ctx, title: str = "", url: str = "", limit: int = 20, max_pages: int = 3) -> OperationResult:
    def _run(_context, page):
        nav_logs: list[str] = []
        if not _open_publish_history_on_page(page, nav_logs):
            return OperationResult.failure(message=f"未能进入发表记录页（URL={page_url(page)}）", step_logs=nav_logs)
        items, scrape_logs = _scrape_publish_history_pages(page, max_pages=max_pages, limit=limit)
        analysis = _analyze_publish_metrics(items, title=title, url=url, snapshot_at=_utcnow())
        return OperationResult.success(
            message="发表记录指标分析完成",
            step_logs=nav_logs + scrape_logs,
            items=items,
            count=len(items),
            title=title,
            target_url=url,
            analysis_key=analysis.get("analysis_key"),
            analysis_snapshot_at=analysis.get("analysis_snapshot_at"),
            analysis=analysis,
            content_strategy_profile=latest_content_strategy_profile([
                {
                    "operation_name": "wechat.analyze_publish_metrics",
                    "status": "success",
                    "params": {"state": {"analysis": analysis}},
                }
            ]),
            current_url=page_url(page),
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"analyze_publish_metrics 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.review_content_strategy",
    category="review",
    description="只读：读取最近发表指标快照，生成可被雷达和文章质量门禁复用的运营策略画像。",
    params={"lookback_runs": "读取最近多少条审计记录，默认 20"},
)
def review_content_strategy(ctx, lookback_runs: int = 20) -> OperationResult:
    try:
        limit = max(1, min(int(lookback_runs), 200))
        tasks, _ = get_repository().list_publish_tasks(limit=limit)
        profile = latest_content_strategy_profile(tasks)
        if not profile.get("available"):
            return OperationResult.skip(
                message=profile.get("message") or "尚无可用运营策略画像",
                content_strategy_profile=profile,
                suggested_next_operation=profile.get("suggested_next_operation"),
            )
        return OperationResult.success(
            message="公众号内容策略画像已生成",
            content_strategy_profile=profile,
            suggested_next_operation=profile.get("suggested_next_operation"),
        )
    except Exception as e:
        return OperationResult.failure(message=f"review_content_strategy 失败: {type(e).__name__}: {e}")


@operation(
    name="wechat.review_content_performance",
    category="review",
    description=(
        "只读：基于发表记录的多页指标抓取与历史快照，输出复盘判断、强弱标签、"
        "可复用模式和下一步建议。"
    ),
    params={
        "title": "可选，目标文章标题；优先做精确命中，歧义时必须补 url",
        "url": "可选，目标文章链接；优先于 title",
        "max_pages": "抓取发表记录的最多翻页数，默认 3",
        "lookback_runs": "历史快照回看条数，默认 5",
    },
)
def review_content_performance(
    ctx,
    title: str = "",
    url: str = "",
    max_pages: int = 3,
    lookback_runs: int = 5,
) -> OperationResult:
    if not str(title or "").strip() and not str(url or "").strip():
        return OperationResult.failure(
            message="review_content_performance 需要 title 或 url 用于定位目标文章",
            suggested_next_operation="wechat.analyze_publish_metrics",
        )

    def _run(_context, page):
        nav_logs: list[str] = []
        if not _open_publish_history_on_page(page, nav_logs):
            return OperationResult.failure(message=f"未能进入发表记录页（URL={page_url(page)}）", step_logs=nav_logs)

        items, scrape_logs = _scrape_publish_history_pages(page, max_pages=max_pages, limit=80)
        analysis = _analyze_publish_metrics(items, title=title, url=url, snapshot_at=_utcnow())
        if analysis.get("target_status") in ("ambiguous_title", "target_not_found") and (title or url):
            return OperationResult.failure(
                message=(
                    "无法唯一定位要复盘的文章："
                    + ("标题歧义" if analysis.get("target_status") == "ambiguous_title" else "未找到目标")
                ),
                step_logs=nav_logs + scrape_logs,
                analysis=analysis,
                target_found=False,
                target_status=analysis.get("target_status"),
                suggested_next_operation="wechat.review_publish_history",
            )

        analysis_key = str(analysis.get("analysis_key") or "").strip()
        if not analysis_key:
            return OperationResult.failure(
                message="复盘分析缺少稳定 analysis_key，无法继续做历史对比",
                step_logs=nav_logs + scrape_logs,
                analysis=analysis,
                suggested_next_operation="wechat.analyze_publish_metrics",
            )

        repo = get_repository()
        history_rows, _ = repo.list_publish_task_snapshots(
            operation_name="wechat.analyze_publish_metrics",
            analysis_key=analysis_key,
            limit=max(1, int(lookback_runs)),
        )
        history_snapshots = summarize_task_snapshots(
            history_rows,
            operation_name="wechat.analyze_publish_metrics",
            analysis_key=analysis_key,
            limit=max(1, int(lookback_runs)),
        )
        review = build_content_performance_review(analysis, history_snapshots)
        hint = build_title_history_hint(title or str(analysis.get("requested_title") or ""), history_snapshots)
        if hint.get("similar_count"):
            review["title_history_hint"] = hint

        state = {
            "analysis": analysis,
            "analysis_key": analysis_key,
            "analysis_snapshot_at": analysis.get("analysis_snapshot_at"),
            "history_snapshots": history_snapshots,
            "lookback_runs": max(1, int(lookback_runs)),
            "review": review,
            "performance_label": review.get("performance_label"),
            "trend": review.get("trend"),
            "delta": review.get("delta"),
            "weakness_tags": review.get("weakness_tags"),
            "winning_patterns": review.get("winning_patterns"),
            "next_content_guidance": review.get("next_content_guidance"),
            "suggested_next_operation": review.get("suggested_next_operation"),
            "analysis_key": analysis_key,
        }
        if hint.get("similar_count"):
            state["title_history_hint"] = hint

        return OperationResult.success(
            message="公众号内容表现复盘完成",
            step_logs=nav_logs + scrape_logs,
            **state,
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"review_content_performance 失败: {type(e).__name__}: {e}")
