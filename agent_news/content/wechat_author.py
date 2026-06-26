"""WeChat author field length helpers."""

from __future__ import annotations

MAX_AUTHOR_UNITS = 8
MAX_AUTHOR_UNITS_TWICE = MAX_AUTHOR_UNITS * 2


def author_units_twice(value: str) -> int:
    """Return WeChat author width in half-units.

    WeChat counts common half-width characters such as ASCII letters, digits,
    spaces, and punctuation as half a Chinese character.
    """
    return sum(1 if char.isascii() else 2 for char in str(value or "").strip())


def author_units_label(value: str) -> str:
    units_twice = author_units_twice(value)
    whole, half = divmod(units_twice, 2)
    return f"{whole}.5" if half else str(whole)


def is_author_within_limit(value: str) -> bool:
    return author_units_twice(value) <= MAX_AUTHOR_UNITS_TWICE


def truncate_author_to_limit(value: str) -> str:
    result = []
    used = 0
    for char in str(value or "").strip():
        width = 1 if char.isascii() else 2
        if used + width > MAX_AUTHOR_UNITS_TWICE:
            break
        result.append(char)
        used += width
    return "".join(result)
