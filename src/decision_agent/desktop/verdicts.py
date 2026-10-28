"""Line verdicts and impact selections stored in the state database, bound to one topic.

A verdict is reused only when everything that could change it is the same: the topic's
decision state, the line itself, the model that judges it, the sampling settings, and the exact
prompt (which carries the effective decision, its due date and any user answers). Verdicts made
by the built-in demo judge are never handed to a real model run.
"""

from __future__ import annotations

import hashlib
import json
import threading

from decision_agent.config import Settings
from decision_agent.consistency import PreparedCheck
from decision_agent.desktop.state import StateDB
from decision_agent.models import Artifact, ConsistencyLabel, ConsistencyResult

# Core settings that change how a line's samples are taken and summarized.
SAMPLING_FIELDS = (
    "consistent_single", "adaptive_min_samples", "prefilter", "routing", "n", "tau_conf",
)


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def artifact_fingerprint(artifact: Artifact) -> str:
    """id|kind|text|scope|metadata of one line."""
    return (
        f"{artifact.id}|{artifact.kind.value}|{artifact.text}|{artifact.scope.model_dump_json()}|"
        f"{json.dumps(artifact.metadata, sort_keys=True, ensure_ascii=False)}"
    )


def sampling_key(core: Settings) -> str:
    return json.dumps({name: getattr(core, name) for name in SAMPLING_FIELDS}, sort_keys=True)


class TopicVerdicts:
    """VerdictStore for one topic in one cycle.

    origin is "model" for a real model and "demo" for the built-in demo judge. A demo verdict
    is reused only by another demo run, never presented as a model verdict.
    """

    def __init__(
        self,
        db: StateDB,
        decision_fp: str,
        judge_model: str,
        select_model: str,
        sampling: str,
        origin: str,
        stamp: str,
    ) -> None:
        self.db = db
        self.decision_fp = decision_fp
        self.judge_model = judge_model
        self.select_model = select_model
        self.sampling = sampling
        self.origin = origin
        self.stamp = stamp
        # Keys written and reused in this cycle. A topic that is run twice (once more without
        # questions) reads its own fresh verdicts again; those are not reuse of earlier results.
        self.written: set[str] = set()
        self.reused: set[str] = set()
        self._lock = threading.Lock()

    @property
    def judged_lines(self) -> int:
        return len(self.written)

    @property
    def reused_lines(self) -> int:
        return len(self.reused - self.written)

    def line_key(self, artifact: Artifact) -> str:
        return _sha("line", self.decision_fp, artifact_fingerprint(artifact), self.judge_model, self.sampling)

    def _verdict_key(self, prepared: PreparedCheck) -> str:
        return _sha(self.line_key(prepared.artifact), prepared.user)

    def _selection_key(self, artifact: Artifact) -> str:
        return _sha("select", self.decision_fp, artifact_fingerprint(artifact), self.select_model)

    def _reusable(self, origin: str) -> bool:
        return origin == self.origin

    def get_verdicts(self, items: list[PreparedCheck]) -> list[ConsistencyResult | None]:
        keys = {i: self._verdict_key(item) for i, item in enumerate(items) if item.early is None}
        rows = self.db.verdicts_get(list(keys.values()), self.stamp)
        results: list[ConsistencyResult | None] = []
        for index, item in enumerate(items):
            row = rows.get(keys[index]) if index in keys else None
            if row is None or not self._reusable(row["origin"]):
                results.append(None)
                continue
            with self._lock:
                self.reused.add(keys[index])
            results.append(
                ConsistencyResult(
                    artifact_id=item.artifact.id,
                    label=ConsistencyLabel(row["label"]),
                    confidence=row["certainty"],
                    evidence=row["evidence"],
                    expected_action=row["expected_action"],
                    effective_decision_id=row["decision_id"],
                )
            )
        return results

    def put_verdicts(self, items: list[tuple[PreparedCheck, ConsistencyResult]]) -> None:
        rows = [
            {
                "key": self._verdict_key(prepared),
                "line_key": self.line_key(prepared.artifact),
                "label": result.label.value,
                "evidence": result.evidence,
                "expected_action": result.expected_action,
                "certainty": result.confidence,
                "decision_id": result.effective_decision_id,
                "origin": self.origin,
            }
            for prepared, result in items
            if prepared.early is None
        ]
        if rows:
            self.db.verdicts_put(rows, self.stamp)
            with self._lock:
                self.written.update(r["key"] for r in rows)

    def get_selections(self, candidates: list[Artifact]) -> dict[str, bool]:
        keys = {a.id: self._selection_key(a) for a in candidates}
        found = self.db.selections_get(list(keys.values()), self.stamp)
        return {artifact_id: found[key] for artifact_id, key in keys.items() if key in found}

    def put_selections(self, candidates: list[Artifact], selected: set[str]) -> None:
        self.db.selections_put(
            {self._selection_key(a): a.id in selected for a in candidates}, self.stamp
        )

    def missing_lines(self, candidates: list[Artifact]) -> int:
        """Candidates without any stored verdict of this origin (used for the call estimate)."""
        keys = [self.line_key(a) for a in candidates]
        known = self.db.verdict_lines_known(keys, (self.origin,))
        return sum(1 for key in keys if key not in known)
