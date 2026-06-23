"""Deep dive — fetch source full text and extract facts/quotes/timeline.

Stage 4. This is rule-based extraction (the old project does NOT call an LLM
for the core deep dive — authorship is the external AI's job). We fetch each
event's source URLs, extract clean text, and pull out:
- facts: declarative sentences with numbers/dates/verbs of assertion
- quotes: text inside quotation marks
- timeline: sentences with date/time markers

The writing guide (house style) is attached separately so the AI knows how to
turn this material into a finished article.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ..models.intel import (
    DeepDiveSourceItem,
    DiscoveryItem,
    EventDeepDive,
    IntelEvent,
)

logger = logging.getLogger(__name__)


# ── Full-text fetch + extraction ────────────────────────────────────────────
def fetch_and_extract_link(url: str, *, timeout: float = 12.0) -> DeepDiveSourceItem:
    """Fetch one URL and extract its main text content.

    Tries trafilatura first (best for articles), falls back to readability-lxml.
    Returns a DeepDiveSourceItem with fetch/extract status regardless of success.
    """
    import httpx

    item = DeepDiveSourceItem(link=url, fetch_status="pending", extract_status="pending")
    if not url:
        item.fetch_status = "failed"
        item.error = "empty url"
        return item

    # 1. Fetch HTML.
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "agent-news/0.1 (+https://github.com)"})
        resp.raise_for_status()
        html = resp.text
        item.fetch_status = "success"
    except Exception as e:  # noqa: BLE001 — network isolation
        item.fetch_status = "failed"
        item.error = f"{type(e).__name__}: {e}"
        return item

    # 2. Extract main text.
    cleaned = ""
    try:
        import trafilatura

        cleaned = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
        if cleaned:
            item.extract_status = "success"
    except Exception as e:  # noqa: BLE001
        logger.debug("trafilatura failed for %s: %s", url, e)

    if not cleaned:
        try:
            from readability import Document

            doc = Document(html)
            cleaned = doc.summary()  # returns HTML
            # crude HTML strip
            cleaned = re.sub(r"<[^>]+>", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            item.extract_status = "success" if cleaned else "failed"
        except Exception as e:  # noqa: BLE001
            item.extract_status = "failed"
            if not item.error:
                item.error = f"readability: {type(e).__name__}: {e}"

    item.cleaned_full_text = cleaned
    item.word_count = len(cleaned.split()) if cleaned else 0
    # excerpt = first ~300 chars
    item.excerpt = cleaned[:300] + ("…" if len(cleaned) > 300 else "") if cleaned else ""
    return item


# ── Fact / quote / timeline extraction (rule-based) ─────────────────────────
# Sentences with numbers, percentages, money, or strong assertion verbs.
_FACT_PATTERN = re.compile(
    r"(\b\d+(?:[.,]\d+)?\s*(?:%|percent|亿|万|million|billion|MB|GB|TB|fps|fps)\b"
    r"|\$?\s?\d+(?:[.,]\d+)?\s*(?:美元|元|dollars?)"
    r"|\b(?:announced|launched|released|raised|acquired|shut down|deprecated)\b)",
    re.IGNORECASE,
)
# Quoted speech: "...", "...", 「...」, "..."
_QUOTE_PATTERNS = [
    re.compile(r'"([^"]{8,300})"'),
    re.compile(r'"([^"]{8,300})"'),
    re.compile(r"「([^」]{8,300})」"),
    re.compile(r"“([^”]{8,300})”"),
]
# Date / time markers.
_TIME_PATTERN = re.compile(
    r"(\b(?:today|yesterday|this week|last week|on Monday|on Tuesday|on Wednesday|"
    r"on Thursday|on Friday|on Saturday|on Sunday)\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b"
    r"|\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}\b"
    r"|\b\d{1,2}月\d{1,2}日\b)",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (handles both 。and .)."""
    # Normalize whitespace, then split on sentence enders.
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[。！？.?!])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def extract_facts(full_text: str, *, max_facts: int = 12) -> list[str]:
    """Pull out declarative sentences containing concrete data or assertions."""
    if not full_text:
        return []
    facts: list[str] = []
    for sent in _split_sentences(full_text):
        if _FACT_PATTERN.search(sent) and sent not in facts:
            facts.append(sent)
            if len(facts) >= max_facts:
                break
    return facts


