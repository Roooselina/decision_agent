"""Local decomposition of batch prompts, used by providers that answer from rules or gold data.

Real models answer a batch prompt directly. Test and demo providers implement only the single
item tasks, so they split the batch, answer each item and join the answers in the batch format.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

CONSISTENCY = "TASK:CONSISTENCY_BATCH"
EXTRACT = "TASK:DECISION_EXTRACT_BATCH"

_CONSISTENCY_SINGLE = "TASK:CONSISTENCY\n"
_EXTRACT_SINGLE = "TASK:DECISION_EXTRACT\n"

_ITEM = re.compile(
    r"^\[(\d+)\] ID=(.*?) \| KIND=(.*?) \| ARTIFACT=(.*) \| ARTIFACT_DATE=(.*?) \| ARTIFACT_STATUS=(.*?)"
    r" \| ARTIFACT_SCOPE=(\{.*\})$"
)
_EXTRACT_ITEM = re.compile(r"^\[(\d+)\] DATE=(.*?) \| TEXT=(.*)$")


def is_batch(system: str) -> bool:
    return CONSISTENCY in system or EXTRACT in system


def answer_batch(
    single: Callable[[str, str, float, dict | None], str],
    system: str,
    user: str,
    temperature: float,
) -> str:
    if EXTRACT in system:
        return _extract(single, user)
    return _consistency(single, user, temperature)


def _consistency(single, user: str, temperature: float) -> str:
    head, _, rest = user.partition("\nARTIFACTS:\n")
    results = []
    for line in rest.splitlines():
        match = _ITEM.match(line)
        if not match:
            continue
        index, artifact_id, kind, text, date, status, scope = match.groups()
        one = (
            f"{head}\nARTIFACT_ID={artifact_id}\nARTIFACT_KIND={kind}\nARTIFACT={text}\n"
            f"ARTIFACT_DATE={date}\nARTIFACT_STATUS={status}\nARTIFACT_SCOPE={scope}\n"
        )
        # keep the EVIDENCE block at the end, as the single-item prompt does
        if "EVIDENCE:" in head:
            before, evidence = head.split("EVIDENCE:", 1)
            one = (
                f"{before}ARTIFACT_ID={artifact_id}\nARTIFACT_KIND={kind}\nARTIFACT={text}\n"
                f"ARTIFACT_DATE={date}\nARTIFACT_STATUS={status}\nARTIFACT_SCOPE={scope}\nEVIDENCE:{evidence}"
            )
        raw = single(_CONSISTENCY_SINGLE, one, temperature, None)
        payload = json.loads(raw)
        results.append({"index": int(index), **payload})
    return json.dumps({"results": results}, ensure_ascii=False)


def _extract(single, user: str) -> str:
    head, _, rest = user.partition("\nITEMS:\n")
    items = []
    for line in rest.splitlines():
        match = _EXTRACT_ITEM.match(line)
        if not match:
            continue
        index, date, text = match.groups()
        one = f"DATE={date}\n{head}\nTEXT={text}"
        raw = single(_EXTRACT_SINGLE, one, 0.0, None)
        items.append({"index": int(index), **json.loads(raw)})
    return json.dumps({"items": items}, ensure_ascii=False)
