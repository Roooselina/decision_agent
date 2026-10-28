"""What one mail source reads, and the local checks applied after a fetch.

A server filters what it can (Gmail labels and search, IMAP SEARCH, Outlook folders). MailFilter
applies the rest locally with the same rules for every provider, so a source reads the same
mails whatever the server supports.
"""

from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass, field

from decision_agent.connectors.base import MailMessage, clean_message_text, mail_document
from decision_agent.sources.model import ParsedDocument

# A decision mail usually says that something was decided, changed, cancelled or moved.
# Purpose "decision" sends a mail to extraction only when a line carries one of these.
DECISION_MARKERS = (
    "결정", "확정", "변경", "하기로", "취소", "연기", "제외", "추가", "합의", "정했", "바꾸", "되돌",
)
# "A -> B" style replacements (ASCII arrows and the Unicode right arrow).
_ARROW = re.compile(r"\S\s*(?:->|=>|→)\s*\S")
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+\S")

PURPOSE_KIND = {
    "decision": "MEETING",
    "task": "TASK",
    "requirement": "REQUIREMENT",
    "schedule": "SCHEDULE",
}


@dataclass
class MailQuery:
    """One source as the connectors see it. labels are Gmail labels or IMAP/Outlook folders."""

    source_id: str
    name: str
    purpose: str = "decision"
    team: str = ""
    labels: list[str] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    subject_keywords: list[str] = field(default_factory=list)
    exclude_senders: list[str] = field(default_factory=list)
    exclude_subject_keywords: list[str] = field(default_factory=list)
    gmail_query: str = ""
    days_back: int = 90
    max_messages: int = 300
    # Body requests in flight at once (bounded by the app's parallel call setting).
    workers: int = 4
    # Apply the decision-marker triage for purpose "decision".
    prefilter: bool = True
    # How the provider calls labels in status sentences (Gmail labels, IMAP/Outlook folders).
    folder_word: str = "라벨"

    @property
    def kind(self) -> str:
        return PURPOSE_KIND.get(self.purpose, "MEETING")

    def scope_text(self) -> str:
        """Short description of the filter for status sentences (no addresses)."""
        parts = []
        if self.labels:
            parts.append(f"{self.folder_word}: " + ", ".join(self.labels))
        if self.senders:
            parts.append(f"보낸 사람 {len(self.senders)}개")
        if self.subject_keywords:
            parts.append("제목: " + ", ".join(self.subject_keywords))
        if self.gmail_query:
            parts.append("검색식")
        parts.append(f"최근 {self.days_back}일")
        return ", ".join(parts)


def address_of(sender: str) -> str:
    return email.utils.parseaddr(sender)[1].strip().casefold()


def sender_matches(sender: str, patterns: list[str]) -> bool:
    """kim@x.com matches that address; @x.com and x.com match the domain and its subdomains."""
    address = address_of(sender)
    if not address:
        return False
    domain = address.rsplit("@", 1)[-1]
    for raw in patterns:
        pattern = raw.strip().casefold()
        if not pattern:
            continue
        if "@" in pattern and not pattern.startswith("@"):
            if address == pattern:
                return True
            continue
        wanted = pattern.lstrip("@")
        if domain == wanted or domain.endswith("." + wanted):
            return True
    return False


def subject_matches(subject: str, keywords: list[str]) -> bool:
    lowered = subject.casefold()
    return any(k.strip() and k.strip().casefold() in lowered for k in keywords)


@dataclass
class MailFilter:
    senders: list[str] = field(default_factory=list)
    subject_keywords: list[str] = field(default_factory=list)
    exclude_senders: list[str] = field(default_factory=list)
    exclude_subject_keywords: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, query: MailQuery) -> MailFilter:
        return cls(
            list(query.senders), list(query.subject_keywords),
            list(query.exclude_senders), list(query.exclude_subject_keywords),
        )

    def matches(self, message: MailMessage) -> bool:
        if self.senders and not sender_matches(message.sender, self.senders):
            return False
        if self.subject_keywords and not subject_matches(message.subject, self.subject_keywords):
            return False
        if self.exclude_senders and sender_matches(message.sender, self.exclude_senders):
            return False
        return not (
            self.exclude_subject_keywords
            and subject_matches(message.subject, self.exclude_subject_keywords)
        )


def looks_like_decision(lines: list[str]) -> bool:
    """True when a line carries a decision marker or an A -> B change, or the mail has bullets."""
    for line in lines:
        if any(marker in line for marker in DECISION_MARKERS) or _ARROW.search(line):
            return True
    return any(_BULLET.match(line) for line in lines)


def _quoted(term: str) -> str:
    term = term.replace('"', " ").strip()
    return f'"{term}"' if re.search(r"\s", term) else term


def _any_of(field_name: str, terms: list[str]) -> str:
    cleaned = [_quoted(t) for t in terms if t.strip()]
    if not cleaned:
        return ""
    return f"{field_name}:({' OR '.join(cleaned)})"


def gmail_search(query: MailQuery) -> str:
    """The Gmail search string for everything except labels (those go in labelIds)."""
    parts = [f"newer_than:{query.days_back}d"]
    for name, terms in (("from", query.senders), ("subject", query.subject_keywords)):
        found = _any_of(name, [t.lstrip("@") if name == "from" else t for t in terms])
        if found:
            parts.append(found)
    for name, terms in (("from", query.exclude_senders), ("subject", query.exclude_subject_keywords)):
        found = _any_of(name, [t.lstrip("@") if name == "from" else t for t in terms])
        if found:
            parts.append("-" + found)
    if query.gmail_query.strip():
        parts.append(f"({query.gmail_query.strip()})")
    return " ".join(parts)


def collect_mail(
    messages: list[MailMessage], query: MailQuery, stats: dict[str, int]
) -> dict[str, ParsedDocument]:
    """Documents for the fetched mails after the local filter and the decision triage."""
    rules = MailFilter.of(query)
    docs: dict[str, ParsedDocument] = {}
    for message in messages:
        if not rules.matches(message):
            stats["skipped_filtered"] = stats.get("skipped_filtered", 0) + 1
            continue
        if query.prefilter and query.purpose == "decision":
            lines = clean_message_text(message.body) or [message.subject]
            if not looks_like_decision(lines):
                stats["skipped_prefilter"] = stats.get("skipped_prefilter", 0) + 1
                continue
        made = mail_document(message, stats, query.kind)
        if made is None:
            continue
        name, doc = made
        doc.meta["source_id"] = query.source_id
        doc.meta["source_name"] = query.name
        if query.team:
            doc.meta["team"] = query.team
        docs[name] = doc
    return docs
