from __future__ import annotations

import collections
import datetime
import json
import logging
import os
import platform
import queue
import shutil
import sys
import threading
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from decision_agent import __version__
from decision_agent.connectors.base import ConnectorError, SecretStore
from decision_agent.connectors.http import Http
from decision_agent.connectors.registry import CONNECTORS
from decision_agent.desktop import calendar_view
from decision_agent.desktop import digest as digest_module
from decision_agent.desktop import folders as folders_module
from decision_agent.desktop import notes as notes_store
from decision_agent.desktop import quick as quick_module
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.connector_manager import ConnectorManager
from decision_agent.desktop.drafts import latest_decision, make_draft
from decision_agent.desktop.engine import EXTERNAL_PREFIX, CycleReport, ProjectEngine
from decision_agent.desktop.errors import UserError
from decision_agent.desktop.notify import LogNotifier, Notifier
from decision_agent.desktop.paths import app_home, log_path, state_db_path
from decision_agent.desktop.policy import FolderPolicy
from decision_agent.desktop.progress import ActivityLog, ProgressTracker
from decision_agent.desktop.providers import (
    PersistentCachingProvider,
    ProviderBundle,
    build_provider,
    check_provider,
    describe_llm_error,
)
from decision_agent.desktop.settings import (
    AppSettings,
    ConnectorSettings,
    FolderRejected,
    add_folder,
    remove_folder,
    root_key,
    save_settings,
    set_excluded_subfolders,
)
from decision_agent.desktop.shell import DropBox
from decision_agent.desktop.state import StateDB, now_iso
from decision_agent.desktop.watcher import Debouncer, FolderWatcher, PeriodicTimer
from decision_agent.sources.model import ParsedDocument
from decision_agent.sources.readers import (
    READERS,
    UNSUPPORTED_HINTS,
    UnsupportedFormat,
    extension_of,
    read_document,
)

log = logging.getLogger(__name__)

MAX_TOASTS_PER_CYCLE = 3
RETRY_SECONDS = 15.0
FOLLOWUP_SECONDS = 300.0
MAX_LOCK_RETRIES = 4
MAX_FOLLOWUPS = 6
LOG_TAIL_LINES = 200


@dataclass
class Job:
    kind: str  # sync, answer, reanalyze
    root: str
    paths: set[Path] | None = None
    topic: str = ""
    text: str = ""
    attempt: int = 0


@dataclass
class _RootRuntime:
    engine: ProjectEngine
    db: StateDB
    watcher: FolderWatcher | None = None
    debouncer: Debouncer | None = None
    timer: PeriodicTimer | None = None


