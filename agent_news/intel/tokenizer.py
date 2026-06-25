"""Tokenizer — lightweight title/anchor token extraction.

Clustering is hand-rolled n-gram overlap. We keep it dependency-free so the
radar works without any NLP library installed.

Two token kinds:
- title_tokens: word-ish n-grams from the title (for clustering similarity)
- anchor_tokens: high-signal terms (brands, products, numbers) kept on the
  event for tagging and audience-fit scoring.
"""

from __future__ import annotations

import re
from typing import Iterable

# CJK range + latin word boundaries. Keep simple; no jieba dependency.
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-+.]*")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
# Noise tokens to drop (articles, connectors, filler).
_STOPWORDS = {
    # latin
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "with",
    "is", "are", "was", "were", "be", "by", "at", "as", "it", "this", "that",
    "from", "into", "over", "after", "before", "new", "says", "said",
    # cjk filler (single chars)
    "的", "了", "在", "是", "和", "与", "及", "或", "等", "为", "对", "由", "从",
}


def _split_cjk(text: str) -> list[str]:
    """Split CJK runs into overlapping bigrams — cheap but effective for clustering."""
    tokens: list[str] = []
    for run in _CJK_RE.findall(text):
        run = run.strip()
        if len(run) <= 1:
            continue
        # bigrams
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
        # also keep the full run if short (proper noun like "字节跳动")
        if len(run) <= 6:
            tokens.append(run)
    return tokens


def tokenize(text: str) -> list[str]:
    """Extract normalized tokens from a title or short text.

    Returns a de-duplicated, lowercased token list mixing:
    - latin words (length >= 2)
    - CJK bigrams and short proper-noun runs
    - standalone numbers
    Stopwords are removed. Order is preserved by first appearance.
    """
    if not text:
        return []
    text = text.strip()
    raw: list[str] = []
    raw.extend(_split_cjk(text))
    for m in _LATIN_WORD_RE.findall(text):
        raw.append(m.lower())
    for m in _NUMBER_RE.findall(text):
        raw.append(m)

    seen: set[str] = set()
    out: list[str] = []
    for tok in raw:
        if len(tok) < 2:
            continue
        if tok.lower() in _STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def extract_anchor_tokens(text: str, known_entities: Iterable[str] = ()) -> list[str]:
    """High-signal tokens for tagging: capitalized latin terms + known entities + numbers.

    Anchor tokens survive into the event for audience-fit scoring and display.
    """
    if not text:
        return []
    anchors: list[str] = []
    # Capitalized latin sequences (likely proper nouns / brands): GPT, OpenAI, Claude
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9\-]{1,}", text):
        tok = m.group()
        if tok.lower() not in _STOPWORDS and len(tok) >= 2:
            anchors.append(tok)
    # Known entities passed in (e.g. watchlist)
    for ent in known_entities:
        if ent and ent in text:
            anchors.append(ent)
    # Numbers (versions, quantities)
    for m in _NUMBER_RE.findall(text):
        anchors.append(m)
    # de-dup preserve order
    seen: set[str] = set()
    out: list[str] = []
    for a in anchors:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Token-set Jaccard similarity — the merge predicate's core.

    Returns 0.0 for empty sets. Used by cluster_discovery_items to decide
    whether two discovery items describe the same event.
    """
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 0.0
