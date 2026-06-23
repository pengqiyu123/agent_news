"""Source probing helpers.

The existing fetch_source API intentionally catches exceptions and returns an
empty list so sync does not crash. Probing needs a richer per-source result, so
this module wraps the fetchers without changing the old compatibility surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.intel import RawItem, Source
from .connectors import FETCHERS


def _source_requires_url(source: Source) -> bool:
    driver = str(source.config.get("driver") or source.kind or "").strip()
    return driver in {"rss", "rss_feed", "wordpress_rest"} or source.kind in {"rss", "html"}


@dataclass
class ProbeResult:
    source_key: str
    source_name: str
    status: str
    item_count: int = 0
    error: str | None = None
    sample_items: list[dict[str, Any]] | None = None
    items: list[RawItem] | None = None

    def as_state(self, *, include_items: bool = False) -> dict[str, Any]:
        state = {
            "source_key": self.source_key,
            "source_name": self.source_name,
            "status": self.status,
            "item_count": self.item_count,
            "error": self.error,
            "sample_items": self.sample_items or [],
        }
        if include_items:
            state["items"] = [item.model_dump() for item in (self.items or [])]
        return state


def _sample_items(items: list[RawItem], limit: int) -> list[dict[str, Any]]:
    return [
        {
            "title": item.title,
            "link": item.link,
            "published_at": item.published_at,
            "source_key": item.source_key,
        }
        for item in items[: max(0, limit)]
    ]


def probe_source(source: Source, *, limit_per_source: int = 3, include_items: bool = False) -> ProbeResult:
    if not source.enabled:
        return ProbeResult(
            source_key=source.key,
            source_name=source.name or source.key,
            status="disabled",
            error="source disabled",
            sample_items=[],
            items=[],
        )
    if _source_requires_url(source) and not source.url:
        return ProbeResult(
            source_key=source.key,
            source_name=source.name or source.key,
            status="failed",
            error="empty url",
            sample_items=[],
            items=[],
        )
    fetcher = FETCHERS.get(source.kind)
    if fetcher is None:
        return ProbeResult(
            source_key=source.key,
            source_name=source.name or source.key,
            status="failed",
            error=f"no fetcher for kind '{source.kind}'",
            sample_items=[],
            items=[],
        )
    try:
        items = fetcher(source)
    except Exception as exc:  # noqa: BLE001 - per-source isolation
        return ProbeResult(
            source_key=source.key,
            source_name=source.name or source.key,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            sample_items=[],
            items=[],
        )

    valid_items = [item for item in items if item.title and item.link]
    status = "ok" if valid_items else "empty"
    return ProbeResult(
        source_key=source.key,
        source_name=source.name or source.key,
        status=status,
        item_count=len(valid_items),
        error=None if valid_items else "no valid items with title and link",
        sample_items=_sample_items(valid_items, limit_per_source),
        items=valid_items if include_items else [],
    )
