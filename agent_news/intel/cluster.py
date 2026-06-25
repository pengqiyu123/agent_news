"""Cluster — union-find merge of discovery items into event clusters.

No scheduler, no state mutation: pure functions return clusters. The merge
predicate uses token Jaccard overlap, only comparing items within a 24h window.

This is Stage 2. It is individually callable — the agent can re-cluster after
adding new raw items without re-fetching sources.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from ..models.intel import DiscoveryItem, Source
from .tokenizer import jaccard

# Two items merge if their token Jaccard similarity is at least this threshold.
MERGE_THRESHOLD = 0.34
# Only compare items within this time window; stale items should not cluster with fresh ones.
WINDOW_HOURS = 24


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # Normalize to UTC-aware — SQLite round-trips can drop tzinfo.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class _UnionFind:
    """Minimal disjoint-set for clustering."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # path compression
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _within_window(a: datetime | None, b: datetime | None, hours: int = WINDOW_HOURS) -> bool:
    """True if two timestamps are close enough to be the same event."""
    if a is None or b is None:
        return True  # unknown time — allow comparison, let tokens decide
    return abs((a - b).total_seconds()) <= hours * 3600


def _should_merge(left: DiscoveryItem, right: DiscoveryItem) -> bool:
    """Merge predicate: enough token overlap AND within the time window.

    Also short-circuits on identical dedupe key (same canonical URL) — those
    are unambiguously the same story.
    """
    if left.dedupe_key and left.dedupe_key == right.dedupe_key:
        return True
    if not left.title_tokens or not right.title_tokens:
        return False
    return jaccard(left.title_tokens, right.title_tokens) >= MERGE_THRESHOLD


def cluster_discovery_items(
    discovery_items: list[DiscoveryItem],
    merge_threshold: float = MERGE_THRESHOLD,
    window_hours: int = WINDOW_HOURS,
) -> list[list[DiscoveryItem]]:
    """Cluster discovery items into groups describing the same event.

    Returns a list of clusters (each a list of DiscoveryItems). Single-item
    clusters are kept — an event may have only one source.
    """
    n = len(discovery_items)
    if n == 0:
        return []
    uf = _UnionFind(n)

    # Pre-parse timestamps once.
    times = [_parse_dt(item.published_at or item.collected_at) for item in discovery_items]

    for i in range(n):
        for j in range(i + 1, n):
            if not _within_window(times[i], times[j], window_hours):
                continue
            # Threshold override via param (for agent tuning)
            left, right = discovery_items[i], discovery_items[j]
            if left.dedupe_key and left.dedupe_key == right.dedupe_key:
                uf.union(i, j)
                continue
            if (
                left.title_tokens
                and right.title_tokens
                and jaccard(left.title_tokens, right.title_tokens) >= merge_threshold
            ):
                uf.union(i, j)

    # Group indices by root.
    groups: dict[int, list[int]] = {}
    for idx in range(n):
        root = uf.find(idx)
        groups.setdefault(root, []).append(idx)

    return [[discovery_items[i] for i in indices] for indices in groups.values()]


def event_id_for_cluster(cluster: list[DiscoveryItem]) -> str:
    """Deterministic event id from the cluster's canonical links + dedupe keys.

    Same cluster → same id across runs (idempotent upsert).
    """
    parts = sorted({item.canonical_link or item.dedupe_key or item.link for item in cluster if item})
    raw = "|".join(parts) or "|".join(item.title for item in cluster)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"evt-{digest}"


def representative_item(cluster: list[DiscoveryItem], sources_by_key: dict[str, Source] | None = None) -> DiscoveryItem:
    """Pick the best representative item from a cluster.

    Ranking: published_at (newer better) → engagement → source priority/weight.
    Used for the event's title/summary/link.
    """
    if not cluster:
        raise ValueError("empty cluster")
    sources_by_key = sources_by_key or {}
    now = datetime.now(timezone.utc)

    def score(item: DiscoveryItem) -> tuple:
        pub = _parse_dt(item.published_at or item.collected_at) or now
        recency = -abs((now - pub).total_seconds())  # newer = higher (less negative)
        src = sources_by_key.get(item.source_key)
        priority = src.priority if src else 50
        return (recency, item.engagement_score, priority)

    return max(cluster, key=score)
