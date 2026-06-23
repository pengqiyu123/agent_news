"""Source discovery and governance helpers.

MVP scope: external agents provide candidate URLs; agent-news validates,
deduplicates, scores, and gates additions to the official source table.
"""

from __future__ import annotations

import re
from hashlib import sha1
from typing import Any
from urllib.parse import urlparse

from ..models.intel import Source
from .source_probe import ProbeResult, probe_source


def slugify_source_key(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path
    host = host.lower().removeprefix("www.")
    base = re.sub(r"[^a-z0-9]+", "-", host).strip("-") or "source"
    digest = sha1(value.encode("utf-8")).hexdigest()[:6]
    return f"{base[:42].strip('-')}-{digest}"


def normalize_candidates(
    candidates: list[Any] | None,
    *,
    query: str = "",
    topic: str = "",
    kind: str = "rss",
    language: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates or []:
        if isinstance(raw, str):
            item = {"url": raw}
        elif isinstance(raw, dict):
            item = dict(raw)
        else:
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(
            {
                "id": f"cand-{sha1(url.encode('utf-8')).hexdigest()[:12]}",
                "url": url,
                "name": str(item.get("name") or item.get("title") or "").strip(),
                "kind": str(item.get("kind") or kind or "rss").strip() or "rss",
                "topic": str(item.get("topic") or topic or "").strip(),
                "language": str(item.get("language") or language or "").strip(),
                "query": query,
                "discovered_by": str(item.get("discovered_by") or "agent").strip(),
                "evidence": list(item.get("evidence") or []),
            }
        )
        if len(normalized) >= max(1, int(limit or 10)):
            break
    return normalized


def _domain(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.netloc or parsed.path).lower().removeprefix("www.")


def dedupe_source(url: str, sources: list[Source]) -> dict[str, Any]:
    normalized_url = url.rstrip("/").lower()
    domain = _domain(url)
    for source in sources:
        if source.url.rstrip("/").lower() == normalized_url:
            return {
                "duplicate": True,
                "matched_source_key": source.key,
                "reason": "same_url",
            }
    for source in sources:
        if domain and _domain(source.url) == domain:
            return {
                "duplicate": True,
                "matched_source_key": source.key,
                "reason": "same_domain",
            }
    return {"duplicate": False, "matched_source_key": None, "reason": ""}


def _looks_like_rejected_page(url: str) -> str | None:
    lower = url.lower()
    if any(part in lower for part in ("/search?", "?q=", "/login", "/signin", "/account/login")):
        return "search_or_login_url"
    if lower in {"https://x.com", "https://twitter.com", "https://www.linkedin.com", "https://linkedin.com"}:
        return "social_homepage"
    return None


def _quality_score(source: Source, probe: ProbeResult, dedupe: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    if probe.status == "ok":
        score += 30
        reasons.append("源可解析")
    elif probe.status == "empty":
        score += 8
        risks.append("源可访问但没有有效条目")
    else:
        risks.append(probe.error or "源验证失败")

    if probe.item_count >= 5:
        score += 35
        reasons.append(f"返回 {probe.item_count} 条有效内容")
    elif probe.item_count >= 3:
        score += 25
        reasons.append(f"返回 {probe.item_count} 条有效内容")
    elif probe.item_count > 0:
        score += 10
        risks.append("有效条目偏少")
    else:
        risks.append("没有有效条目")

    if not dedupe.get("duplicate"):
        score += 20
        reasons.append("未与现有源重复")
    else:
        risks.append(f"疑似重复源: {dedupe.get('matched_source_key')}")

    if source.kind == "rss":
        score += 10
        reasons.append("RSS/Atom 源优先")
    else:
        score += 3
        risks.append(f"源类型 {source.kind} 需要额外选择器稳定性")

    if source.tags:
        score += 5

    score = max(0, min(score, 100))
    return score, reasons, risks


def validate_source_candidate(
    *,
    url: str,
    kind: str = "rss",
    topic: str = "",
    name: str = "",
    limit_per_source: int = 5,
    existing_sources: list[Source],
) -> dict[str, Any]:
    url = str(url or "").strip()
    kind = str(kind or "rss").strip() or "rss"
    topic = str(topic or "").strip()
    name = str(name or "").strip()
    if not url:
        return {
            "valid": False,
            "score": 0,
            "decision": "reject",
            "reason": "empty url",
            "sample_items": [],
            "dedupe": {"duplicate": False, "matched_source_key": None, "reason": ""},
            "suggested_source": {},
            "risks": ["empty url"],
        }

    rejected = _looks_like_rejected_page(url)
    dedupe = dedupe_source(url, existing_sources)
    source = Source(
        key=slugify_source_key(url),
        name=name or _domain(url) or url,
        kind=kind,
        url=url,
        tags=[topic] if topic else [],
        priority=70,
    )
    if rejected:
        return {
            "valid": False,
            "score": 0,
            "decision": "reject",
            "reason": rejected,
            "sample_items": [],
            "dedupe": dedupe,
            "suggested_source": source.model_dump(),
            "risks": [rejected],
        }

    probe = probe_source(source, limit_per_source=limit_per_source)
    score, reasons, risks = _quality_score(source, probe, dedupe)
    if dedupe.get("duplicate"):
        score = min(score, 59)
    if probe.status == "failed":
        score = min(score, 39)
    if score >= 80:
        decision = "auto_add"
    elif score >= 60:
        decision = "needs_confirmation"
    else:
        decision = "reject"
    valid = decision in ("auto_add", "needs_confirmation")
    reason = "; ".join(reasons or risks or [probe.error or "validated"])
    return {
        "valid": valid,
        "score": score,
        "decision": decision,
        "reason": reason,
        "sample_items": probe.sample_items or [],
        "probe_status": probe.status,
        "probe_error": probe.error,
        "dedupe": dedupe,
        "suggested_source": source.model_dump(),
        "risks": risks,
        "suggested_next_operation": "radar.add_validated_source" if valid else "radar.discover_sources",
    }


def proposal_from_validation(validated_source: dict[str, Any]) -> dict[str, Any]:
    source = dict(validated_source.get("suggested_source") or {})
    proposal = {
        "valid": bool(validated_source.get("valid")),
        "score": int(validated_source.get("score") or 0),
        "decision": str(validated_source.get("decision") or "reject"),
        "reason": str(validated_source.get("reason") or ""),
        "sample_items": list(validated_source.get("sample_items") or []),
        "dedupe": dict(validated_source.get("dedupe") or {}),
        "risks": list(validated_source.get("risks") or []),
        "suggested_source": source,
        "source_key": source.get("key"),
        "suggested_next_operation": "radar.add_validated_source"
        if validated_source.get("valid")
        else "radar.discover_sources",
    }
    return proposal

