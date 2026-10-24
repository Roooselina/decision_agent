from __future__ import annotations

import datetime
import re

_YMD = re.compile(r"(?<!\d)(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)")
_KOREAN_YMD = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_KOREAN_MD = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_COMPACT = re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)")


def _valid(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_date(text: str, default_year: int | None = None) -> str | None:
    """Return the first valid date in text as YYYY-MM-DD, or None.

    Supported: 2026-09-01, 2026.9.1, 2026/09/01, 2026년 9월 1일, 20260901, and
    9월 1일 when default_year is given.
    """
    for pattern in (_YMD, _KOREAN_YMD, _COMPACT):
        for match in pattern.finditer(text):
            found = _valid(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if found:
                return found
    if default_year is not None:
        for match in _KOREAN_MD.finditer(text):
            found = _valid(default_year, int(match.group(1)), int(match.group(2)))
            if found:
                return found
    return None


def contains_date(text: str, default_year: int | None = None) -> bool:
    return parse_date(text, default_year) is not None