class DecisionAgentApp:
    """Controller that owns watchers, the analysis worker, notifications, and settings changes.

    All analysis runs on one worker thread, so only one folder is analysed at a time and the
    web UI, the tray, and the watchers never touch the engines concurrently.
    """

    def __init__(
        self,
        settings: AppSettings,
        settings_file: Path | None = None,
        notifier: Notifier | None = None,
        bundle_factory: Callable[[AppSettings], ProviderBundle] = build_provider,
        db_factory: Callable[[Path], StateDB] | None = None,
        start_watchers: bool = True,
        connector_http: Http | None = None,
        secret_store: SecretStore | None = None,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
        activity_file: Path | None = None,
    ) -> None:
        self.clock = clock
        self.hotkey_state: dict = {"enabled": False, "active": False, "spec": "", "reason": ""}
        self.settings_hooks: list[Callable[[AppSettings], None]] = []
        self._digest_timer: PeriodicTimer | None = None
        self.settings = settings
        self.settings_file = settings_file
        self.notifier = notifier or LogNotifier()
        self.bundle_factory = bundle_factory
        self.db_factory = db_factory or (lambda root: StateDB(state_db_path(root)))
        self.start_watchers = start_watchers
        self.bundle = bundle_factory(settings)
        self.runtimes: dict[str, _RootRuntime] = {}
        self.jobs: queue.Queue[Job | None] = queue.Queue()
        self.deferred: list[Job] = []
        self.paused = False
        self.busy = False
        self.last_cycle: str | None = None
        self.last_error: str | None = None
        self._last_error_notified: str | None = None
        self._lock = threading.RLock()
        self._exec_lock = threading.RLock()
        self._dbs: dict[str, StateDB] = {}
        self._worker: threading.Thread | None = None
        self._timers: list[threading.Timer] = []
        self._lock_attempts: dict[Path, int] = {}
        self.listeners: list[Callable[[], None]] = []
        self.activity = ActivityLog(path=activity_file)
        self.connectors = ConnectorManager(lambda: self.settings, connector_http, secret_store)
        self._connector_timer: PeriodicTimer | None = None
        self.idle = threading.Event()
        self.idle.set()
        self._running = False
        # Hooks set by the launcher; all optional so the controller also runs headless.
        self.ui_mode = "browser"
        self.ui_reason = ""
        self.folder_picker: Callable[[], str | None] | None = None
        self.activate_hook: Callable[[str], None] | None = None
        self.path_opener: Callable[[Path], None] = _open_in_file_manager
        self.autostart = None
        self.drops = DropBox()
        self.inbox = ActivityLog(limit=50)

    # lifecycle

    def start(self, initial_scan: bool = True) -> None:
        with self._lock:
            self._running = True
            self._build_runtimes()
            self._worker = threading.Thread(target=self._work, name="analysis-worker", daemon=True)
            self._worker.start()
            self._start_connector_timer()
            self._digest_timer = PeriodicTimer(60.0, self.digest_tick)
            self._digest_timer.start()
            if initial_scan:
                for root in self.runtimes:
                    self.enqueue(Job("sync", root))

    def _start_connector_timer(self) -> None:
        if self._connector_timer is not None:
            self._connector_timer.stop()
            self._connector_timer = None
        if not self.connectors.enabled_ids():
            return
        interval = self.settings.connector_interval_minutes * 60.0
        self._connector_timer = PeriodicTimer(interval, lambda: self.run_connectors())
        self._connector_timer.start()
        self._schedule(20.0, lambda: self.run_connectors())

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._connector_timer is not None:
                self._connector_timer.stop()
                self._connector_timer = None
            if self._digest_timer is not None:
                self._digest_timer.stop()
                self._digest_timer = None
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
            self._stop_runtimes()
        self.jobs.put(None)
        if self._worker is not None:
            self._worker.join(timeout=10)
        for db in self._dbs.values():
            db.close()
        self._dbs.clear()
        self.runtimes.clear()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until the queue is empty and the worker is idle (used by tests and the CLI)."""
        return self.idle.wait(timeout)

    def _build_runtimes(self) -> None:
        for folder in self.settings.allowed_folders:
            root = Path(folder).resolve()
            if not root.is_dir():
                log.warning("watched folder is missing: %s", root)
                continue
            key = str(root)
            if key in self.runtimes:
                continue
            db = self._dbs.get(key)
            if db is None:
                db = self._dbs[key] = self.db_factory(root)
            progress = ProgressTracker(on_change=self._changed, activity=self.activity)
            engine = ProjectEngine(root, self.settings, db, self.bundle, progress=progress)
            runtime = _RootRuntime(engine=engine, db=db)
            if self.start_watchers:
                policy = FolderPolicy.for_root(root, self.settings)
                debouncer = Debouncer(
                    self.settings.debounce_seconds,
                    lambda paths, k=key: self.enqueue(Job("sync", k, set(paths))),
                )
                debouncer.start()
                watcher = FolderWatcher(policy, debouncer.add)
                watcher.start()
                timer = PeriodicTimer(
                    self.settings.rescan_minutes * 60.0, lambda k=key: self.enqueue(Job("sync", k))
                )
                timer.start()
                runtime.debouncer, runtime.watcher, runtime.timer = debouncer, watcher, timer
            self.runtimes[key] = runtime

    def _stop_runtimes(self) -> None:
        for runtime in self.runtimes.values():
            if runtime.timer:
                runtime.timer.stop()
            if runtime.watcher:
                runtime.watcher.stop()
            if runtime.debouncer:
                runtime.debouncer.stop()

    # queue

    def enqueue(self, job: Job) -> None:
        with self._lock:
            if self.paused:
                self.deferred.append(job)
                self._changed()
                return
            self.idle.clear()
            self.jobs.put(job)

    def _schedule(self, delay: float, fn: Callable[[], None]) -> None:
        if not self._running:
            return
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        self._timers = [t for t in self._timers if t.is_alive()]
        self._timers.append(timer)
        timer.start()

    def pause(self) -> None:
        with self._lock:
            self.paused = True
        self._changed()

    def resume(self) -> None:
        with self._lock:
            self.paused = False
            pending, self.deferred = self.deferred, []
        merged: dict[tuple[str, str], Job] = {}
        for job in pending:
            if job.kind == "sync":
                key = (job.root, "sync")
                existing = merged.get(key)
                if existing is None:
                    merged[key] = job
                elif existing.paths is None or job.paths is None:
                    existing.paths = None
                else:
                    existing.paths |= job.paths
            else:
                merged[(job.root, f"{job.kind}:{job.topic}:{len(merged)}")] = job
        for job in merged.values():
            self.enqueue(job)
        self._changed()

    def rescan(self, root: str | None = None) -> None:
        for key in [root] if root else list(self.runtimes):
            if key in self.runtimes:
                self.enqueue(Job("sync", key))

    def answer(self, root: str, topic: str, text: str) -> None:
        self.enqueue(Job("answer", root, topic=topic, text=text))

    # worker

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                self.idle.set()
                return
            with self._lock:
                runtime = self.runtimes.get(job.root)
            self.busy = True
            self._changed()
            try:
                if runtime is not None:
                    with self._exec_lock:
                        self._run_job(runtime, job)
            except Exception as exc:
                log.exception("job failed")
                self.last_error = f"점검 중 오류: {exc}"
            finally:
                self.busy = False
                self.last_cycle = now_iso()
                if self.jobs.empty():
                    self.idle.set()
                self._changed()

    def _run_job(self, runtime: _RootRuntime, job: Job) -> None:
        engine = runtime.engine
        first_run = runtime.db.meta_get("initial_done") is None
        if job.kind == "sync":
            report = engine.process(job.paths)
        elif job.kind == "answer":
            report = engine.answer(job.topic, job.text)
        elif job.kind == "connectors":
            report = self._connector_cycle(runtime, job.text)
        elif job.kind == "notes":
            report = self._notes_cycle(runtime)
        else:
            report = engine.analyze(force=False)
        stamp = now_iso()
        if job.kind == "sync":
            # The heartbeat: the folder was looked at, whether or not anything changed.
            runtime.db.meta_put("last_checked", stamp)
        if report.rebuilt and not report.error and not report.needs_followup:
            runtime.db.meta_put("last_analyzed", stamp)
        self.last_error = report.error
        for path in report.sync.retry:
            attempts = self._lock_attempts.get(path, 0) + 1
            self._lock_attempts[path] = attempts
            if attempts <= MAX_LOCK_RETRIES:
                self._schedule(
                    RETRY_SECONDS,
                    lambda p=path, r=job.root: self.enqueue(Job("sync", r, {p})),
                )
        for path in report.sync.changed:
            self._lock_attempts.pop(Path(engine.root / path), None)
        if report.needs_followup:
            attempt = job.attempt + 1
            if attempt <= MAX_FOLLOWUPS:
                self._schedule(
                    FOLLOWUP_SECONDS,
                    lambda r=job.root, n=attempt: self.enqueue(Job("reanalyze", r, attempt=n)),
                )
            else:
                self.last_error = (report.error or "") + " 자동 재시도를 멈췄습니다. '지금 다시 점검'을 눌러 주세요."
        self._notify(runtime, report, first_run)
        if report.rebuilt and not report.needs_followup:
            runtime.db.meta_put("initial_done", "1")

    # connectors, notes, imports

    def target_root(self) -> str | None:
        """The folder whose analysis receives mail, calendar, tool, note and imported documents."""
        wanted = self.settings.connector_target
        if wanted:
            key = str(Path(wanted).resolve())
            if key in self.runtimes:
                return key
        return next(iter(self.runtimes), None)

    @staticmethod
    def _status_key(connector_id: str) -> str:
        return f"connector_status:{connector_id}"

    def _save_status(self, runtime: _RootRuntime, connector_id: str, status: dict) -> None:
        runtime.db.meta_put(self._status_key(connector_id), json.dumps(status, ensure_ascii=False))

    def _connector_cycle(self, runtime: _RootRuntime, only: str) -> CycleReport:
        engine = runtime.engine
        ids = [only] if only else self.connectors.enabled_ids()
        engine.progress.begin()
        engine.progress.set_phase("reading")
        engine.progress.set_files(len(ids))
        for connector_id in ids:
            label = self.connectors.cls(connector_id).label
            outcome, status = self.connectors.fetch(connector_id)
            self._save_status(runtime, connector_id, status)
            if outcome is not None:
                engine.set_external(connector_id, outcome.docs)
                self.activity.add(f"{label}: {outcome.summary}")
            else:
                self.activity.add(f"{label}: {status['message']}", "error")
            engine.progress.file_done(label)
        return engine.analyze()

    def _notes_cycle(self, runtime: _RootRuntime) -> CycleReport:
        doc = notes_store.notes_document(notes_store.list_notes(runtime.db))
        runtime.engine.progress.begin()
        runtime.engine.set_external("notes", {"notes": doc} if doc is not None else {})
        return runtime.engine.analyze()

    def run_connectors(self, connector_id: str = "") -> bool:
        target = self.target_root()
        if target is None:
            return False
        if not connector_id and not self.connectors.enabled_ids():
            return False
        self.enqueue(Job("connectors", target, text=connector_id))
        return True

    def connector_overview(self) -> list[dict]:
        target = self.target_root()
        runtime = self.runtimes.get(target) if target else None
        statuses: dict[str, dict | None] = {}
        for connector_id in self.settings.connectors:
            raw = runtime.db.meta_get(self._status_key(connector_id)) if runtime else None
            statuses[connector_id] = json.loads(raw) if raw else None
        rows = self.connectors.overview(statuses)
        for row in rows:
            row["target"] = target
        return rows

    def save_connector_config(self, connector_id: str, enabled: bool, options: dict) -> None:
        cleaned = self.connectors.coerce_options(connector_id, options)
        new = self.settings.model_copy(deep=True)
        current = new.connectors.get(connector_id) or ConnectorSettings()
        current.enabled = enabled
        current.options = {**current.options, **cleaned}
        new.connectors[connector_id] = current
        self.settings = new
        if self.settings_file is not None:
            save_settings(new, self.settings_file)
        self._start_connector_timer()
        self._changed()

    def disconnect_connector(self, connector_id: str) -> None:
        """Turn the connector off, forget its secrets, and drop the documents it delivered."""
        self.connectors.cls(connector_id)
        self.save_connector_config(connector_id, False, {})
        self.connectors.forget(connector_id)
        target = self.target_root()
        if target is not None:
            runtime = self.runtimes[target]
            runtime.db.meta_delete(self._status_key(connector_id))
            if runtime.engine.set_external(connector_id, {}):
                self.enqueue(Job("notes", target))

    def add_note(self, text: str, date: str | None = None) -> dict:
        target = self.target_root()
        if target is None:
            raise ConnectorError("메모를 분석할 감시 폴더를 먼저 등록하세요.")
        note = notes_store.add_note(self.runtimes[target].db, text, date)
        self.enqueue(Job("notes", target))
        return note

    def list_notes(self) -> list[dict]:
        target = self.target_root()
        return notes_store.list_notes(self.runtimes[target].db) if target else []

    def delete_note(self, note_id: str) -> bool:
        target = self.target_root()
        if target is None:
            return False
        removed = notes_store.delete_note(self.runtimes[target].db, note_id)
        if removed:
            self.enqueue(Job("notes", target))
        return removed

    def import_file(self, name: str, data: bytes) -> str:
        """Read an uploaded file of any supported format into the analysis (not kept on disk)."""
        target = self.target_root()
        if target is None:
            raise ConnectorError("가져온 파일을 분석할 감시 폴더를 먼저 등록하세요.")
        if len(data) > self.settings.max_file_mb * 1024 * 1024:
            raise ConnectorError("파일이 최대 크기를 넘습니다.")
        safe = Path(name).name or "import"
        scratch = app_home() / "tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        path = scratch / f"{uuid.uuid4().hex[:8]}-{safe}"
        try:
            path.write_bytes(data)
            extension = extension_of(path)
            if extension in UNSUPPORTED_HINTS:
                raise ConnectorError(UNSUPPORTED_HINTS[extension])
            if extension not in READERS:
                raise ConnectorError("지원하지 않는 형식입니다.")
            try:
                doc = read_document(path, scratch)
            except UnsupportedFormat as exc:
                raise ConnectorError(str(exc)) from exc
        finally:
            path.unlink(missing_ok=True)
        doc.meta["mtime_date"] = datetime.date.today().isoformat()
        self.runtimes[target].engine.put_external_doc("import", safe, doc)
        self.activity.add(f"파일을 가져왔습니다: {safe}")
        self.enqueue(Job("notes", target))
        return safe

    # drafts, quick check, search

    def _runtime_for(self, root: str) -> _RootRuntime:
        runtime = self.runtimes.get(root)
        if runtime is None:
            raise UserError("폴더를 찾을 수 없습니다.")
        return runtime

    def draft_fix(self, root: str, topic: str, artifact_id: str) -> dict:
        runtime = self._runtime_for(root)
        row = next(
            (r for r in runtime.db.list_findings(True) if r["topic"] == topic and r["artifact_id"] == artifact_id),
            None,
        )
        if row is None:
            raise UserError("항목을 찾을 수 없습니다.")
        topic_row = runtime.db.topic_get(topic)
        decision = latest_decision(topic_row["summary"]) if topic_row else ""
        provider = None
        if not self.bundle.local_only:
            provider = PersistentCachingProvider(
                self.bundle.provider, runtime.db, self.bundle.provider_id
            )
        try:
            return make_draft(provider, dict(row), decision)
        except Exception as exc:
            message, _ = describe_llm_error(exc)
            raise UserError(message) from exc

    def quick_preview(self, text: str) -> dict:
        text = text.strip()
        if len(text) < 2:
            raise UserError("내용을 입력하세요.")
        artifacts: list[dict] = []
        decisions: list[dict] = []
        for key, runtime in self._each():
            labels = {f["artifact_id"]: f["label"] for f in runtime.db.list_findings()}
            build = runtime.engine.last_build
            for artifact in (build.artifacts if build else []):
                artifacts.append(
                    {
                        "id": artifact.id, "kind": artifact.kind.value, "text": artifact.text,
                        "source_doc": artifact.source_doc, "root": key,
                        "label": labels.get(artifact.id, ""),
                    }
                )
            for row in quick_module.topic_rows([dict(r) for r in runtime.db.list_topics()]):
                decisions.append({**row, "root": key})
        result = quick_module.preview(text, artifacts, decisions)
        result["checked_kinds"] = sorted({a["kind"] for a in artifacts})
        return result

    def search(self, query: str) -> dict:
        findings = self.findings()
        topics: list[dict] = []
        files: list[str] = []
        for _, runtime in self._each():
            topics += quick_module.topic_rows([dict(r) for r in runtime.db.list_topics()])
            files += [r["path"] for r in runtime.db.list_files()]
        return quick_module.search(query, findings, topics, files, self.list_notes())

    # home calendar

    def calendar_week(self, start: str | None = None) -> dict:
        """One week (Monday to Sunday) of dated items from calendars, .ics files and schedules."""
        today = self.clock().date()
        first = calendar_view.week_start(calendar_view.parse_day(start, today))
        artifacts: list[tuple[str, object]] = []
        docs = []
        for key, runtime in self._each():
            build = runtime.engine.last_build
            if build is not None:
                artifacts.extend((key, a) for a in build.artifacts)
            for row in runtime.db.list_files():
                if row["status"] != "ok" or not row["payload"]:
                    continue
                if '"time"' not in row["payload"]:
                    continue
                try:
                    docs.append(ParsedDocument.model_validate_json(row["payload"]))
                except ValueError:
                    continue
        connected = any(c in self.connectors.enabled_ids() for c in calendar_view.CALENDAR_SOURCES)
        week = calendar_view.build_week(
            first, artifacts, self.findings(), calendar_view.event_times(docs), connected
        )
        week["today"] = today.isoformat()
        return week

    # daily digest

    def digest_now(self) -> dict:
        now = self.clock()
        info = digest_module.build_digest(self.status(), self.findings(), self.questions(), now)
        path = digest_module.save_digest(info["markdown"], now)
        info["path"] = str(path)
        return info

    def digest_tick(self) -> None:
        """Called every minute. Sends the digest once a day after the configured time."""
        if not self.settings.digest_enabled or not self.runtimes or self.paused:
            return
        runtime = next(iter(self.runtimes.values()))
        now = self.clock()
        last = runtime.db.meta_get("last_digest_date")
        if not digest_module.is_due(now, self.settings.digest_time, last):
            return
        runtime.db.meta_put("last_digest_date", now.date().isoformat())
        info = self.digest_now()
        self.activity.add(f"일일 요약을 만들었습니다: {info['path']}")
        self.inbox.add(f"{info['title']}: {info['short']}")
        if self.settings.notify:
            self.notifier.notify(info["title"], info["short"])

    def latest_digest(self) -> dict | None:
        folder = app_home() / "digests"
        files = sorted(folder.glob("*.md")) if folder.exists() else []
        if not files:
            return None
        return {"name": files[-1].name, "markdown": files[-1].read_text(encoding="utf-8")}

    # what the program may read

    def access_report(self) -> dict:
        folders = []
        for key, runtime in self._each():
            rows = runtime.db.list_files()
            counts = Counter(r["status"] for r in rows if not r["path"].startswith(EXTERNAL_PREFIX))
            folders.append(
                {
                    "root": key,
                    "exclude_patterns": self.settings.exclude_patterns,
                    "max_file_mb": self.settings.max_file_mb,
                    "files": dict(counts),
                    "external_documents": sum(1 for r in rows if r["path"].startswith(EXTERNAL_PREFIX)),
                    "scan_stats": json.loads(runtime.db.meta_get("scan_stats") or "null"),
                }
            )
        enabled = []
        for connector_id in self.connectors.enabled_ids():
            cls = self.connectors.cls(connector_id)
            enabled.append({"id": connector_id, "label": cls.label, "reads": cls.reads})
        return {
            "folders": folders,
            "connectors": enabled,
            "target": self.target_root(),
            "provider": {
                "id": self.bundle.provider_id,
                "local_only": self.bundle.local_only,
                "sends_externally": self.bundle.sends_externally,
                "reason": self.bundle.reason,
            },
            "storage": {"app_home": str(app_home()), "log": str(log_path())},
            "never": [
                "등록하지 않은 폴더와 그 안의 파일 (폴더 밖을 가리키는 바로가기와 심볼릭 링크 포함)",
                "드라이브 루트, 사용자 홈 폴더 전체, 시스템 폴더, 이 프로그램의 데이터 폴더는 등록할 수 없음",
                "감시 폴더 안 파일의 수정, 이름 변경, 삭제, 새 파일 만들기",
                "메일 보내기, 지우기, 읽음 표시 바꾸기, 일정 만들기와 고치기, 메시지 보내기",
                "API 키와 비밀번호를 설정 파일이나 실행 파일에 저장하기 (운영체제 자격 증명 저장소만 사용)",
                "외부 전송 스위치, API 키, 모델 이름이 모두 없으면 문서 내용을 외부 서비스로 보내기",
            ],
            "stored_locally": [
                "읽은 문서의 구조화된 텍스트 사본 (메일과 캘린더 내용 포함)이 데이터 폴더의 상태 파일에 저장됩니다.",
                "연결을 해제하면 그 연결이 가져온 사본이 지워집니다.",
            ],
        }

    # notifications

    def _topic_label(self, runtime: _RootRuntime, topic: str) -> str:
        row = runtime.db.topic_get(topic)
        if row is not None:
            try:
                latest = json.loads(row["summary"]).get("latest", "")
                if latest:
                    return latest if len(latest) <= 40 else latest[:37] + "..."
            except (ValueError, TypeError):
                pass
        return topic

    def _completion_message(
        self, runtime: _RootRuntime, report: CycleReport, total_new: int
    ) -> tuple[str, str] | None:
        """One summary toast when a folder has just been checked under a new judge.

        This is the visible "check finished" signal. It fires the first time a folder is checked
        and again when the judge changes (for example after the API key is added), and it says
        plainly whether the real model or only the demo judge was used.
        """
        if not report.rebuilt or report.error or report.needs_followup:
            return None
        key = self.bundle.provider_id
        previous = runtime.db.meta_get("completed_notified")
        if previous == key:
            return None
        runtime.db.meta_put("completed_notified", key)
        info = runtime.engine.completion() or {}
        files = info.get("files_ok", 0)
        topics = info.get("topics", 0)
        if self.bundle.local_only:
            title = "처음 점검을 마쳤습니다" if previous is None else "데모 판정기로 다시 점검했습니다"
            body = (
                f"파일 {files}개, 결정 {topics}건. 데모 판정기라 실제 검사가 아닙니다. "
                f"반영 안 됨 {total_new}건."
            )
        else:
            title = "실제 모델 점검을 마쳤습니다" if previous is not None else "처음 점검을 마쳤습니다"
            body = (
                f"{self.bundle.provider_id}로 파일 {files}개, 결정 {topics}건을 점검했습니다. "
                f"반영 안 됨 {total_new}건."
            )
        return title, body

    def _notify(self, runtime: _RootRuntime, report: CycleReport, first_run: bool) -> None:
        messages: list[tuple[str, str]] = []
        marks: list[tuple[str, str]] = []
        total_new = 0
        for outcome in report.outcomes:
            if outcome.new_inconsistent:
                total_new += len(outcome.new_inconsistent)
                first = outcome.new_inconsistent[0]
                label = self._topic_label(runtime, outcome.topic)
                owner = f", 담당 {first['owner']}" if first.get("owner") else ""
                body = (
                    f"'{label}' 결정과 맞지 않는 항목 {len(outcome.new_inconsistent)}건"
                    f" (예: {first['text'][:40]}, {first['source_doc']}{owner})"
                )
                messages.append(("결정 변경이 반영되지 않았을 수 있습니다", body))
                marks.extend((outcome.topic, i["artifact_id"]) for i in outcome.new_inconsistent)
            if outcome.new_question and outcome.pending_question:
                messages.append(("확인이 필요합니다", outcome.pending_question))
                runtime.db.mark_question_notified(outcome.topic)
        if marks:
            runtime.db.mark_notified(marks)
        summary = self._completion_message(runtime, report, total_new)
        if summary is not None:
            self.activity.add(f"{summary[0]}: {summary[1]}")
        if summary is not None:
            self.inbox.add(f"{summary[0]}: {summary[1]}")
        for title, body in messages:
            self.inbox.add(f"{title}: {body}", "attention")
        if report.error and report.error != self._last_error_notified:
            self.inbox.add(f"점검 중 문제가 발생했습니다: {report.error[:200]}", "error")
        if self.settings.notify:
            if summary is not None:
                messages = [summary] + [m for m in messages if m[0] == "확인이 필요합니다"][:1]
            for title, body in messages[:MAX_TOASTS_PER_CYCLE]:
                self.notifier.notify(title, body)
            extra = len(messages) - MAX_TOASTS_PER_CYCLE
            if extra > 0:
                self.notifier.notify("결정 변경 점검", f"그 밖에 알림 {extra}건이 더 있습니다.")
            if report.error and report.error != self._last_error_notified:
                self._last_error_notified = report.error
                self.notifier.notify("점검 중 문제가 발생했습니다", report.error[:200])
            if not report.error:
                self._last_error_notified = None

    # data for the UI and the tray

    def _changed(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                log.exception("listener failed")

    def _each(self):
        with self._lock:
            return list(self.runtimes.items())

    def status(self) -> dict:
        folders = []
        inconsistent = uncertain = 0
        questions = 0
        for key, runtime in self._each():
            files = runtime.db.list_files()
            counts = {"ok": 0, "error": 0, "unsupported": 0}
            for row in files:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            for finding in runtime.db.list_findings():
                if finding["label"] == "INCONSISTENT":
                    inconsistent += 1
                elif finding["label"] == "UNCERTAIN":
                    uncertain += 1
            questions += len(runtime.db.list_questions())
            topic_rows = runtime.db.list_topics()
            folders.append(
                {
                    "root": key,
                    "files": counts,
                    "topics": len(topic_rows),
                    "topics_by_judge": dict(Counter(r["judge"] or "?" for r in topic_rows)),
                    "watcher": runtime.watcher.mode if runtime.watcher else "off",
                    "pending_events": runtime.debouncer.pending_count() if runtime.debouncer else 0,
                    "progress": runtime.engine.progress.snapshot(),
                    "completion": runtime.engine.completion(),
                    "failure": runtime.engine.last_failure(),
                    "scan_stats": json.loads(runtime.db.meta_get("scan_stats") or "null"),
                    "last_checked": runtime.db.meta_get("last_checked"),
                    "last_analyzed": runtime.db.meta_get("last_analyzed"),
                }
            )
        return {
            "paused": self.paused,
            "busy": self.busy,
            "folders": folders,
            "provider": {
                "id": self.bundle.provider_id,
                "local_only": self.bundle.local_only,
                "sends_externally": self.bundle.sends_externally,
                "reason": self.bundle.reason,
            },
            "registered_folders": len(self.settings.allowed_folders),
            "connected_accounts": len(self.connectors.enabled_ids()),
            "onboarding_needed": not self.settings.onboarding_done and not self.settings.allowed_folders,
            "ui_mode": self.ui_mode,
            "version": __version__,
            "inconsistent": inconsistent,
            "uncertain": uncertain,
            "questions": questions,
            "last_cycle": self.last_cycle,
            "last_error": self.last_error,
            "debounce_seconds": self.settings.debounce_seconds,
            "overall": self.overall(folders),
            "hotkey": self.hotkey_state,
            "watch": self.watch_info(folders),
            "last_result": self.last_result(folders),
        }

    def watch_info(self, folders: list[dict]) -> dict:
        """How the folders are watched and when they were last looked at.

        last_checked moves on every check, even when nothing changed; last_analyzed only when
        the documents were analysed again. Times are ISO strings in local time.
        """
        checked = [f["last_checked"] for f in folders if f.get("last_checked")]
        analyzed = [f["last_analyzed"] for f in folders if f.get("last_analyzed")]
        modes = {f["watcher"] for f in folders}
        return {
            "mode": "realtime" if modes & {"native", "polling"} else "periodic",
            "rescan_minutes": self.settings.rescan_minutes,
            "last_checked": max(checked) if checked else None,
            "last_analyzed": max(analyzed) if analyzed else None,
            "folders": len(folders),
        }

    @staticmethod
    def last_result(folders: list[dict]) -> dict | None:
        """The last finished check across folders ("문서 5개, 결정 4건 확인"), or None."""
        records = [f["completion"] for f in folders if f.get("completion")]
        if not records:
            return None
        return {
            "at": max(r["at"] for r in records),
            "files": sum(r["files_ok"] for r in records),
            "topics": sum(r["topics"] for r in records),
            "verified": all(r["verified"] for r in records),
        }

    def overall(self, folders: list[dict]) -> dict:
        """One honest sentence about how complete the checking is.

        verified means every folder was fully checked by a real model. demo means it was only
        checked by the built-in rule judge, which is not a real check of normal documents.
        """
        if not folders:
            return {"state": "no_folders", "label": "감시 폴더가 없습니다.", "detail": ""}
        if self.busy or not self.idle.is_set():
            return {"state": "running", "label": "점검 중입니다.", "detail": ""}
        for folder in folders:
            failure = folder["failure"]
            if failure and failure.get("fatal"):
                return {"state": "failed", "label": "점검에 실패했습니다.", "detail": failure["message"]}
        for folder in folders:
            if folder["failure"]:
                return {
                    "state": "retrying",
                    "label": "일부만 점검했고 자동으로 다시 시도합니다.",
                    "detail": folder["failure"]["message"],
                }
        records = [f["completion"] for f in folders]
        if any(r is None for r in records):
            return {"state": "incomplete", "label": "아직 점검을 끝내지 못했습니다.", "detail": ""}
        if any(r["provider_id"] != self.bundle.provider_id for r in records):
            return {"state": "stale", "label": "판정 방식이 바뀌어 다시 점검해야 합니다.", "detail": ""}
        files = sum(r["files_ok"] for r in records)
        topics = sum(r["topics"] for r in records)
        latest = max(r["at"] for r in records)
        if all(r["verified"] for r in records):
            return {
                "state": "verified",
                "label": "검사 완료",
                "detail": f"{self.bundle.provider_id} / 파일 {files}개, 결정 {topics}건 / {latest}",
            }
        return {
            "state": "demo",
            "label": "데모 판정기로만 점검했습니다. 실제 검사가 아닙니다.",
            "detail": f"파일 {files}개, 결정 {topics}건 / {latest}",
        }

    def test_provider(self) -> dict:
        """Check the model connection right now (used after saving a key)."""
        result = check_provider(self.bundle)
        self.activity.add(result["message"], "info" if result["ok"] else "error")
        return result

    def log_tail(self, lines: int = LOG_TAIL_LINES) -> list[str]:
        path = log_path()
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return [line.rstrip("\n") for line in collections.deque(handle, maxlen=lines)]
        except OSError:
            return []

    def _topic_summary(self, runtime: _RootRuntime, topic: str) -> dict:
        row = runtime.db.topic_get(topic)
        try:
            return json.loads(row["summary"]) if row is not None and row["summary"] else {}
        except (ValueError, TypeError):
            return {}

    def findings(self, include_hidden: bool = False) -> list[dict]:
        """Findings with what the detail panel shows: decision text, its source and date, and
        the date of the item itself when it is a dated schedule entry."""
        rows: list[dict] = []
        for key, runtime in self._each():
            build = runtime.engine.last_build
            dates = {
                a.id: str(a.metadata.get("date") or "")[:10] for a in (build.artifacts if build else [])
            }
            summaries: dict[str, dict] = {}
            for row in runtime.db.list_findings(include_hidden):
                item = dict(row)
                item["root"] = key
                item["topic_label"] = self._topic_label(runtime, row["topic"])
                if row["topic"] not in summaries:
                    summaries[row["topic"]] = self._topic_summary(runtime, row["topic"])
                summary = summaries[row["topic"]]
                item["decision_text"] = summary.get("latest") or item["topic_label"]
                item["decision_date"] = summary.get("date", "")
                item["decision_source"] = summary.get("source", "")
                item["date"] = dates.get(row["artifact_id"], "")
                rows.append(item)
        return rows

    def questions(self) -> list[dict]:
        rows: list[dict] = []
        for key, runtime in self._each():
            for row in runtime.db.list_questions():
                item = dict(row)
                item["root"] = key
                item["topic_label"] = self._topic_label(runtime, row["topic"])
                rows.append(item)
        return rows

    def files(self) -> list[dict]:
        rows: list[dict] = []
        for key, runtime in self._each():
            summaries = {}
            unclassified: set[str] = set()
            if runtime.engine.last_build is not None:
                summaries = {s.path: s for s in runtime.engine.last_build.summaries}
                unclassified = set(runtime.engine.last_build.unclassified)
            for row in runtime.db.list_files():
                summary = summaries.get(row["path"])
                rows.append(
                    {
                        "root": key,
                        "path": row["path"],
                        "fmt": row["fmt"],
                        "status": row["status"],
                        "message": row["message"],
                        "kinds": summary.kinds if summary else [],
                        "meeting_items": summary.meeting_items if summary else 0,
                        "artifacts": summary.artifacts if summary else 0,
                        "warnings": summary.warnings if summary else [],
                        "unclassified": row["path"] in unclassified
                        or any(u.startswith(row["path"] + " [") for u in unclassified),
                    }
                )
        return rows

    def set_finding_status(
        self,
        root: str,
        topic: str,
        artifact_id: str,
        status: str,
        snooze_hours: int = 0,
        reason: str = "",
    ) -> bool:
        if status not in {"open", "dismissed", "false_positive", "snoozed"}:
            raise ValueError(status)
        runtime = self.runtimes.get(root)
        if runtime is None:
            return False
        until = None
        if status == "snoozed":
            until = (
                datetime.datetime.now() + datetime.timedelta(hours=max(1, snooze_hours or 24))
            ).replace(microsecond=0).isoformat()
        ok = runtime.db.set_finding_status(topic, artifact_id, status, until)
        if ok and status != "open":
            text = next(
                (r["text"] for r in runtime.db.list_findings(True)
                 if r["topic"] == topic and r["artifact_id"] == artifact_id),
                artifact_id,
            )
            what = {"dismissed": "확인 완료로", "false_positive": "잘못된 탐지로", "snoozed": "내일 다시 알림으로"}
            note = f" 사유: {' '.join(reason.split())[:200]}" if reason.strip() else ""
            self.activity.add(f"'{text[:40]}' 항목을 {what[status]} 표시했습니다.{note}")
        self._changed()
        return ok

    # settings changes

    def reconfigure(self, new_settings: AppSettings) -> None:
        """Apply new settings: restart watchers and engines, then rescan every folder.

        Waits for a running analysis to finish first, so state databases are never closed
        while the worker is using them.
        """
        with self._exec_lock, self._lock:
            self._stop_runtimes()
            self.runtimes.clear()
            self.settings = new_settings
            self.bundle = self.bundle_factory(new_settings)
            keep = {str(Path(p).resolve()) for p in new_settings.allowed_folders}
            for key in [k for k in self._dbs if k not in keep]:
                self._dbs.pop(key).close()
            self._build_runtimes()
            if self.settings_file is not None:
                save_settings(new_settings, self.settings_file)
        for key in list(self.runtimes):
            self.enqueue(Job("sync", key))
        self._start_connector_timer()
        for hook in list(self.settings_hooks):
            try:
                hook(new_settings)
            except Exception:
                log.exception("settings hook failed")
        self._changed()

    def refresh_provider(self) -> None:
        """Re-read provider choice after the API key or external-send switch changed."""
        self.reconfigure(self.settings)

    def add_folder(self, path: str, merge: bool = False) -> Path:
        new = self.settings.model_copy(deep=True)
        before = list(new.allowed_folders)
        resolved = add_folder(new, path, merge=merge)
        if new.allowed_folders != self.settings.allowed_folders:
            self.reconfigure(new)
            kept = {root_key(p) for p in new.allowed_folders}
            for folder in before:
                if root_key(folder) not in kept:
                    self._forget_state(Path(folder))
            self.activity.add(f"폴더를 추가했습니다: {resolved}")
        return resolved

    def remove_folder(self, path: str) -> bool:
        """Stop watching a folder and delete the app's copy of what was read from it.

        The folder itself and its files are never touched.
        """
        new = self.settings.model_copy(deep=True)
        removed = remove_folder(new, path)
        if removed:
            self.reconfigure(new)
            self._forget_state(Path(path))
            self.activity.add(f"폴더를 목록에서 제거했습니다: {path}")
        return removed

    def _forget_state(self, root: Path) -> None:
        """Delete the state database of a folder that is no longer watched (app data only)."""
        if root_key(root) in {root_key(k) for k in self._dbs}:
            return
        target = state_db_path(root)
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = target.with_name(target.name + suffix)
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                log.warning("could not delete state file %s", candidate)

    # folder screens

    def preview_folder(self, path: str) -> dict:
        return folders_module.preview_folder(path, self.settings)

    def _registered(self, root: str) -> Path:
        wanted = root_key(root)
        for folder in self.settings.allowed_folders:
            if root_key(folder) == wanted:
                return Path(folder)
        raise UserError("등록된 감시 폴더가 아닙니다.")

    def folder_tree(self, root: str) -> dict:
        folder = self._registered(root)
        if not folder.is_dir():
            raise UserError("폴더를 찾을 수 없습니다. 옮겨졌거나 삭제되었을 수 있습니다.")
        data = folders_module.folder_tree(folder, self.settings)
        runtime = self.runtimes.get(str(folder.resolve()))
        build = runtime.engine.last_build if runtime is not None else None
        kinds = {
            s.path: list(s.kinds) for s in (build.summaries if build else [])
            if not s.path.startswith(EXTERNAL_PREFIX)
        }
        folders_module.add_kind_counts(data["tree"], kinds)
        return data

    def set_excluded(self, root: str, relative: list[str]) -> list[str]:
        new = self.settings.model_copy(deep=True)
        try:
            cleaned = set_excluded_subfolders(new, root, relative)
        except FolderRejected as exc:
            raise UserError(str(exc)) from exc
        self.reconfigure(new)
        self.activity.add(f"제외할 하위 폴더를 바꿨습니다: {root} ({len(cleaned)}개 제외)")
        return cleaned

    def folder_cards(self) -> list[dict]:
        cards = []
        runtimes = dict(self._each())
        for folder in self.settings.allowed_folders:
            key = str(Path(folder).resolve())
            runtime = runtimes.get(key)
            exists = Path(folder).is_dir()
            counts: Counter[str] = Counter()
            completion = scan = None
            if runtime is not None:
                counts = Counter(
                    r["status"] for r in runtime.db.list_files()
                    if not r["path"].startswith(EXTERNAL_PREFIX)
                )
                completion = runtime.engine.completion()
                scan = json.loads(runtime.db.meta_get("scan_stats") or "null")
            if not exists:
                watch = "missing"
            elif self.paused:
                watch = "paused"
            else:
                watch = "watching"
            cards.append(
                {
                    "root": key,
                    "exists": exists,
                    "watch": watch,
                    "watcher_mode": runtime.watcher.mode if runtime and runtime.watcher else "off",
                    "files_ok": counts.get("ok", 0),
                    "files_error": counts.get("error", 0),
                    "files_unsupported": counts.get("unsupported", 0),
                    "last_checked": (completion or {}).get("at") or (scan or {}).get("at"),
                    "excluded": self.settings.excluded_for(key),
                    "is_target": key == self.target_root(),
                }
            )
        return cards

    def pick_folder(self) -> str | None:
        if self.folder_picker is None:
            raise UserError("이 환경에서는 폴더 선택 창을 열 수 없습니다. 경로를 직접 입력하세요.")
        return self.folder_picker()

    def open_location(self, target: str) -> None:
        """Open a registered folder, the data folder, or the log folder in the file manager."""
        if target == "data":
            path = app_home()
        elif target == "logs":
            path = log_path().parent
        else:
            path = self._registered(target)
        if not path.is_dir():
            raise UserError("폴더를 찾을 수 없습니다.")
        self.path_opener(path)

    def activate(self, page: str = "") -> bool:
        if self.activate_hook is None:
            return False
        self.activate_hook(page)
        return True

    # data management

    def diagnostics(self) -> str:
        """Plain text for support requests. Contains no secret values and no document text."""
        status = self.status()
        lines = [
            f"Decision Agent {__version__}",
            f"OS: {platform.platform()}",
            f"Python: {sys.version.split()[0]}",
            f"화면 방식: {self.ui_mode} {self.ui_reason}".rstrip(),
            (
                f"판정기: {self.bundle.provider_id} (로컬 전용: {self.bundle.local_only}, "
                f"외부 전송: {self.bundle.sends_externally})"
            ),
            f"판정기 설명: {self.bundle.reason}",
            f"API 키: {'저장됨' if secret_store.get_api_key() else '없음'}",
            f"외부 전송 스위치: {'켜짐' if self.settings.allow_external_send else '꺼짐'}",
            f"모델 이름: {self.settings.model_name or '(없음)'}",
            f"상태: {status['overall']['state']} - {status['overall']['label']}",
            f"감시 폴더 수: {len(self.settings.allowed_folders)}",
        ]
        for card in self.folder_cards():
            lines.append(
                f"  폴더: 상태 {card['watch']}, 감시 방식 {card['watcher_mode']}, "
                f"읽은 파일 {card['files_ok']}, 읽지 못함 {card['files_error']}, "
                f"지원 안 함 {card['files_unsupported']}, 제외 하위 폴더 {len(card['excluded'])}"
            )
        enabled = self.connectors.enabled_ids()
        lines.append(f"켜 둔 연결: {', '.join(enabled) if enabled else '(없음)'}")
        lines.append(f"마지막 오류: {self.last_error or '(없음)'}")
        return "\n".join(lines)

    def purge_all_data(self) -> dict:
        """Delete every secret and all app data, then start over with default settings.

        Removes the API key and connector secrets from the credential store, the state
        databases, settings, digests, and caches. Files in the watched folders are not touched.
        """
        with self._exec_lock, self._lock:
            self._stop_runtimes()
            self.runtimes.clear()
            for db in self._dbs.values():
                db.close()
            self._dbs.clear()
        for connector_id in CONNECTORS:
            try:
                self.connectors.forget(connector_id)
            except Exception:  # noqa: BLE001 - keep going so the rest is still deleted
                log.warning("could not forget connector %s", connector_id)
        secret_store.delete_api_key()
        home = app_home()
        for name in ("state", "digests", "tmp", "webview"):
            shutil.rmtree(home / name, ignore_errors=True)
        targets = [
            home / "settings.json", home / "settings.broken.json", home / "ui_state.json",
            home / "activity.jsonl",
        ]
        if self.settings_file is not None:
            targets.append(self.settings_file)
        for target in targets:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                log.warning("could not delete %s", target)
        self.activity.add("모든 앱 데이터를 삭제했습니다. 감시 폴더의 파일은 그대로입니다.")
        self.reconfigure(AppSettings(ui_port=self.settings.ui_port))
        return {"ok": True, "data_folder": str(home)}


def _open_in_file_manager(path: Path) -> None:
    """Show a folder in Explorer (or the platform file manager). Opening never writes to it."""
    if sys.platform == "win32":
        os.startfile(str(path))
        return
    import subprocess
    import webbrowser

    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener:
        subprocess.Popen([opener, str(path)])
    else:
        webbrowser.open(path.as_uri())


__all__ = ["DecisionAgentApp", "Job"]
