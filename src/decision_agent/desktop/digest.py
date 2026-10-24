from __future__ import annotations

import datetime
from pathlib import Path

from decision_agent.desktop.paths import app_home

LABELS = {"INCONSISTENT": "불일치", "UNCERTAIN": "불확실"}


def build_digest(status: dict, findings: list[dict], questions: list[dict], now: datetime.datetime) -> dict:
    """Plain-text and markdown summary of what needs attention. Facts from the dashboard only."""
    overall = status["overall"]
    bad = [f for f in findings if f["label"] == "INCONSISTENT"]
    unsure = [f for f in findings if f["label"] == "UNCERTAIN"]
    lines = [f"# 결정 변경 점검 요약 ({now.strftime('%Y-%m-%d %H:%M')})", ""]
    lines.append(f"- 점검 상태: {overall['label']} {overall['detail']}".rstrip())
    lines.append(f"- 불일치 {len(bad)}건, 불확실 {len(unsure)}건, 답변이 필요한 질문 {len(questions)}건")
    if status["provider"]["local_only"]:
        lines.append("- 주의: 데모 판정기라 이 요약은 실제 검사 결과가 아닙니다.")
    if bad:
        lines += ["", "## 반영되지 않은 것 같은 항목"]
        for item in bad[:15]:
            owner = f" / 담당 {item['owner']}" if item.get("owner") else ""
            lines.append(f"- {item['text']} ({item['source_doc']}{owner}) - 결정: {item['topic_label']}")
    if questions:
        lines += ["", "## 확인이 필요한 질문"]
        lines += [f"- {q['topic_label']}: {q['text']}" for q in questions[:10]]
    short = (
        f"불일치 {len(bad)}건, 불확실 {len(unsure)}건, 질문 {len(questions)}건"
        + (" (데모 판정기)" if status["provider"]["local_only"] else "")
    )
    return {"title": "오늘의 결정 점검 요약", "short": short, "markdown": "\n".join(lines) + "\n"}


def save_digest(markdown: str, now: datetime.datetime) -> Path:
    folder = app_home() / "digests"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.date().isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def is_due(now: datetime.datetime, digest_time: str, last_date: str | None) -> bool:
    hour, minute = (int(x) for x in digest_time.split(":"))
    return now.time() >= datetime.time(hour, minute) and last_date != now.date().isoformat()
