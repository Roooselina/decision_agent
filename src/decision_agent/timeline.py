from __future__ import annotations

from decision_agent.storage import SQLiteStore


def build(store: SQLiteStore, topic: str) -> tuple[str, list[dict]]:
    decisions = store.list_decisions(topic)
    rels = store.list_relations(topic)
    rel_by_new = {r.new_id: r for r in rels}
    rows = []
    lines = []
    for d in decisions:
        rel = rel_by_new.get(d.id)
        note = ""
        if (
            rel
            and rel.relation_type.value in {"SCOPE_RESTRICT", "SUPERSEDE", "CANCEL"}
            and rel.affected_scope.scope_kind.value == "SCOPED"
        ):
            note = "기존 결정 전체가 폐기된 것이 아니라 해당 범위에서만 변경됨"
        row = {
            "date": d.date,
            "decision": d.text,
            "scope": d.scope.model_dump(mode="json"),
            "relation": rel.relation_type.value if rel else None,
            "reason": d.reason,
            "note": note,
        }
        rows.append(row)
        lines.append(
            f"{d.date} | {d.text} | scope={d.scope.scope_kind.value} | relation={row['relation'] or '-'} {note}".strip()
        )
    return "\n".join(lines), rows
