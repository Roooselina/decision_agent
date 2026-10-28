"""Background mail refresh: keeps the local mail index current while the app runs.

The poller never calls a model. Each check asks the provider cheaply whether anything was
added (Gmail history, Outlook delta, IMAP UIDNEXT); only then it lists the source again, reads
the new bodies into the index and rebuilds the source's documents. When a source with analysis
on gets new documents, one normal analysis is queued after a quiet period through the usual
job queue, which applies the cost profile, verdict reuse and the confirm gate.

Logs carry counts only, never subjects, bodies or addresses.
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from decision_agent.connectors.base import ConnectorError, LoginExpired, RateLimited, mail_summary
from decision_agent.desktop import mailsources
from decision_agent.desktop.settings import MailSource

log = logging.getLogger(__name__)

# Longest wait between checks of a failing source.
MAX_BACKOFF_SECONDS = 900.0
STATE_KEY = "poll_state:{}"


@dataclass
class SourcePoll:
    """What the poller knows about one source (in memory; shown in the mail page)."""

    next_due: float = 0.0
    failures: int = 0
    checked_at: str = ""
    new: int = 0
    ok: bool = True
    stopped: bool = False
    message: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


class MailPoller:
    """One thread that checks every active mail source on its own schedule.

    app is the DecisionAgentApp; the poller uses its settings, mail index, connectors, folder
    runtimes and job queue. clock returns seconds (time.monotonic in the app).
    """

    def __init__(self, app, clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self.clock = clock
        self.polls: dict[str, SourcePoll] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mail-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def trigger(self, source_id: str = "") -> None:
        """Check now ("지금 확인"): one source, or all. A stopped source is retried too."""
        for sid, poll in self.polls.items():
            if not source_id or sid == source_id:
                poll.next_due = 0.0
                poll.stopped = False
                poll.failures = 0
        self._wake.set()

    # scheduling

    def interval(self) -> float:
        return float(self.app.settings.mail_poll_seconds)

    def active_sources(self) -> list[MailSource]:
        return [s for s in self.app.settings.mail_sources if self.app._mail_active(s)]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception:
                log.exception("mail poller cycle failed")
            waits = [p.next_due - self.clock() for p in self.polls.values() if not p.stopped]
            wait = max(1.0, min(waits)) if waits else self.interval()
            self._wake.wait(min(wait, self.interval()))
            self._wake.clear()

    def run_due(self) -> int:
        """Check every source that is due now. Returns how many were checked."""
        sources = self.active_sources()
        current = {s.id for s in sources}
        for sid in [sid for sid in self.polls if sid not in current]:
            del self.polls[sid]
        checked = 0
        for source in sources:
            if self._stop.is_set():
                break
            poll = self.polls.setdefault(source.id, SourcePoll())
            if poll.stopped or poll.next_due > self.clock():
                continue
            self.check(source)
            checked += 1
        return checked

    # one check

    def check(self, source: MailSource) -> SourcePoll:
        poll = self.polls.setdefault(source.id, SourcePoll())
        if not poll.lock.acquire(blocking=False):
            return poll  # a check of this source is already running
        try:
            self._check(source, poll)
        except LoginExpired as exc:
            poll.ok, poll.stopped, poll.message = False, True, str(exc)
            log.info("mail poller: login expired for one source, polling stopped")
        except RateLimited as exc:
            self._failed(poll, str(exc), exc.retry_after)
        except ConnectorError as exc:
            self._failed(poll, str(exc))
        except Exception as exc:
            log.exception("mail poller: unexpected failure")
            self._failed(poll, f"예기치 않은 오류: {exc}"[:200])
        finally:
            poll.lock.release()
        self.app._changed()
        return poll

    def _failed(self, poll: SourcePoll, message: str, retry_after: float = 0.0) -> None:
        poll.failures += 1
        poll.ok = False
        poll.message = message
        wait = min(MAX_BACKOFF_SECONDS, self.interval() * (2 ** poll.failures))
        poll.next_due = self.clock() + max(wait, retry_after)

    def _check(self, source: MailSource, poll: SourcePoll) -> None:
        app = self.app
        maildb = app.maildb
        target = app.source_target(source)
        runtime = app.runtimes.get(target) if target else None
        if runtime is None:
            poll.ok, poll.message = False, "메일을 받을 감시 폴더가 없습니다."
            poll.next_due = self.clock() + self.interval()
            return
        connector = app.connectors.instance(source.connector)
        problems = connector.problems()
        if problems:
            raise LoginExpired(problems[0])
        state_key = STATE_KEY.format(source.id)
        state = json.loads(maildb.meta_get(state_key) or "{}")
        query = mailsources.query_for(source, app.settings, maildb)
        changed, new_state, threads = connector.poll_changes(query, state)
        new = 0
        docs_changed = False
        if changed:
            known = mailsources.known_ids(maildb, source)
            last = maildb.meta_get(f"last_fetch:{source.id}")
            since = None
            if last and state:
                since = (datetime.date.fromisoformat(last) - datetime.timedelta(days=1)).isoformat()
            query = mailsources.query_for(source, app.settings, maildb, since)
            mine = maildb.thread_keys(source.id)
            outcome = connector.fetch_source(
                query, app.connectors.today(), known, {t for t in threads if t in mine}
            )
            if source.connector not in app.connectors.accounts:
                app.connectors.remember_account(source.connector, connector)
            account = app.connectors.accounts.get(source.connector, {}).get("account", "")
            stats = mailsources.apply_fetch(
                runtime.engine, maildb, source, outcome, query, app.settings, app.connectors.today(),
                accounts=[account] if account else [], unread=bool(last),
            )
            new = stats.get("new", 0)
            docs_changed = bool(stats.get("docs_changed"))
            status = {
                "at": datetime.datetime.now().replace(microsecond=0).isoformat(), "ok": True, "message": "",
                "summary": mail_summary(stats, query.scope_text()), "stats": stats,
            }
            mailsources.save_status(runtime.db, source, status)
        maildb.meta_put(state_key, json.dumps(new_state))
        poll.ok, poll.message, poll.failures = True, "", 0
        poll.new = new
        poll.checked_at = datetime.datetime.now().strftime("%H:%M")
        poll.next_due = self.clock() + self.interval()
        log.info("mail poller: one source checked, %d new", new)
        if docs_changed and source.effective_analysis != "off":
            app.schedule_mail_analysis(target)
        if new and wants_toast(source):
            app.mail_arrived(source, new)

    # status for the pages

    def status(self) -> dict:
        """One line for the home card and the mail page, plus per-source rows."""
        polls = [(s, self.polls.get(s.id)) for s in self.active_sources()]
        rows = [
            {"id": s.id, "name": s.name, "checked_at": p.checked_at, "new": p.new, "ok": p.ok,
             "stopped": p.stopped, "message": p.message}
            for s, p in polls if p is not None
        ]
        checked = sorted(r["checked_at"] for r in rows if r["checked_at"])
        failing = [r for r in rows if not r["ok"]]
        if not polls:
            line = ""
        elif any(r["stopped"] for r in rows):
            line = "확인 멈춤 · 다시 로그인이 필요합니다"
        elif failing and not checked:
            line = "확인 실패 · 다시 시도 중"
        elif checked:
            line = f"마지막 확인 {checked[-1]}"
            new = sum(r["new"] for r in rows)
            if new:
                line += f" · 새 메일 {new}통"
            if failing:
                line += " · 일부 확인 실패, 다시 시도 중"
        else:
            line = "확인 대기 중"
        return {"line": line, "running": self.running, "interval": self.interval(), "sources": rows}


def wants_toast(source: MailSource) -> bool:
    """A toast for new mail: on by default only for sources whose mail is analysed."""
    if source.notify == "on":
        return True
    if source.notify == "off":
        return False
    return source.effective_analysis != "off"
