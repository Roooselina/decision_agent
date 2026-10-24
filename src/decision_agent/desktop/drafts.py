from __future__ import annotations

import json

from pydantic import BaseModel

from decision_agent.llm.base import LLMProvider

DRAFT_SYSTEM = """TASK:FIX_DRAFT
너는 팀 문서 관리자를 돕는다. 최신 결정(DECISION)과 맞지 않는다고 판정된 항목(ITEM)이 있다.
항목을 최신 결정에 맞게 고친 문장 하나를 제안한다.

규칙:
1. 항목의 원래 말투와 형식을 유지하고, 결정과 상관없는 내용은 바꾸지 않는다.
2. 근거에 없는 날짜, 이름, 숫자를 만들지 않는다. 모르면 원래 표현을 그대로 둔다.
3. 항목을 삭제해야 한다면 revised_text에 "(삭제 제안)"과 이유를 쓴다.

출력 JSON: {"revised_text": "고친 문장", "note": "무엇을 왜 바꿨는지 한 문장"}
"""


class DraftPayload(BaseModel):
    revised_text: str
    note: str = ""


def owner_message(finding: dict, decision: str) -> str:
    """A short message to the person in charge. Built from facts only, never by a model."""
    owner = finding.get("owner") or ""
    greeting = f"{owner}님, " if owner else ""
    place = finding.get("source_doc", "")
    if finding.get("location"):
        place += f" ({finding['location']})"
    return (
        f"{greeting}'{decision}' 결정이 있었는데, {place}의 "
        f"'{finding.get('text', '')}' 항목이 아직 반영되지 않은 것 같아요. "
        f"확인 후 필요하면 고쳐 주세요. ({finding.get('action', '').strip()})"
    ).strip()


def make_draft(
    provider: LLMProvider | None, finding: dict, decision: str
) -> dict:
    """Suggest a fix for an inconsistent item. Nothing is written anywhere.

    With the demo judge only the owner message is produced, because a rule judge cannot rewrite
    sentences. With a real model a revised sentence is added.
    """
    result = {
        "revised_text": "",
        "note": "",
        "message": owner_message(finding, decision),
        "decision": decision,
        "model_used": False,
    }
    if provider is None or finding.get("label") != "INCONSISTENT":
        return result
    user = (
        f"DECISION={decision}\nITEM={finding.get('text', '')}\n"
        f"ITEM_SOURCE={finding.get('source_doc', '')}\nEVIDENCE={finding.get('evidence', '')}"
    )
    payload = provider.complete_json(DRAFT_SYSTEM, user, DraftPayload, 0.0)
    result["revised_text"] = payload.revised_text.strip()
    result["note"] = payload.note.strip()
    result["model_used"] = True
    return result


def latest_decision(summary_json: str) -> str:
    try:
        return str(json.loads(summary_json).get("latest", ""))
    except (ValueError, TypeError, AttributeError):
        return ""
