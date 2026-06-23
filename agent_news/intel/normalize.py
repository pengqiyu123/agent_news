"""Normalize — turn RawItems into DiscoveryItems (tokenized, dedupe-keyed).

Pure function. This is Stage 1b: it does field shaping + tokenization only.
No scoring, no clustering — those live in score.py and cluster.py, each
individually callable (the design fix vs. the old fused build_intel_state).
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from ..models.intel import DiscoveryItem, RawItem, Source
from .tokenizer import extract_anchor_tokens, tokenize


def _canonicalize_url(url: str) -> str:
    """Strip tracking params + fragments for a stable dedupe key base."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"


def _dedupe_key(item: RawItem) -> str:
    """Build a stable dedupe key from canonical url + title tokens hash.

    Two items pointing at the same canonical URL or sharing enough title
    tokens collapse to the same key — the first stage of dedup before
    cross-source clustering.
    """
    canon = _canonicalize_url(item.link)
    if canon:
        return f"url:{canon}"
    # No usable URL: hash normalized title tokens.
    title_tokens = tokenize(item.title)
    if title_tokens:
        h = hashlib.sha1("|".join(sorted(title_tokens)).encode("utf-8")).hexdigest()[:12]
        return f"title:{h}"
    return f"raw:{item.id}"


def normalize_raw_items(
    raw_items: list[RawItem],
    sources_by_key: dict[str, Source] | None = None,
) -> list[DiscoveryItem]:
    """Convert RawItems to DiscoveryItems with tokens + dedupe keys.

    Args:
        raw_items: freshly collected items.
        sources_by_key: optional source lookup for tag/entity enrichment.

    Returns discovery items ready for cluster_discovery_items(). Items with
    empty titles are dropped — they can't be clustered meaningfully.
    """
    sources_by_key = sources_by_key or {}
    out: list[DiscoveryItem] = []
    for raw in raw_items:
        if not raw.title.strip():
            continue
        source = sources_by_key.get(raw.source_key)
        tags = list(raw.tags)
        if source:
            tags.extend(t for t in source.tags if t not in tags)
        entity_names = list(raw.metadata.get("entity_names", [])) if raw.metadata else []
        out.append(
            DiscoveryItem(
                id=raw.id,
                source_key=raw.source_key,
                source_name=raw.source_name or (source.name if source else ""),
                title=raw.title.strip(),
                summary=(raw.summary or "").strip(),
                link=raw.link,
                canonical_link=_canonicalize_url(raw.link),
                dedupe_key=_dedupe_key(raw),
                source_native_id=raw.metadata.get("native_id") if raw.metadata else None,
                title_tokens=tokenize(raw.title),
                anchor_tokens=extract_anchor_tokens(raw.title, known_entities=entity_names),
                published_at=raw.published_at,
                collected_at=raw.collected_at,
                tags=tags,
                engagement_score=raw.engagement_score,
                entity_names=entity_names,
                metadata=dict(raw.metadata) if raw.metadata else {},
            )
        )
    return out
