from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from decision_agent.agent.agent import run_agent
from decision_agent.agent.interfaces import NeedUserInput, ReplayUserInterface
from decision_agent.agent.pipeline import run_pipeline
from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.desktop.embeddings import OllamaEmbedder, make_extra_ranker
from decision_agent.desktop.policy import FolderPolicy
from decision_agent.desktop.profile import load_profile
from decision_agent.desktop.progress import ProgressTracker
from decision_agent.desktop.providers import (
    BudgetExceeded,
    CountingProvider,
    PersistentCachingProvider,
    ProviderBundle,
    describe_llm_error,
    is_loopback_url,
)
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import FindingInput, StateDB, sha256_file
from decision_agent.impact import candidate_artifacts_for_topic
from decision_agent.llm.caching import CachingProvider
from decision_agent.models import AgentTrace, Artifact, ConsistencyLabel
from decision_agent.sources.builder import BuildResult, build_inputs
from decision_agent.sources.model import ParsedDocument
from decision_agent.sources.readers import UnsupportedFormat, read_document
from decision_agent.storage import SQLiteStore
from decision_agent.workflow import ingest_items

log = logging.getLogger(__name__)

AS_OF = "9999-12-31"
EXTERNAL_PREFIX = "@"


@dataclass
class SyncResult:
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    retry: list[Path] = field(default_factory=list)


@dataclass
class TopicOutcome:
    topic: str
    state: str
    new_inconsistent: list[dict] = field(default_factory=list)
    inconsistent_total: int = 0
    pending_question: str | None = None
    new_question: bool = False


@dataclass
class CycleReport:
    root: str
    sync: SyncResult = field(default_factory=SyncResult)
    rebuilt: bool = False
    topics_checked: list[str] = field(default_factory=list)
    outcomes: list[TopicOutcome] = field(default_factory=list)
    needs_followup: bool = False
    error: str | None = None
    fatal: bool = False
    llm_calls: int = 0


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def short_label(text: str, limit: int = 30) -> str:
    """A decision sentence cut to fit one line of the activity list."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def decision_fingerprint(store: SQLiteStore, topic: str) -> str:
    """Content-based fingerprint of a topic's decisions and relations (ids are not included)."""
    decisions = store.list_decisions(topic)
    by_id = {d.id: d for d in decisions}
    parts: list[str] = []
    for d in decisions:
        parts.append(
            f"D|{d.date}|{d.text}|{d.scope.model_dump_json()}|{d.status.value}|{d.due_date}"
        )
    for r in store.list_relations(topic):
        old = by_id.get(r.old_id)
        new = by_id.get(r.new_id)
        parts.append(
            f"R|{old.text if old else r.old_id}|{new.text if new else r.new_id}|"
            f"{r.relation_type.value}|{r.affected_scope.model_dump_json()}"
        )
    return _sha(*parts)


def artifacts_fingerprint(artifacts: Iterable[Artifact]) -> str:
    parts = [
        f"{a.id}|{a.kind.value}|{a.text}|{a.scope.model_dump_json()}|"
        f"{json.dumps(a.metadata, sort_keys=True, ensure_ascii=False)}"
        for a in sorted(artifacts, key=lambda x: x.id)
    ]
    return _sha(*parts)


