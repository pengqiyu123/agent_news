"""Connectors — fetch raw items from sources.

Stage 1. Each source kind has a fetcher. RSS is the primary one; HTML scraping
lives here too but stays optional (the old project's HTML monitors were tightly
coupled to per-site selectors — we keep a generic readability fallback instead).

All fetchers are pure: given a Source, return a list of RawItem. No DB writes.
The atomic operation layer handles persistence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from ..models.intel import RawItem, Source

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_raw_id() -> str:
    import uuid

    return f"raw-{uuid.uuid4().hex[:12]}"


# ── RSS fetcher ─────────────────────────────────────────────────────────────
def fetch_rss(source: Source, *, timeout: float = 15.0, max_items: int = 50) -> list[RawItem]:
    """Fetch a single RSS/Atom feed and return normalized RawItems.

    Uses feedparser. Items without a title are skipped (can't cluster them).
    """
    import feedparser

    if not source.url:
        return []
    try:
        # feedparser doesn't take a timeout directly; rely on its default.
        parsed = feedparser.parse(source.url)
    except Exception as e:  # noqa: BLE001 — network errors must not crash the sync
        logger.warning("RSS fetch failed for %s: %s", source.key, e)
        return []

    items: list[RawItem] = []
    for entry in parsed.entries[:max_items]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        link = entry.get("link", "")
        summary = entry.get("summary") or entry.get("description") or ""
        published = entry.get("published") or entry.get("updated")
        items.append(
            RawItem(
                id=_new_raw_id(),
                source_key=source.key,
                source_name=source.name or source.key,
                title=title,
                link=link,
                summary=summary,
                published_at=published,
                collected_at=_utcnow(),
                tags=list(source.tags),
                metadata={"platform": "web", "native_id": entry.get("id")},
            )
        )
    return items


# ── Fetcher registry ────────────────────────────────────────────────────────
# Maps SourceKind → fetcher callable. New kinds register here.
FETCHERS: dict[str, Callable[[Source], list[RawItem]]] = {
    "rss": fetch_rss,
}


def fetch_source(source: Source) -> list[RawItem]:
    """Dispatch to the right fetcher by source kind. Returns [] on unknown kind."""
    fetcher = FETCHERS.get(source.kind)
    if fetcher is None:
        logger.warning("No fetcher registered for source kind '%s' (%s)", source.kind, source.key)
        return []
    try:
        return fetcher(source)
    except Exception as e:  # noqa: BLE001 — isolate per-source failures
        logger.warning("Fetcher for %s (%s) failed: %s", source.key, source.kind, e)
        return []


def collect_sources(sources: list[Source], *, max_workers: int = 4) -> list[RawItem]:
    """Fetch from all enabled sources, concurrently, aggregating raw items.

    A single source failing never aborts the batch — fetch_source catches.
    """
    enabled = [s for s in sources if s.enabled]
    if not enabled:
        return []

    all_items: list[RawItem] = []
    if max_workers <= 1:
        for source in enabled:
            all_items.extend(fetch_source(source))
        return all_items

    # Concurrent fetch via threads (RSS is I/O-bound).
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_source, s): s for s in enabled}
        for future in as_completed(futures):
            try:
                all_items.extend(future.result())
            except Exception as e:  # noqa: BLE001
                logger.warning("Source %s raised: %s", futures[future].key, e)
    return all_items
