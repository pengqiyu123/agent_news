"""WeChat publish-history review and metrics operations.

The DOM scraping logic is ported from auto-news-studio
backend/app/publishers/wechat/history.py. These operations are read-only:
they navigate to 内容管理 -> 发表记录, scrape remote publish records, and
optionally compute engagement metrics. They never click publish or edit content.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ...browser import BROWSER_MANAGER, WECHAT_HOME_URL, default_wechat_channel, get_selectors
from ...browser.dom import page_url, pick_selector
from ...config import get_settings
from ...models.operation import OperationResult
from ..base import operation

_CHANNEL = default_wechat_channel()


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


def _open_publish_history_on_page(page, step_logs: list[str]) -> bool:
    """Navigate to publish history page, using the old project's fallback pattern."""
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


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _enrich_metric_item(item: dict[str, Any]) -> dict[str, Any]:
    reads = int(item.get("read_count") or 0)
    likes = int(item.get("like_count") or 0)
    shares = int(item.get("share_count") or 0)
    recommends = int(item.get("recommend_count") or 0)
    comments = int(item.get("comment_count") or 0)
    highlights = int(item.get("highlight_count") or 0)
    reprints = int(item.get("reprint_count") or 0)
    tip_amount = _to_float_money(item.get("tip_amount"))
    engagement_actions = likes + shares + recommends + comments + highlights + reprints
    return {
        **item,
        "tip_amount_numeric": tip_amount,
        "engagement_actions": engagement_actions,
        "engagement_rate": _rate(engagement_actions, reads),
        "like_rate": _rate(likes, reads),
        "share_rate": _rate(shares, reads),
        "comment_rate": _rate(comments, reads),
        "quality_score": round(_metric_score(item), 2),
        "signals": {
            "audience_reach": reads,
            "audience_approval": likes + recommends,
            "spread": shares + reprints,
            "discussion": comments,
            "deep_reading": highlights,
            "monetization": tip_amount,
        },
    }


def _analyze_publish_metrics(items: list[dict[str, Any]], *, title: str = "") -> dict[str, Any]:
    enriched = [_enrich_metric_item(item) for item in items]
    matched = _find_item_by_title(enriched, title) if title else None
    scope_items = [matched] if matched else enriched
    scope_items = [item for item in scope_items if item]

    total_reads = sum(int(item.get("read_count") or 0) for item in scope_items)
    total_likes = sum(int(item.get("like_count") or 0) for item in scope_items)
    total_shares = sum(int(item.get("share_count") or 0) for item in scope_items)
    total_recommends = sum(int(item.get("recommend_count") or 0) for item in scope_items)
    total_comments = sum(int(item.get("comment_count") or 0) for item in scope_items)
    total_highlights = sum(int(item.get("highlight_count") or 0) for item in scope_items)
    total_reprints = sum(int(item.get("reprint_count") or 0) for item in scope_items)
    total_tips = round(sum(float(item.get("tip_amount_numeric") or 0) for item in scope_items), 2)
    total_engagement = total_likes + total_shares + total_recommends + total_comments + total_highlights + total_reprints

    sorted_by_score = sorted(enriched, key=lambda item: float(item.get("quality_score") or 0), reverse=True)
    sorted_by_reads = sorted(enriched, key=lambda item: int(item.get("read_count") or 0), reverse=True)
    sorted_by_share_rate = sorted(
        [item for item in enriched if int(item.get("read_count") or 0) > 0],
        key=lambda item: float(item.get("share_rate") or 0),
        reverse=True,
    )

    return {
        "scope": "matched_title" if matched else "all_items",
        "target_found": bool(matched) if title else None,
        "matched_item": matched,
        "summary": {
            "item_count": len(scope_items),
            "total_reads": total_reads,
            "total_likes": total_likes,
            "total_shares": total_shares,
            "total_recommends": total_recommends,
            "total_comments": total_comments,
            "total_highlights": total_highlights,
            "total_reprints": total_reprints,
            "total_tip_amount": total_tips,
            "total_engagement_actions": total_engagement,
            "overall_engagement_rate": _rate(total_engagement, total_reads),
            "overall_like_rate": _rate(total_likes, total_reads),
            "overall_share_rate": _rate(total_shares, total_reads),
            "overall_comment_rate": _rate(total_comments, total_reads),
        },
        "top_items": {
            "by_quality_score": sorted_by_score[:5],
            "by_reads": sorted_by_reads[:5],
            "by_share_rate": sorted_by_share_rate[:5],
        },
        "metric_meaning": {
            "read_count": "阅读人数，衡量触达和选题吸引力",
            "like_count": "点赞人数，衡量认可度",
            "share_count": "分享人数，衡量传播性",
            "recommend_count": "推荐人数，衡量平台内推荐意愿",
            "comment_count": "留言条数，衡量讨论度",
            "highlight_count": "划线人数，衡量深读和摘录价值",
            "tip_amount": "赞赏金额，衡量付费认可",
            "reprint_count": "被转载次数，衡量外部引用和扩散",
        },
    }


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
    name="wechat.analyze_publish_metrics",
    category="review",
    description=(
        "只读：基于发表记录抓取阅读、点赞、分享、推荐、留言、划线、赞赏、转载等指标，"
        "量化稿件质量和受众喜爱程度。"
    ),
    params={
        "title": "可选，目标文章标题；传入时优先分析该文章，未命中则分析全部抓取记录",
        "limit": "最多分析多少条，默认 20",
        "max_pages": "最多翻页数，默认 3",
    },
)
def analyze_publish_metrics(ctx, title: str = "", limit: int = 20, max_pages: int = 3) -> OperationResult:
    def _run(_context, page):
        nav_logs: list[str] = []
        if not _open_publish_history_on_page(page, nav_logs):
            return OperationResult.failure(message=f"未能进入发表记录页（URL={page_url(page)}）", step_logs=nav_logs)
        items, scrape_logs = _scrape_publish_history_pages(page, max_pages=max_pages, limit=limit)
        analysis = _analyze_publish_metrics(items, title=title)
        return OperationResult.success(
            message="发表记录指标分析完成",
            step_logs=nav_logs + scrape_logs,
            items=items,
            count=len(items),
            title=title,
            analysis=analysis,
            url=page_url(page),
        )

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_run, reset_on_failure=False)
    except Exception as e:
        return OperationResult.failure(message=f"analyze_publish_metrics 失败: {type(e).__name__}: {e}")
