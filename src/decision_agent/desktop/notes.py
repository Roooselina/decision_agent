from __future__ import annotations

import datetime
import json
import uuid

from decision_agent.desktop.state import StateDB
from decision_agent.sources.model import Block, ParsedDocument, Section

META_KEY = "notes"
MAX_NOTES = 500
MAX_LENGTH = 600


def list_notes(db: StateDB) -> list[dict]:
    raw = db.meta_get(META_KEY)
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


def add_note(db: StateDB, text: str, date: str | None = None) -> dict:
    """Store a decision typed in by the user, for example during a meeting."""
    text = " ".join(text.split())[:MAX_LENGTH]
    if len(text) < 2:
        raise ValueError("내용을 입력하세요.")
    day = date or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
    notes = list_notes(db)
    note = {"id": uuid.uuid4().hex[:10], "date": day, "text": text}
    notes.append(note)
    db.meta_put(META_KEY, json.dumps(notes[-MAX_NOTES:], ensure_ascii=False))
    return note


def delete_note(db: StateDB, note_id: str) -> bool:
    notes = list_notes(db)
    kept = [n for n in notes if n["id"] != note_id]
    db.meta_put(META_KEY, json.dumps(kept, ensure_ascii=False))
    return len(kept) != len(notes)


def notes_document(notes: list[dict]) -> ParsedDocument | None:
    """All notes as one meeting-style document: a dated heading and one bullet per note."""
    if not notes:
        return None
    section = Section(name="notes")
    for day in sorted({n["date"] for n in notes}):
        section.blocks.append(Block(text=day, kind="heading", level=2))
        section.blocks.extend(
            Block(text=n["text"], kind="bullet", location=n["id"]) for n in notes if n["date"] == day
        )
    doc = ParsedDocument(path="", fmt="connector", sections=[section])
    doc.meta["kind"] = "MEETING"
    return doc
