"""Dates and deadlines mentioned in mail, found with local patterns. No model is called.

These are hints for the mail view and the calendar, not facts: they never feed findings,
fingerprints or a model prompt. A day-only date ("27일까지") is resolved against the mail's
date: the nearest date on or after it with that day of the month.
"""

from __future__ import annotations

import datetime
import re

DEADLINE_WORDS = ("까지", "마감", "기한", "제출", "due", "deadline")
MAX_HINTS = 10

# Sentences end at ! or ?, or at a period followed by a space and a non-digit, so dotted dates
# such as 26.11.4. stay in one piece.
_SENTENCE_END = re.compile(r"(?<=[!?])\s+|(?<=\.)\s+(?=[^\d\s])")
_FULL = re.compile(r"(?<!\d)(20\d{2}|\d{2})[.\-/]\s?(\d{1,2})[.\-/]\s?(\d{1,2})\.?(?!\d)")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SLASH = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])")
_DAY_ONLY = re.compile(r"(?<![\d월/.])(\d{1,2})\s*일\s*(?:\(\S{1,3}\)\s*)?(까지|마감|기한|에\s*제출|제출)")
_TILDE = re.compile(r"~\s*(\d{1,2})\s*일")


def _date(year: int, month: int, day: int) -> datetime.date | None:
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _following_month_day(month: int, day: int, base: datetime.date) -> datetime.date | None:
    """The month and day in the mail's year, or the next year when it lies well before the mail."""
    found = _date(base.year, month, day)
    if found is not None and found < base - datetime.timedelta(days=60):
        found = _date(base.year + 1, month, day)
    return found


def _following_day(day: int, base: datetime.date) -> datetime.date | None:
    """The nearest date on or after the mail date whose day of the month is `day`."""
    year, month = base.year, base.month
    for _ in range(3):
        found = _date(year, month, day)
        if found is not None and found >= base:
            return found
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return None


def _is_deadline(sentence: str) -> bool:
    lowered = sentence.casefold()
    return any(word in lowered for word in DEADLINE_WORDS)


def extract_hints(subject: str, body: str, mail_date: str) -> list[dict]:
    """(date, phrase, sentence, kind) for each date in the subject and body.

    kind is "deadline" when the sentence says 까지, 마감, 기한, 제출, due or deadline, else "date".
    A sentence without a date gives no hint.
    """
    try:
        base = datetime.date.fromisoformat(mail_date[:10])
    except ValueError:
        return []
    hints: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for block in [subject, *body.splitlines()]:
        for part in _SENTENCE_END.split(block):
            sentence = " ".join(part.split())
            if not sentence:
                continue
            for found, phrase in _dates_in(sentence, base):
                key = (found.isoformat(), sentence)
                if key in seen:
                    continue
                seen.add(key)
                hints.append({
                    "date": found.isoformat(), "phrase": phrase, "sentence": sentence[:200],
                    "kind": "deadline" if _is_deadline(sentence) else "date",
                })
                if len(hints) >= MAX_HINTS:
                    return hints
    return hints


def _dates_in(sentence: str, base: datetime.date) -> list[tuple[datetime.date, str]]:
    found: list[tuple[datetime.date, str]] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for match in _FULL.finditer(sentence):
        year = int(match.group(1))
        year = year + 2000 if year < 100 else year
        day = _date(year, int(match.group(2)), int(match.group(3)))
        if day is not None:
            found.append((day, match.group(0).strip()))
            taken.append(match.span())
    for match in _MONTH_DAY.finditer(sentence):
        if not free(match.span()):
            continue
        day = _following_month_day(int(match.group(1)), int(match.group(2)), base)
        if day is not None:
            end = match.end()
            tail = re.match(r"\s*(까지|마감|기한)", sentence[end:])
            phrase = sentence[match.start(): end + (tail.end() if tail else 0)]
            found.append((day, phrase.strip()))
            taken.append(match.span())
    for match in _SLASH.finditer(sentence):
        if not free(match.span()):
            continue
        day = _following_month_day(int(match.group(1)), int(match.group(2)), base)
        if day is not None:
            found.append((day, match.group(0)))
            taken.append(match.span())
    for pattern in (_DAY_ONLY, _TILDE):
        for match in pattern.finditer(sentence):
            if not free(match.span()):
                continue
            day = _following_day(int(match.group(1)), base)
            if day is not None:
                found.append((day, match.group(0).strip()))
                taken.append(match.span())
    return found