class ProjectEngine:
    """Incremental analysis for one watched folder.

    sync() updates the file index (parsing only changed files); analyze() rebuilds the decision
    store from all indexed documents and re-checks only topics whose fingerprint changed.
    LLM calls are cached by prompt, so rebuilding costs no new calls for unchanged text.
    """

    def __init__(
        self,
        root: Path,
        settings: AppSettings,
        db: StateDB,
        bundle: ProviderBundle,
        progress: ProgressTracker | None = None,
    ) -> None:
        self.progress = progress or ProgressTracker()
        self._skips: Counter[str] = Counter()
        self.root = root.resolve()
        self.settings = settings
        self.db = db
        self.bundle = bundle
        self.policy = FolderPolicy.for_root(self.root, settings)
        self.counter = CountingProvider(bundle.provider, settings.max_llm_calls_per_cycle)
        self.persistent = PersistentCachingProvider(self.counter, db, bundle.provider_id)
        self.lock = threading.RLock()
        self.extra_ranker = None
        if settings.embedding_model and (
            is_loopback_url(settings.ollama_url) or settings.allow_external_send
        ):
            embedder = OllamaEmbedder(settings.ollama_url, settings.embedding_model, db)
            self.extra_ranker = make_extra_ranker(embedder)
        self.last_build: BuildResult | None = None
        self.profile_notes: list[str] = []

    # file index

    def _key(self, path: Path) -> str | None:
        parts = self.policy.relative_parts(path)
        return "/".join(parts) if parts else None

    def _process_file(self, path: Path, key: str, result: SyncResult) -> None:
        verdict = self.policy.check_file(path)
        if verdict.status == "skip":
            self._skips[verdict.reason] += 1
            if self.db.get_file(key) is not None:
                self.db.delete_files([key])
                result.removed.append(key)
            return
        stat = path.stat()
        existing = self.db.get_file(key)
        if verdict.status == "unsupported":
            if existing is None or existing["status"] != "unsupported":
                self.db.upsert_file(
                    key, stat.st_mtime_ns, stat.st_size, "", path.suffix.lstrip(".").lower(),
                    "unsupported", verdict.reason,
                )
                result.changed.append(key)
            return
        if (
            existing is not None
            and existing["mtime_ns"] == stat.st_mtime_ns
            and existing["size"] == stat.st_size
        ):
            return
        try:
            sha = sha256_file(path)
            if existing is not None and existing["sha256"] == sha and existing["status"] == "ok":
                self.db.touch_file(key, stat.st_mtime_ns)
                return
            fmt = path.suffix.lstrip(".").lower()
            try:
                doc = read_document(path, self.root)
                doc.meta["mtime_date"] = datetime.date.fromtimestamp(stat.st_mtime).isoformat()
                self.db.upsert_file(
                    key, stat.st_mtime_ns, stat.st_size, sha, fmt, "ok", "", doc.model_dump_json()
                )
            except UnsupportedFormat as exc:
                self.db.upsert_file(
                    key, stat.st_mtime_ns, stat.st_size, sha, fmt, "unsupported", str(exc)
                )
            except PermissionError:
                raise
            except Exception as exc:  # noqa: BLE001 - one broken file must not stop the others
                log.warning("cannot read %s: %s", key, exc)
                self.db.upsert_file(
                    key, stat.st_mtime_ns, stat.st_size, sha, fmt, "error", str(exc)[:300]
                )
                result.errors.append((key, str(exc)[:300]))
            result.changed.append(key)
        except PermissionError:
            result.retry.append(path)

    def sync(self, paths: Iterable[Path] | None = None) -> SyncResult:
        """Update the index. paths=None scans the whole folder and detects deletions."""
        result = SyncResult()
        with self.lock:
            self.progress.set_phase("reading")
            if paths is None:
                seen: set[str] = set()
                self._skips = Counter()
                files = list(self.policy.iter_files())
                self.progress.set_files(len(files))
                for path in files:
                    key = self._key(path)
                    if key is not None:
                        seen.add(key)
                        self._process_file(path, key, result)
                    self.progress.file_done(key or "")
                stale = [
                    r["path"]
                    for r in self.db.list_files()
                    if r["path"] not in seen and not r["path"].startswith(EXTERNAL_PREFIX)
                ]
                if stale:
                    self.db.delete_files(stale)
                    result.removed.extend(stale)
                self.db.meta_put(
                    "scan_stats",
                    json.dumps(
                        {
                            "at": datetime.datetime.now().replace(microsecond=0).isoformat(),
                            "seen": len(files),
                            "skipped": dict(self._skips),
                        },
                        ensure_ascii=False,
                    ),
                )
                return result
            targets = [Path(p) for p in paths]
            self.progress.set_files(len(targets))
            for path in targets:
                self._sync_one(path, result)
                self.progress.file_done(path.name)
        return result

    def _sync_one(self, path: Path, result: SyncResult) -> None:
        key = self._key(path)
        if path.is_dir() and key is not None:
            prefix = key + "/"
            current: set[str] = set()
            for inner in self.policy.iter_files():
                inner_key = self._key(inner)
                if inner_key and inner_key.startswith(prefix):
                    current.add(inner_key)
                    self._process_file(inner, inner_key, result)
            stale = [
                r["path"] for r in self.db.list_files()
                if r["path"].startswith(prefix) and r["path"] not in current
            ]
            if stale:
                self.db.delete_files(stale)
                result.removed.extend(stale)
            return
        if key is None:
            # Missing path: derive the key without resolving, then drop it and anything below it.
            try:
                relative = path.absolute().relative_to(self.root).as_posix()
            except ValueError:
                return
            stale = [
                r["path"] for r in self.db.list_files()
                if r["path"] == relative or r["path"].startswith(relative + "/")
            ]
            if stale:
                self.db.delete_files(stale)
                result.removed.extend(stale)
            return
        if not path.exists():
            stale = [
                r["path"] for r in self.db.list_files()
                if r["path"] == key or r["path"].startswith(key + "/")
            ]
            if stale:
                self.db.delete_files(stale)
                result.removed.extend(stale)
            return
        if path.is_file():
            self._process_file(path, key, result)

    # analysis

    def _run_topic(
        self, topic: str, project, answers: list[str]
    ) -> tuple[AgentTrace, str | None]:
        provider = CachingProvider(self.persistent)
        ui = ReplayUserInterface(answers)
        mode = self.settings.analysis_mode
        try:
            provider.begin_run()
            if mode == "pipeline":
                trace = run_pipeline(
                    project.store, provider, topic, project.artifacts, AS_OF, clarify=True, ui=ui
                )
            else:
                trace = run_agent(
                    project.store, provider, topic, project.artifacts, AS_OF, ui=ui,
                    allow_clarify=True,
                )
            return trace, None
        except NeedUserInput as need:
            provider.begin_run()
            if mode == "pipeline":
                trace = run_pipeline(
                    project.store, provider, topic, project.artifacts, AS_OF, clarify=False
                )
            else:
                trace = run_agent(
                    project.store, provider, topic, project.artifacts, AS_OF, allow_clarify=False
                )
            return trace, need.question

    def _store_topic_results(
        self, topic: str, project, trace: AgentTrace, question: str | None
    ) -> TopicOutcome:
        by_id = {a.id: a for a in project.artifacts}
        items: list[FindingInput] = []
        for result in trace.final_results:
            artifact = by_id.get(result.artifact_id)
            if artifact is None:
                continue
            items.append(
                FindingInput(
                    artifact_id=artifact.id,
                    kind=artifact.kind.value,
                    text=artifact.text,
                    source_doc=artifact.source_doc,
                    location=artifact.location,
                    label=result.label.value,
                    confidence=result.confidence,
                    evidence=result.evidence,
                    action=result.expected_action,
                    decision_id=result.effective_decision_id,
                    owner=str(artifact.metadata.get("assignee", ""))[:60],
                )
            )
        changed = set(self.db.replace_findings(topic, items))
        total = sum(1 for i in items if i.label == ConsistencyLabel.INCONSISTENT.value)
        new_items = [
            {
                "artifact_id": i.artifact_id, "text": i.text, "source_doc": i.source_doc,
                "evidence": i.evidence, "action": i.action, "owner": i.owner,
            }
            for i in items
            if i.label == ConsistencyLabel.INCONSISTENT.value and i.artifact_id in changed
        ]
        new_question = False
        if question:
            new_question = self.db.set_question(topic, question)
        else:
            self.db.clear_question(topic)
        return TopicOutcome(
            topic=topic,
            state="waiting_answer" if question else "ok",
            new_inconsistent=new_items,
            inconsistent_total=total,
            pending_question=question,
            new_question=new_question,
        )

    # external sources (connectors, quick notes)

    def set_external(self, source: str, docs: dict[str, ParsedDocument]) -> bool:
        """Replace every document that one external source delivered. Returns True on change.

        These documents live only in the state database (path prefix @source/), never in the
        watched folder, and are removed from the analysis when the source stops delivering them.
        """
        prefix = f"{EXTERNAL_PREFIX}{source}/"
        changed = False
        with self.lock:
            existing = {r["path"]: r for r in self.db.list_files() if r["path"].startswith(prefix)}
            for name, doc in docs.items():
                path = prefix + name
                doc.path = path
                payload = doc.model_dump_json()
                digest = _sha(payload)
                old = existing.pop(path, None)
                if old is not None and old["sha256"] == digest and old["status"] == "ok":
                    continue
                self.db.upsert_file(
                    path, 0, len(payload), digest, "connector", "ok", "", payload
                )
                changed = True
            if existing:
                self.db.delete_files(list(existing))
                changed = True
        return changed

    def put_external_doc(self, source: str, name: str, doc: ParsedDocument) -> None:
        """Add or replace one external document without touching the source's other documents."""
        path = f"{EXTERNAL_PREFIX}{source}/{name}"
        doc.path = path
        payload = doc.model_dump_json()
        with self.lock:
            self.db.upsert_file(path, 0, len(payload), _sha(payload), "connector", "ok", "", payload)

    def remove_external_doc(self, source: str, name: str) -> bool:
        with self.lock:
            return self.db.delete_files([f"{EXTERNAL_PREFIX}{source}/{name}"]) > 0

    # analysis

    def _start_if_idle(self) -> None:
        if self.progress.phase in {"idle", "done", "error"}:
            self.progress.begin()

    def _record_completion(self, topics_total: int, report: CycleReport) -> None:
        counts = Counter(r["status"] for r in self.db.list_files())
        judges = Counter(r["judge"] or self.bundle.provider_id for r in self.db.list_topics())
        record = {
            "at": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "provider_id": self.bundle.provider_id,
            "local_only": self.bundle.local_only,
            "verified": not self.bundle.local_only,
            "mode": self.settings.analysis_mode,
            "files_ok": counts.get("ok", 0),
            "files_error": counts.get("error", 0),
            "files_unsupported": counts.get("unsupported", 0),
            "topics": topics_total,
            "topics_rechecked": len(report.topics_checked),
            "judges": dict(judges),
            "llm_calls": report.llm_calls,
        }
        self.db.meta_put("completion", json.dumps(record, ensure_ascii=False))

    def completion(self) -> dict | None:
        raw = self.db.meta_get("completion")
        return json.loads(raw) if raw else None

    def last_failure(self) -> dict | None:
        raw = self.db.meta_get("last_failure")
        return json.loads(raw) if raw else None

    def analyze(
        self, force: bool = False, force_topics: set[str] | None = None
    ) -> CycleReport:
        self._start_if_idle()
        try:
            report = self._analyze(force, force_topics)
        except Exception:
            self.progress.set_phase("error")
            raise
        self.progress.set_llm_calls(report.llm_calls)
        if report.error:
            self.db.meta_put(
                "last_failure",
                json.dumps(
                    {
                        "at": datetime.datetime.now().replace(microsecond=0).isoformat(),
                        "message": report.error,
                        "fatal": report.fatal,
                    },
                    ensure_ascii=False,
                ),
            )
            self.progress.activity.add(report.error, "error")
            self.progress.set_phase("error")
        else:
            self.db.meta_delete("last_failure")
            self.progress.set_phase("done")
        return report

    def _fail(self, report: CycleReport, exc: Exception, prefix: str) -> None:
        message, fatal = describe_llm_error(exc)
        report.error = f"{prefix}{message}"
        report.fatal = fatal
        report.needs_followup = not fatal
        report.llm_calls = self.counter.calls

    def _analyze(
        self, force: bool = False, force_topics: set[str] | None = None
    ) -> CycleReport:
        report = CycleReport(root=str(self.root))
        force_topics = force_topics or set()
        with self.lock:
            self.counter.reset()
            self.progress.set_phase("extracting")
            rows = [r for r in self.db.list_files() if r["status"] == "ok"]
            docs = [ParsedDocument.model_validate_json(r["payload"]) for r in rows]
            profile = load_profile(self.root, self.settings)
            self.profile_notes = profile.notes
            built = build_inputs(docs, profile.config, profile.aliases, profile.overrides)
            self.last_build = built
            input_fp = _sha(
                self.bundle.provider_id,
                profile.fingerprint,
                self.settings.analysis_mode,
                self.settings.embedding_model,
                json.dumps([m.model_dump() for m in built.meetings], ensure_ascii=False),
                artifacts_fingerprint(built.artifacts),
            )
            if not force and not force_topics and self.db.meta_get("input_fp") == input_fp:
                if self.db.meta_get("completion") is None:
                    self._record_completion(len(self.db.list_topics()), report)
                return report
            report.rebuilt = True
            self.progress.activity.add(
                f"문서 {len(docs)}개를 확인했습니다. 회의 결정 {len(built.meetings)}건, "
                f"관련 항목 {len(built.artifacts)}건을 찾았습니다."
            )
            try:
                project = ingest_items(
                    built.meetings, built.artifacts, profile.config, profile.aliases, self.persistent
                )
            except BudgetExceeded as exc:
                report.error = str(exc)
                report.needs_followup = True
                report.llm_calls = self.counter.calls
                return report
            except Exception as exc:
                log.exception("ingest failed")
                self._fail(report, exc, "결정 추출에 실패했습니다. ")
                return report

            topics = sorted({d.topic for d in project.decisions})
            for stored in self.db.list_topics():
                if stored["topic"] not in topics:
                    self.db.drop_topic(stored["topic"])
            self.progress.set_topics(len(topics))
            self.progress.set_phase("checking")
            all_done = True
            for topic in topics:
                decisions, candidates = candidate_artifacts_for_topic(
                    project.store, topic, project.artifacts, AS_OF, DEFAULT_SETTINGS.k,
                    extra_ranker=self.extra_ranker,
                )
                decision_fp = decision_fingerprint(project.store, topic)
                fingerprint = _sha(
                    decision_fp,
                    artifacts_fingerprint(candidates),
                    self.bundle.provider_id,
                    self.settings.analysis_mode,
                )
                previous = self.db.topic_get(topic)
                self.db.clear_stale_answers(topic, decision_fp)
                answers = self.db.get_answers(topic, decision_fp)
                unchanged = (
                    previous is not None
                    and previous["fingerprint"] == fingerprint
                    and previous["state"] in {"ok", "waiting_answer"}
                )
                if unchanged and topic not in force_topics and not force:
                    self.progress.topic_done(topic, unchanged=True)
                    continue
                latest = decisions[-1] if decisions else None
                summary = json.dumps(
                    {"latest": latest.text if latest else "", "date": latest.date if latest else ""},
                    ensure_ascii=False,
                )
                judge = self.bundle.provider_id
                if not candidates:
                    self.db.replace_findings(topic, [])
                    self.db.clear_question(topic)
                    self.db.topic_put(topic, fingerprint, decision_fp, "ok", summary, judge)
                    report.topics_checked.append(topic)
                    self.progress.topic_done(topic)
                    continue
                try:
                    trace, question = self._run_topic(topic, project, answers)
                except BudgetExceeded as exc:
                    self.db.topic_put(topic, "", decision_fp, "deferred", summary, judge)
                    report.error = str(exc)
                    report.needs_followup = True
                    report.outcomes.append(TopicOutcome(topic=topic, state="deferred"))
                    all_done = False
                    break
                except Exception as exc:
                    log.exception("topic %s failed", topic)
                    self.db.topic_put(topic, "", decision_fp, "error", summary, judge)
                    self._fail(report, exc, f"'{topic}' 점검에 실패했습니다. ")
                    report.outcomes.append(TopicOutcome(topic=topic, state="error"))
                    all_done = False
                    if report.fatal:
                        break
                    continue
                outcome = self._store_topic_results(topic, project, trace, question)
                self.db.topic_put(topic, fingerprint, decision_fp, outcome.state, summary, judge)
                report.topics_checked.append(topic)
                report.outcomes.append(outcome)
                self.progress.set_llm_calls(self.counter.calls)
                self.progress.topic_done(topic)
                self.progress.activity.add(
                    f"'{short_label(latest.text if latest else topic)}' 결정을 점검했습니다. "
                    f"반영 안 됨 {outcome.inconsistent_total}건"
                    + (", 확인 질문 1건" if outcome.pending_question else "")
                )
            report.llm_calls = self.counter.calls
            if all_done:
                self.db.meta_put("input_fp", input_fp)
                self._record_completion(len(topics), report)
        return report

    def process(self, paths: Iterable[Path] | None = None) -> CycleReport:
        self.progress.begin()
        sync = self.sync(paths)
        if sync.changed or sync.removed:
            self.progress.activity.add(
                f"파일 변경 {len(sync.changed)}건, 삭제 {len(sync.removed)}건을 반영했습니다."
            )
        report = self.analyze()
        report.sync = sync
        return report

    def answer(self, topic: str, text: str) -> CycleReport:
        """Record a user answer for the topic's pending question and re-check that topic."""
        with self.lock:
            stored = self.db.topic_get(topic)
            if stored is None:
                raise KeyError(topic)
            self.db.add_answer(topic, text.strip(), stored["decision_fp"])
            self.db.clear_question(topic)
            self.progress.begin()
            return self.analyze(force_topics={topic})