def extract_quotes(full_text: str, *, max_quotes: int = 8) -> list[str]:
    """Extract quoted speech."""
    if not full_text:
        return []
    quotes: list[str] = []
    for pattern in _QUOTE_PATTERNS:
        for match in pattern.finditer(full_text):
            q = match.group(1).strip()
            if q and q not in quotes:
                quotes.append(q)
                if len(quotes) >= max_quotes:
                    return quotes
    return quotes


def extract_timeline(full_text: str, *, max_items: int = 8) -> list[str]:
    """Extract sentences with date/time markers (rough chronological signal)."""
    if not full_text:
        return []
    timeline: list[str] = []
    for sent in _split_sentences(full_text):
        if _TIME_PATTERN.search(sent) and sent not in timeline:
            timeline.append(sent)
            if len(timeline) >= max_items:
                break
    return timeline


# ── Worthiness evaluation ───────────────────────────────────────────────────
def evaluate_worthiness(event: IntelEvent, sources: list[DeepDiveSourceItem]) -> tuple[bool, str]:
    """Decide whether an event is worth writing up. Returns (worth, reason).

    Heuristic: enough successful full-text fetches + decent composite score.
    The agent may override this — it's a suggestion, not a gate.
    """
    success = sum(1 for s in sources if s.fetch_status == "success" and s.word_count > 100)
    if event.composite_score < 25:
        return False, f"composite_score too low ({event.composite_score:.0f})"
    if success == 0:
        return False, "no source full text successfully extracted"
    if success == 1 and event.composite_score < 50:
        return False, "only one source and moderate score — thin material"
    return True, f"{success} sources extracted, composite={event.composite_score:.0f}"


# ── Full deep dive assembly ─────────────────────────────────────────────────
def build_deep_dive(
    event: IntelEvent,
    discovery_items: list[DiscoveryItem],
    *,
    article_writing_guide: str = "",
    max_sources: int = 6,
    timeout: float = 12.0,
) -> EventDeepDive:
    """Build a complete EventDeepDive for one event.

    Fetches the event's top source URLs, extracts facts/quotes/timeline, and
    evaluates worthiness. Does NOT call an LLM — this is the material pack the
    external AI turns into prose.
    """
    import uuid

    dive_id = f"dd-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Pick source URLs to fetch: prefer items belonging to this event.
    event_item_ids = set(event.discovery_item_ids)
    candidates = [di for di in discovery_items if di.id in event_item_ids]
    if not candidates:
        candidates = discovery_items[:max_sources]
    # rank by engagement then take top N unique links
    seen_links: set[str] = set()
    to_fetch: list[DiscoveryItem] = []
    for di in sorted(candidates, key=lambda x: x.engagement_score, reverse=True):
        if di.link and di.link not in seen_links:
            seen_links.add(di.link)
            to_fetch.append(di)
        if len(to_fetch) >= max_sources:
            break

    sources: list[DeepDiveSourceItem] = []
    for di in to_fetch:
        item = fetch_and_extract_link(di.link, timeout=timeout)
        item.source_key = di.source_key
        item.source_name = di.source_name
        item.title = di.title
        item.published_at = di.published_at
        # extract per-source quotes for attribution
        item.quotes = extract_quotes(item.cleaned_full_text, max_quotes=3)
        sources.append(item)

    success_sources = [s for s in sources if s.extract_status == "success"]
    combined_text = "\n\n".join(s.cleaned_full_text for s in success_sources if s.cleaned_full_text)

    facts = extract_facts(combined_text)
    quotes = extract_quotes(combined_text)
    timeline = extract_timeline(combined_text)
    worth, reason = evaluate_worthiness(event, sources)

    return EventDeepDive(
        id=dive_id,
        event_id=event.id,
        status="ready" if success_sources else ("partial" if sources else "failed"),
        started_at=now,
        finished_at=datetime.now(timezone.utc).isoformat(),
        attempted_count=len(to_fetch),
        success_count=len(success_sources),
        failed_count=len(sources) - len(success_sources),
        resolved_evidence_pack=[
            {"link": s.link, "title": s.title, "excerpt": s.excerpt, "word_count": s.word_count}
            for s in sources
        ],
        full_text_sources=sources,
        sources=sources,
        facts=facts,
        quotes=quotes,
        timeline=timeline,
        worthiness={"worth_to_brief": worth, "reason": reason},
        article_writing_guide=article_writing_guide,
    )
