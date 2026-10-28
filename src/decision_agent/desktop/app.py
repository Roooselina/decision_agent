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
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from decision_agent import __version__
from decision_agent.connectors.base import ConnectorError, SecretStore, mail_summary
from decision_agent.connectors.http import Http
from decision_agent.connectors.registry import CONNECTORS
from decision_agent.desktop import calendar_view, mailsources
from decision_agent.desktop import digest as digest_module
from decision_agent.desktop import folders as folders_module
from decision_agent.desktop import notes as notes_store
from decision_agent.desktop import quick as quick_module
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.connector_manager import ConnectorManager
from decision_agent.desktop.drafts import latest_decision, make_draft
from decision_agent.desktop.engine import EXTERNAL_PREFIX, CycleReport, ProjectEngine
from decision_agent.desktop.errors import UserError
from decision_agent.desktop.helper import quiet_now
from decision_agent.desktop.hotkey import hotkey_status
from decision_agent.desktop.maildb import MailDB
from decision_agent.desktop.notify import LogNotifier, Notifier
from decision_agent.desktop.paths import app_home, log_path, mail_db_path, state_db_path
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
    MAIL_CONNECTORS,
    MAX_MAIL_SOURCES,
    AppSettings,
    ConnectorSettings,
    FolderRejected,
    MailSource,
    add_folder,
    migrate_mail_sources,
    new_source_id,
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
SUPERVISE_SECONDS = 30.0
PARTIAL_LABEL = "일부만 점검했습니다. 호출 한도를 늘리거나 다시 점검하세요."
# Settings that only change how the program shows itself. Saving them does not restart the
# watchers or check the folders again.
LIGHT_FIELDS = frozenset(
    {"helper_enabled", "helper_x", "helper_y", "quiet_hours_enabled", "quiet_start", "quiet_end", "confirm_over_calls",
     "mail_prefilter"}
)


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
        mail_db: MailDB | None = None,
    ) -> None:
        self.clock = clock
        # Local mail index and address book (app folder, never inside a watched folder).
        self.maildb = mail_db or MailDB(mail_db_path())
        self.hotkey_state: dict = hotkey_status(settings.hotkey, False, False, "")
        self.settings_hooks: list[Callable[[AppSettings], None]] = []
        self._digest_timer: PeriodicTimer | None = None
        self._supervisor: PeriodicTimer | None = None
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
        # Heartbeat for the helper: the last time the supervisor or the worker reported in.
        self.last_tick: float | None = None
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
            self.last_tick = time.time()
            self._migrate_mail_settings()
            self._build_runtimes()
            self._worker = threading.Thread(target=self._work, name="analysis-worker", daemon=True)
            self._worker.start()
            self._start_connector_timer()
            self._digest_timer = PeriodicTimer(60.0, self.digest_tick)
            self._digest_timer.start()
            self._supervisor = PeriodicTimer(SUPERVISE_SECONDS, self.supervise)
            self._supervisor.start()
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
            if self._supervisor is not None:
                self._supervisor.stop()
                self._supervisor = None
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
            self._stop_runtimes()
            # A running check stops at its next model call instead of running to the end.
            for runtime in self.runtimes.values():
                runtime.engine.cancel()
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
            self._drop_single_label_mail(runtime)
            if self.start_watchers:
                self._start_debouncer(key, runtime)
                self._start_watcher(key, runtime)
                self._start_timer(key, runtime)
            self.runtimes[key] = runtime

    def _start_debouncer(self, key: str, runtime: _RootRuntime) -> None:
        debouncer = Debouncer(
            self.settings.debounce_seconds,
            lambda paths, k=key: self.enqueue(Job("sync", k, set(paths))),
            max_wait=max(self.settings.debounce_max_seconds, self.settings.debounce_seconds),
        )
        debouncer.start()
        runtime.debouncer = debouncer

    def _start_watcher(self, key: str, runtime: _RootRuntime) -> None:
        policy = FolderPolicy.for_root(Path(key), self.settings)
        watcher = FolderWatcher(policy, lambda path: runtime.debouncer.add(path))
        watcher.start()
        runtime.watcher = watcher

    def _start_timer(self, key: str, runtime: _RootRuntime) -> None:
        # The heartbeat: a full look at the folder every rescan_minutes. Unchanged files are
        # skipped by size and time and unchanged topics by fingerprint, so no LLM call is made.
        timer = PeriodicTimer(
            self.settings.rescan_minutes * 60.0, lambda k=key: self.enqueue(Job("sync", k))
        )
        timer.start()
        runtime.timer = timer

    def supervise(self) -> list[str]:
        """Restart watcher parts that died (called every SUPERVISE_SECONDS). Returns what restarted.

        A missing folder is not restarted; the folder screen shows it as missing instead.
        """
        restarted: list[str] = []
        with self._lock:
            if not self._running:
                return restarted
            self.last_tick = time.time()
            if self._worker is not None and not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="analysis-worker", daemon=True)
                self._worker.start()
                restarted.append("analysis")
            for key, runtime in list(self.runtimes.items()):
                if not self.start_watchers or not Path(key).is_dir():
                    continue
                name = Path(key).name or key
                parts = (
                    ("debouncer", runtime.debouncer, self._start_debouncer),
                    ("watcher", runtime.watcher, self._start_watcher),
                    ("timer", runtime.timer, self._start_timer),
                )
                for label, part, starter in parts:
                    if part is not None and part.is_alive():
                        continue
                    try:
                        if part is not None:
                            part.stop()
                        starter(key, runtime)
                    except Exception:  # try again on the next round
                        log.exception("could not restart %s for %s", label, key)
                        continue
                    restarted.append(f"{label}:{name}")
        for item in restarted:
            log.warning("restarted %s", item)
        if restarted:
            self.activity.add("폴더 감시를 다시 시작했습니다. (" + ", ".join(restarted) + ")", "attention")
        # Every round reports in, so the helper can tell a quiet program from a stuck one.
        self._changed()
        return restarted

    def pending_changes(self) -> list[dict]:
        """Changes the watchers saw that are still waiting for the quiet period, per folder."""
        items = []
        for _, runtime in self._each():
            info = runtime.debouncer.pending_info() if runtime.debouncer else None
            if info:
                items.append(
                    {"name": Path(info["path"]).name, "count": info["count"], "seconds": info["seconds"]}
                )
        return sorted(items, key=lambda i: i["seconds"])

    def update_settings_light(self, changes: dict, announce: bool = True) -> AppSettings:
        """Save settings from LIGHT_FIELDS without restarting watchers or checking again.

        announce=False skips the settings hooks and listeners (used for the helper position,
        which changes while the user drags it).
        """
        unknown = set(changes) - LIGHT_FIELDS
        if unknown:
            raise ValueError(f"not a light setting: {sorted(unknown)}")
        with self._lock:
            merged = self.settings.model_dump(mode="json")
            merged.update(changes)
            checked = type(self.settings).model_validate(merged)
            # Change the shared object in place: the engines keep a reference to it.
            for name in changes:
                setattr(self.settings, name, getattr(checked, name))
            if self.settings_file is not None:
                save_settings(self.settings, self.settings_file)
        if "confirm_over_calls" in changes:
            # A new limit decides again for checks that are waiting; reading is cached, so this is cheap.
            for key, runtime in self._each():
                if runtime.engine.pending_confirm() is not None:
                    self.enqueue(Job("reanalyze", key))
        if not announce:
            return self.settings
        for hook in list(self.settings_hooks):
            try:
                hook(self.settings)
            except Exception:
                log.exception("settings hook failed")
        self._changed()
        return self.settings

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

    def confirm(self, root: str) -> None:
        """Run the part of the check that was held back because of its estimated cost."""
        self.enqueue(Job("confirm", root))

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
                self.last_tick = time.time()
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
        elif job.kind == "confirm":
            report = engine.analyze(confirm=True)
        elif job.kind == "connectors":
            report = self._connector_cycle(runtime, job.text)
        elif job.kind == "notes":
            report = self._notes_cycle(runtime)
        else:
            report = engine.analyze(force=False)
        if engine.cancelled.is_set():
            return
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
        if report.rebuilt and not report.needs_followup and report.pending_confirm is None:
            runtime.db.meta_put("initial_done", "1")

    # mail sources

    def _migrate_mail_settings(self) -> None:
        """Carry the old single label or folder of each mail connector into a source."""
        new = self.settings.model_copy(deep=True)
        if migrate_mail_sources(new):
            self.settings = new
            if self.settings_file is not None:
                save_settings(new, self.settings_file)

    @staticmethod
    def _drop_single_label_mail(runtime: _RootRuntime) -> None:
        """Mail stored by the old single-label setup is dropped once; the sources refetch it."""
        if runtime.db.meta_get("mail_sources_v1"):
            return
        old = [
            r["path"] for r in runtime.db.list_files()
            if any(r["path"].startswith(f"{EXTERNAL_PREFIX}{c}/") for c in MAIL_CONNECTORS)
        ]
        if old:
            runtime.db.delete_files(old)
        for connector_id in MAIL_CONNECTORS:
            runtime.db.meta_delete(f"connector_status:{connector_id}")
        runtime.db.meta_put("mail_sources_v1", "1")

    def _source(self, source_id: str) -> MailSource:
        for source in self.settings.mail_sources:
            if source.id == source_id:
                return source
        raise UserError("메일 소스를 찾지 못했습니다.")

    def source_target(self, source: MailSource) -> str | None:
        """The folder whose analysis receives the source's mail (its own, else the default)."""
        if source.target:
            key = str(Path(source.target).resolve())
            if key in self.runtimes:
                return key
        return self.target_root()

    def _mail_active(self, source: MailSource) -> bool:
        saved = self.settings.connectors.get(source.connector)
        return source.enabled and bool(saved and saved.enabled)

    def _fetch_source(self, runtime: _RootRuntime, source: MailSource) -> dict:
        """Fetch one source into the index and the runtime. Returns its status (counts only)."""
        known = mailsources.known_ids(self.maildb, source)
        since = mailsources.since_for(self.maildb, source)
        query = mailsources.query_for(source, self.settings, self.maildb, since)
        outcome, status = self.connectors.fetch_source(source.connector, query, known)
        if outcome is not None:
            stats = mailsources.apply_fetch(
                runtime.engine, self.maildb, source, outcome, query, self.settings, self.connectors.today()
            )
            status["stats"] = stats
            status["summary"] = mail_summary(stats, query.scope_text())
        mailsources.save_status(runtime.db, source, status)
        return status

    def _rebuild_sources(self, sources: list[MailSource]) -> None:
        """Rebuild the documents of sources from the index and re-check changed folders."""
        changed: set[str] = set()
        for source in sources:
            target = self.source_target(source)
            runtime = self.runtimes.get(target) if target else None
            if runtime is not None and mailsources.rebuild(runtime.engine, self.maildb, source, self.settings):
                changed.add(target)
        for target in changed:
            self.enqueue(Job("reanalyze", target))

    def save_mail_source(self, raw: dict) -> dict:
        """Create or update a mail source from the settings form (validated here, never trusted)."""
        data = dict(raw or {})
        source_id = str(data.pop("id", "") or "")
        sources = list(self.settings.mail_sources)
        previous = next((s for s in sources if s.id == source_id), None) if source_id else None
        if source_id and previous is None:
            raise UserError("메일 소스를 찾지 못했습니다.")
        if previous is None and len(sources) >= MAX_MAIL_SOURCES:
            raise UserError(f"메일 소스는 {MAX_MAIL_SOURCES}개까지 만들 수 있습니다.")
        if previous is None:
            source_id = new_source_id(str(data.get("name") or ""), {s.id for s in sources})
        confirmed = data.pop("all_mail_confirm", False) is True
        data.pop("all_mail_confirmed_at", None)
        if data.get("scope") == "all_mail":
            if confirmed:
                data["all_mail_confirmed_at"] = now_iso()
            elif previous is not None and previous.scope == "all_mail":
                data["all_mail_confirmed_at"] = previous.all_mail_confirmed_at
        try:
            source = MailSource(id=source_id, **data)
        except (ValueError, TypeError) as exc:
            raise UserError(_validation_message(exc)) from exc
        if source.target and str(Path(source.target).resolve()) not in self.runtimes:
            raise UserError("등록된 감시 폴더만 고를 수 있습니다.")
        if previous is not None:
            old_target = self.source_target(previous)
            moved = old_target != self.source_target(source)
            if moved or not source.enabled:
                # The documents follow the source; a disabled source keeps none.
                runtime = self.runtimes.get(old_target) if old_target else None
                if runtime is not None and mailsources.remove_documents(runtime.engine, runtime.db, previous):
                    self.enqueue(Job("reanalyze", old_target))
        new = self.settings.model_copy(deep=True)
        new.mail_sources = [source if s.id == source.id else s for s in sources]
        if previous is None:
            new.mail_sources.append(source)
        if source.enabled:
            saved = new.connectors.get(source.connector) or ConnectorSettings()
            saved.enabled = True
            new.connectors[source.connector] = saved
        self._apply_light(new)
        if previous is not None:
            # Purpose, team, policy or switch changes apply from the index without a fetch.
            self._rebuild_sources([source])
        return source.model_dump()

    def delete_mail_source(self, source_id: str) -> None:
        """Remove one source and only its documents."""
        source = self._source(source_id)
        target = self.source_target(source)
        new = self.settings.model_copy(deep=True)
        new.mail_sources = [s for s in new.mail_sources if s.id != source_id]
        self._apply_light(new)
        runtime = self.runtimes.get(target) if target else None
        mailsources.forget_source(self.maildb, source)
        if runtime is not None and mailsources.remove_documents(runtime.engine, runtime.db, source):
            self.enqueue(Job("reanalyze", target))

    def forget_source_mail(self, source_id: str) -> int:
        """'메일 지우기': delete the source's indexed mail and its documents; keep the source."""
        source = self._source(source_id)
        removed = self.maildb.count(source.id)
        mailsources.forget_source(self.maildb, source)
        self._rebuild_sources([source])
        return removed

    def include_mail(self, uid: str) -> dict:
        """Send one stored mail to the analysis whatever the source's policy says."""
        row = self.maildb.get(uid)
        if row is None:
            raise UserError("메일을 찾지 못했습니다.")
        source = self._source(row["source_id"])
        self.maildb.set_include(uid, True)
        self._rebuild_sources([source])
        return {"ok": True, "source": source.name}

    # address book

    def contact_list(self) -> dict:
        return {"contacts": self.maildb.contacts(), "suggested": self.maildb.top_senders(20)}

    def save_contact(self, raw: dict) -> dict:
        allowed = {"pattern", "name", "team", "role", "note"}
        unknown = set(raw or {}) - allowed
        if unknown:
            raise UserError(f"알 수 없는 항목입니다: {', '.join(sorted(unknown))}")
        try:
            row = self.maildb.put_contact(
                str(raw.get("pattern") or ""), str(raw.get("name") or ""), str(raw.get("team") or ""),
                str(raw.get("role") or "기타"), str(raw.get("note") or ""),
            )
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        self._contacts_changed()
        return row

    def delete_contact(self, pattern: str) -> bool:
        removed = self.maildb.delete_contact(pattern)
        if removed:
            self._contacts_changed()
        return removed

    def import_contacts(self, text: str) -> dict:
        if len(text) > 1_000_000:
            raise UserError("CSV 파일이 너무 큽니다.")
        result = self.maildb.import_csv(text)
        if result["added"]:
            self._contacts_changed()
        return result

    def _contacts_changed(self) -> None:
        """Sources that depend on the address book choose their documents again."""
        affected = [
            s for s in self.settings.mail_sources
            if s.effective_analysis == "address_book"
            or any(x.casefold().startswith(mailsources.TEAM_PREFIX) for x in s.senders + s.exclude_senders)
        ]
        self._rebuild_sources(affected)
        self._changed()

    def preview_mail_source(self, source_id: str) -> dict:
        source = self._source(source_id)
        return self.connectors.preview_source(
            source.connector, mailsources.query_for(source, self.settings)
        )

    def run_mail_source(self, source_id: str) -> bool:
        source = self._source(source_id)
        target = self.source_target(source)
        if target is None:
            return False
        self.enqueue(Job("connectors", target, text=f"source:{source.id}"))
        return True

    def _apply_light(self, new: AppSettings) -> None:
        """Save settings that need no folder restart (connector and source changes).

        The fields change in place: the engines keep a reference to the settings object.
        """
        with self._lock:
            self.settings.mail_sources = new.mail_sources
            self.settings.connectors = new.connectors
            if self.settings_file is not None:
                save_settings(self.settings, self.settings_file)
        self._start_connector_timer()
        self._changed()

    def mail_source_rows(self, connector_id: str) -> list[dict]:
        rows = []
        for source in self.settings.mail_sources:
            if source.connector != connector_id:
                continue
            target = self.source_target(source)
            runtime = self.runtimes.get(target) if target else None
            item = source.model_dump()
            item["purpose_label"] = mailsources.PURPOSE_LABELS[source.purpose]
            item["filter_summary"] = mailsources.filter_summary(source)
            item["status"] = mailsources.read_status(runtime.db if runtime else None, source)
            item["target_root"] = target
            item["analysis_effective"] = source.effective_analysis
            item["analysis_label"] = mailsources.ANALYSIS_LABELS[source.effective_analysis]
            item["indexed"] = self.maildb.count(source.id)
            rows.append(item)
        return rows

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
        """Fetch into one folder: its mail sources, and the other connectors when it is the
        default target. `only` is "", a connector id, or "source:<id>"."""
        engine = runtime.engine
        key = str(engine.root)
        is_default = key == self.target_root()
        if only.startswith("source:"):
            ids: list[str] = []
            sources = [s for s in self.settings.mail_sources if s.id == only[len("source:"):]]
        else:
            if only:
                ids = [only] if only not in MAIL_CONNECTORS and is_default else []
            else:
                ids = [i for i in self.connectors.enabled_ids() if i not in MAIL_CONNECTORS] if is_default else []
            sources = [
                s for s in self.settings.mail_sources
                if (s.connector == only if only else self._mail_active(s)) and (only or s.enabled)
            ]
            sources = [s for s in sources if self.source_target(s) == key]
        engine.progress.begin()
        engine.progress.set_phase("reading")
        engine.progress.set_files(len(ids) + len(sources))
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
        for source in sources:
            label = f"{mailsources.CONNECTOR_LABELS[source.connector]} {source.name}"
            status = self._fetch_source(runtime, source)
            if status["ok"]:
                self.activity.add(f"{label}: {status['summary']}")
            else:
                self.activity.add(f"{label}: {status['message']}", "error")
            self._save_status(runtime, source.connector, status)
            engine.progress.file_done(label)
        return engine.analyze()

    def _notes_cycle(self, runtime: _RootRuntime) -> CycleReport:
        doc = notes_store.notes_document(notes_store.list_notes(runtime.db))
        runtime.engine.progress.begin()
        runtime.engine.set_external("notes", {"notes": doc} if doc is not None else {})
        return runtime.engine.analyze()

    def run_connectors(self, connector_id: str = "") -> bool:
        """Queue one connectors job per folder that receives something."""
        target = self.target_root()
        if target is None:
            return False
        if not connector_id and not self.connectors.enabled_ids():
            return False
        targets: list[str] = []
        if connector_id not in MAIL_CONNECTORS:
            targets.append(target)
        for source in self.settings.mail_sources:
            wanted = source.connector == connector_id if connector_id else self._mail_active(source)
            place = self.source_target(source)
            if wanted and source.enabled and place and place not in targets:
                targets.append(place)
        if not targets:
            return False
        for place in targets:
            self.enqueue(Job("connectors", place, text=connector_id))
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
            if row["id"] in MAIL_CONNECTORS:
                row["sources"] = self.mail_source_rows(row["id"])
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
        """Turn the connector off, forget its secrets, and drop the documents it delivered.

        For a mail connector every source of it is removed with its documents.
        """
        self.connectors.cls(connector_id)
        for source in [s for s in self.settings.mail_sources if s.connector == connector_id]:
            self.delete_mail_source(source.id)
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
        if not report.rebuilt or report.error or report.needs_followup or report.pending_confirm:
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
        waiting = self._new_confirm_wait(runtime)
        if waiting is not None:
            messages.append(waiting)
            self.inbox.add(f"{waiting[0]}: {waiting[1]}", "attention")
        if self.settings.notify and quiet_now(self.settings, self.clock()):
            # Do not disturb: the inbox above keeps everything, no toast is shown.
            log.info("quiet hours: %d notifications kept in the inbox only", len(messages))
        elif self.settings.notify:
            if summary is not None:
                messages = [summary] + [m for m in messages if m[0] == "확인이 필요합니다"][:1]
            elif waiting is not None:
                messages = [waiting] + [m for m in messages if m is not waiting]
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

    def _new_confirm_wait(self, runtime: _RootRuntime) -> tuple[str, str] | None:
        """The toast for a check that has just started waiting for confirmation, once per wait.

        A periodic or file-change check that stops at the same gate again adds nothing; the mark
        is cleared when the folder is no longer waiting.
        """
        waiting = runtime.engine.pending_confirm()
        if waiting is None:
            runtime.db.meta_delete("confirm_notified")
            return None
        if runtime.db.meta_get("confirm_notified"):
            return None
        runtime.db.meta_put("confirm_notified", "1")
        return (
            "점검 확인이 필요합니다",
            f"예상 호출 약 {waiting['estimated_calls']}회. 앱을 열어 계속할지 선택해 주세요.",
        )

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
            counts = {"ok": 0, "error": 0, "unsupported": 0}
            counts.update(runtime.db.file_status_counts())
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
                    "watch_alive": all(
                        part.is_alive() for part in (runtime.watcher, runtime.debouncer, runtime.timer)
                        if part is not None
                    ),
                    "pending_events": runtime.debouncer.pending_count() if runtime.debouncer else 0,
                    "progress": runtime.engine.progress.snapshot(),
                    "completion": runtime.engine.completion(),
                    "pending_confirm": runtime.engine.pending_confirm(),
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
        alive = all(f.get("watch_alive", True) for f in folders)
        return {
            "mode": "realtime" if modes & {"native", "polling"} else "periodic",
            "alive": alive and (self._worker is None or self._worker.is_alive()),
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
            failure = folder["failure"]
            if failure and failure.get("partial"):
                return {"state": "partial", "label": PARTIAL_LABEL, "detail": failure["message"]}
        for folder in folders:
            if folder["failure"]:
                return {
                    "state": "retrying",
                    "label": "일부만 점검했고 자동으로 다시 시도합니다.",
                    "detail": folder["failure"]["message"],
                }
        for folder in folders:
            waiting = folder.get("pending_confirm")
            if waiting:
                return {
                    "state": "awaiting_confirm",
                    "label": "확인 대기",
                    "detail": f"예상 약 {waiting['estimated_calls']}회 호출 (기준 {waiting['limit']}회)",
                }
        records = [f["completion"] for f in folders]
        if any(r is None for r in records):
            return {"state": "incomplete", "label": "아직 점검을 끝내지 못했습니다.", "detail": ""}
        if any(r["provider_id"] != self.bundle.provider_id for r in records) or any(
            r.get("cost_profile", "precise") != self.settings.cost_profile for r in records
        ):
            return {"state": "stale", "label": "판정 방식이 바뀌어 다시 점검해야 합니다.", "detail": ""}
        files = sum(r["files_ok"] for r in records)
        topics = sum(r["topics"] for r in records)
        latest = max(r["at"] for r in records)
        local = sum(r.get("local_answers", 0) for r in records)
        if all(r["verified"] for r in records):
            extra = f" / 로컬 선거름 {local}건" if local else ""
            return {
                "state": "verified",
                "label": "검사 완료",
                "detail": f"{self.bundle.provider_id} / 파일 {files}개, 결정 {topics}건{extra} / {latest}",
            }
        return {
            "state": "demo",
            "label": "시연용 모드, 실제 검사가 아닙니다.",
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
        by_prefix = {
            f"{EXTERNAL_PREFIX}{mailsources.source_key(s)}/": s for s in self.settings.mail_sources
        }
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
                source = next(
                    (s for p, s in by_prefix.items() if str(row["source_doc"] or "").startswith(p)), None
                )
                item["mail_source"] = source.name if source else ""
                item["mail_connector"] = mailsources.CONNECTOR_LABELS[source.connector] if source else ""
                item["team"] = source.team if source else ""
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
            # Do not wait for (and pay for) the rest of a check of the folder being removed.
            runtime = self.runtimes.get(str(Path(path).resolve()))
            if runtime is not None:
                runtime.engine.cancel()
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
                counts = Counter(runtime.db.file_status_counts(include_external=False))
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
        self.maildb.purge()
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


SOURCE_FIELD_LABELS = {
    "name": "이름", "connector": "연결", "purpose": "용도", "team": "팀", "target": "프로젝트 폴더",
    "labels": "라벨 또는 폴더", "senders": "보낸 사람", "subject_keywords": "제목 키워드",
    "exclude_senders": "제외할 보낸 사람", "exclude_subject_keywords": "제외할 제목 키워드",
    "gmail_query": "Gmail 검색식", "days_back": "과거 일수", "max_messages": "최대 메일 수",
}


def _validation_message(exc: Exception) -> str:
    """A Korean sentence for the first problem of a rejected form."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    for error in errors():
        if error.get("type") == "value_error":
            return str(error.get("msg", "")).removeprefix("Value error, ")
        field = str((error.get("loc") or ("",))[0])
        if error.get("type") == "extra_forbidden":
            return f"알 수 없는 항목입니다: {field}"
        return f"'{SOURCE_FIELD_LABELS.get(field, field)}' 값을 확인하세요."
    return "입력값을 확인하세요."


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
