from __future__ import annotations

import base64
import datetime
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from decision_agent.connectors.base import (
    NO_SCOPE,
    NO_SUBJECT,
    CalendarEvent,
    Connector,
    ConnectorError,
    FetchOutcome,
    MailMessage,
    OptionSpec,
    SecretSpec,
    calendar_documents,
    check_response,
    clock_of,
    mail_summary,
    new_mail_stats,
    preview_summary,
    strip_html,
)
from decision_agent.connectors.http import HttpError, HttpResponse
from decision_agent.connectors.mailfilter import MailQuery, collect_mail, gmail_search
from decision_agent.connectors.oauth import OAuthApp

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_PAGES = 10
# Threads expanded in one fetch (one threads.get each).
MAX_THREADS = 50
# Waits after HTTP 429 before the next attempt; SLEEP is replaced in tests.
BACKOFF = (1.0, 2.0, 4.0, 8.0)
SLEEP = time.sleep
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Gmail system labels are addressed by id (INBOX, STARRED, ...), not by a display name.
SYSTEM_LABELS = frozenset({
    "INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "UNREAD", "CHAT",
    "CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
})

GOOGLE_GUIDE = (
    "Google Cloud 콘솔에서 프로젝트를 만들고 해당 API(Calendar 또는 Gmail)를 사용 설정합니다.",
    "OAuth 동의 화면을 설정하고 본인 계정을 테스트 사용자로 추가합니다.",
    "사용자 인증 정보에서 'OAuth 클라이언트 ID'를 '데스크톱 앱' 유형으로 만듭니다.",
    "발급된 클라이언트 ID(와 있으면 보안 비밀)를 아래에 입력하고 '계정 연결'을 누릅니다.",
)


class _GoogleBase(Connector):
    uses_oauth = True
    scope = ""
    secrets = (
        SecretSpec(
            "client_secret",
            "클라이언트 보안 비밀",
            "데스크톱 앱 유형은 보안 비밀이 함께 발급됩니다. 있으면 입력하세요.",
            required=False,
        ),
    )

    def oauth_app(self) -> OAuthApp:
        return OAuthApp(
            auth_url=AUTH_URL,
            token_url=TOKEN_URL,
            scopes=(self.scope,),
            client_id=self.text_option("client_id"),
            client_secret=self.ctx.secrets.get(self.secret_key("client_secret")) or "",
            redirect_host="127.0.0.1",
            extra_params={"access_type": "offline", "prompt": "consent"},
        )

    def _headers(self) -> dict[str, str]:
        token = self.ctx.oauth.access_token(self.id, self.oauth_app())
        return {"Authorization": f"Bearer {token}"}

    def _request(self, url: str, params: dict | None = None) -> HttpResponse:
        """GET with a short backoff on HTTP 429 (Retry-After is honoured, up to 30 seconds)."""
        for attempt in range(len(BACKOFF) + 1):
            try:
                response = self.ctx.http.request("GET", url, headers=self._headers(), params=params)
            except HttpError as exc:
                raise ConnectorError(str(exc)) from exc
            if response.status != 429 or attempt == len(BACKOFF):
                return response
            wait = BACKOFF[attempt]
            retry_after = {k.lower(): v for k, v in response.headers.items()}.get("retry-after", "")
            if retry_after.isdigit():
                wait = min(30.0, float(retry_after))
            SLEEP(wait)
        return response

    def _get(self, url: str, params: dict | None = None) -> dict:
        return check_response(self._request(url, params), self.label).json()


class GoogleCalendar(_GoogleBase):
    id = "google_calendar"
    label = "Google 캘린더"
    description = "캘린더 일정을 일정 산출물로, 회의 설명란을 회의록으로 읽습니다."
    reads = "선택한 캘린더의 일정 제목, 날짜, 설명, 장소만 읽습니다. 일정을 만들거나 고치지 않습니다."
    guide = GOOGLE_GUIDE
    scope = CALENDAR_SCOPE
    options = (
        OptionSpec("client_id", "클라이언트 ID", required=True),
        OptionSpec("calendar_ids", "캘린더 (한 줄에 하나)", "list", ["primary"], "기본 캘린더는 primary"),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("days_ahead", "미래 일수", "number", 60),
    )

    def fetch(self, today: datetime.date) -> FetchOutcome:
        back = self.int_option("days_back", 14, 0, 365)
        ahead = self.int_option("days_ahead", 60, 0, 365)
        start = datetime.datetime.combine(today - datetime.timedelta(days=back), datetime.time.min)
        end = datetime.datetime.combine(today + datetime.timedelta(days=ahead), datetime.time.max)
        events: list[CalendarEvent] = []
        for calendar_id in self.list_option("calendar_ids") or ["primary"]:
            url = (
                "https://www.googleapis.com/calendar/v3/calendars/"
                f"{urllib.parse.quote(calendar_id, safe='')}/events"
            )
            params = {
                "timeMin": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeMax": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 250,
            }
            for _ in range(MAX_PAGES):
                body = self._get(url, params)
                for item in body.get("items", []):
                    start_info = item.get("start", {})
                    raw = start_info.get("dateTime") or start_info.get("date") or ""
                    events.append(
                        CalendarEvent(
                            summary=item.get("summary", ""),
                            date=raw[:10],
                            time=clock_of(raw),
                            description=strip_html(item.get("description", "")),
                            location=item.get("location", ""),
                            cancelled=item.get("status") == "cancelled",
                        )
                    )
                token = body.get("nextPageToken")
                if not token:
                    break
                params = {**params, "pageToken": token}
        docs = calendar_documents(events)
        return FetchOutcome(docs, f"일정 {len(events)}건")


class Gmail(_GoogleBase):
    id = "gmail"
    label = "Gmail"
    description = "메일 소스에서 정한 라벨, 보낸 사람, 제목 키워드에 맞는 메일을 읽습니다."
    reads = (
        "메일 소스에서 정한 범위에 맞는 최근 메일의 제목, 날짜, 보낸 사람, 본문 텍스트만 읽습니다. "
        "Google의 읽기 권한은 메일 전체 범위이지만 이 프로그램은 소스에서 정한 범위만 조회합니다. "
        "메일을 보내거나 지우거나 읽음 표시를 바꾸지 않습니다."
    )
    guide = GOOGLE_GUIDE
    scope = GMAIL_SCOPE
    options = (
        OptionSpec("client_id", "클라이언트 ID", required=True),
        OptionSpec("label", "읽을 라벨 이름", help="이전 버전 설정입니다. 읽을 범위는 메일 소스에서 정합니다."),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("max_messages", "최대 메일 수", "number", 200),
    )

    def __init__(self, options, ctx) -> None:
        super().__init__(options, ctx)
        # Filled while talking to Gmail; shown in the settings card only, never logged.
        self.label_names: list[str] | None = None
        # Label id -> name for user labels, so stored mail carries names, not ids.
        self.label_map: dict[str, str] = {}

    @staticmethod
    def _decode(data: str) -> str:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")

    def _part_data(self, part: dict, msg_id: str) -> str | None:
        """The part's text. Large parts carry only an attachmentId and are fetched separately."""
        body = part.get("body") or {}
        if body.get("data"):
            return self._decode(body["data"])
        if body.get("attachmentId") and msg_id:
            found = self._get(f"{API}/messages/{msg_id}/attachments/{body['attachmentId']}")
            data = found.get("data")
            return self._decode(data) if data else None
        return None

    def _body_text(self, part: dict, msg_id: str = "") -> str:
        mime = part.get("mimeType", "")
        if mime == "text/plain" and not part.get("filename"):
            data = self._part_data(part, msg_id)
            if data:
                return data
        plain = ""
        html_text = ""
        for child in part.get("parts", []) or []:
            found = self._body_text(child, msg_id)
            if found and child.get("mimeType", "").startswith("text/html"):
                html_text = html_text or found
            elif found:
                plain = plain or found
        if plain:
            return plain
        if mime == "text/html" and not part.get("filename"):
            data = self._part_data(part, msg_id)
            if data:
                return strip_html(data)
        return strip_html(html_text) if html_text else ""

    # labels and account

    def user_labels(self) -> list[dict] | None:
        """All labels, or None when the current grant does not allow listing them."""
        response = self._request(f"{API}/labels")
        if response.status == 403:
            return None
        labels = check_response(response, self.label).json().get("labels", []) or []
        self.label_map = {
            str(item.get("id")): str(item.get("name")) for item in labels
            if item.get("type") == "user" and item.get("id") and item.get("name")
        }
        self.label_names = sorted(
            item.get("name", "") for item in labels if item.get("type") == "user" and item.get("name")
        )
        return labels

    def resolve_label(self, name: str, labels: list[dict]) -> str:
        """Label id for a name: case-insensitive, trimmed, nested labels by their full A/B name."""
        wanted = name.strip()
        if wanted.upper() in SYSTEM_LABELS:
            return wanted.upper()
        for item in labels:
            if item.get("id") == wanted or item.get("name", "").strip().casefold() == wanted.casefold():
                return item["id"]
        known = ", ".join((self.label_names or [])[:10]) or "없음"
        raise ConnectorError(f"라벨 '{wanted}'을(를) 찾지 못했습니다. 있는 라벨: {known}")

    def account_info(self) -> dict:
        profile = self._get(f"{API}/profile")
        if self.label_names is None:
            self.user_labels()
        return {"account": str(profile.get("emailAddress") or ""), "labels": list(self.label_names or [])}

    # listing and reading

    def _list_ids(self, params: dict, limit: int) -> list[dict]:
        refs: list[dict] = []
        token = None
        while len(refs) < limit:
            page = {**params, "maxResults": min(limit - len(refs), 100)}
            if token:
                page["pageToken"] = token
            listing = self._get(f"{API}/messages", page)
            refs.extend(listing.get("messages", []) or [])
            token = listing.get("nextPageToken")
            if not token:
                break
        return refs[:limit]

    @staticmethod
    def _headers_of(message: dict) -> dict[str, str]:
        return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}

    @staticmethod
    def _date_of(message: dict, today: datetime.date) -> str:
        stamp = int(message.get("internalDate", "0") or 0) / 1000.0
        return datetime.datetime.fromtimestamp(stamp).date().isoformat() if stamp else today.isoformat()

    @staticmethod
    def _time_of(message: dict) -> str:
        stamp = int(message.get("internalDate", "0") or 0) / 1000.0
        return datetime.datetime.fromtimestamp(stamp).strftime("%H:%M") if stamp else ""

    def mail_message(self, message: dict, today: datetime.date) -> MailMessage:
        headers = self._headers_of(message)
        msg_id = str(message.get("id", ""))
        label_ids = [str(i) for i in message.get("labelIds", []) or []]
        return MailMessage(
            msg_id=msg_id,
            date=self._date_of(message, today),
            subject=headers.get("subject", NO_SUBJECT),
            sender=headers.get("from", ""),
            body=self._body_text(message.get("payload", {}), msg_id),
            thread_id=str(message.get("threadId", "")),
            labels=[self.label_map.get(i, i) for i in label_ids],
            time=self._time_of(message),
            to=headers.get("to", ""),
            header_id=headers.get("message-id", "").strip(),
            in_reply_to=headers.get("in-reply-to", "").strip(),
            references=headers.get("references", "").split(),
            direction="sent" if "SENT" in label_ids else "received",
        )


    def options_query(self) -> MailQuery:
        """The single-label setup of older versions, as a mail query (kept for direct use)."""
        label = self.text_option("label")
        if not label:
            raise ConnectorError(NO_SCOPE)
        return MailQuery(
            source_id="", name=label, labels=[label] if label else [],
            days_back=self.int_option("days_back", 14, 1, 365),
            max_messages=self.int_option("max_messages", 200, 1, 1000), prefilter=False,
        )

    def _refs(self, query: MailQuery) -> list[dict]:
        """Message refs for the query: one list per label (any of), unioned by id, newest first."""
        search = gmail_search(query)
        if not query.labels:
            return self._list_ids({"q": search}, query.max_messages)
        labels = self.user_labels()
        seen: dict[str, dict] = {}
        for name in query.labels:
            if labels is None:
                # Listing labels was refused: fall back to the label name in the search query.
                params = {"q": f'label:"{name}" {search}'}
            else:
                params = {"labelIds": self.resolve_label(name, labels), "q": search}
            for ref in self._list_ids(params, query.max_messages):
                seen.setdefault(ref["id"], ref)
        return list(seen.values())[: query.max_messages]

    def fetch_source(
        self,
        query: MailQuery,
        today: datetime.date,
        known: set[str] | None = None,
        threads: set[str] | None = None,
    ) -> FetchOutcome:
        """Fetch one source. Mails whose id is in `known` are already stored: Gmail messages
        never change, so their bodies are not requested again.

        With query.include_sent, the other messages of each thread with a new matching mail
        (and of the thread ids in `threads`) are read too, so my own replies, which usually do
        not carry the source's label, are part of the conversation.
        """
        known = known or set()
        refs = self._refs(query)
        stats = new_mail_stats()
        stats["matched"] = len(refs)
        fresh = [ref["id"] for ref in refs if ref["id"] not in known]
        stats["known"] = len(refs) - len(fresh)
        fresh = query.capped(fresh, stats)

        def read(msg_id: str) -> MailMessage:
            message = self._get(f"{API}/messages/{msg_id}", {"format": "full"})
            message.setdefault("id", msg_id)
            return self.mail_message(message, today)

        workers = max(1, min(query.workers, len(fresh) or 1))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gmail") as pool:
            messages = list(pool.map(read, fresh))
        if query.include_sent:
            fresh_set = set(fresh)
            wanted = {str(r["threadId"]) for r in refs if r["id"] in fresh_set and r.get("threadId")}
            wanted |= set(threads or ())
            listed = {r["id"] for r in refs} | known
            extra = self.thread_messages(sorted(wanted), listed, today)
            messages.extend(extra)
            stats["thread"] = len(extra)
        docs = collect_mail([m for m in messages if not m.via_thread], query, stats)
        return FetchOutcome(
            docs, mail_summary(stats, query.scope_text()), stats, seen_ids=[r["id"] for r in refs],
            messages=messages,
        )

    def thread_messages(self, thread_ids: list[str], skip: set[str], today: datetime.date) -> list[MailMessage]:
        """Messages of the threads that are not in skip (one threads.get per thread)."""
        found: list[MailMessage] = []
        for thread_id in thread_ids[:MAX_THREADS]:
            data = self._get(f"{API}/threads/{thread_id}", {"format": "full"})
            for raw in data.get("messages", []) or []:
                if str(raw.get("id", "")) in skip or "DRAFT" in (raw.get("labelIds") or []):
                    continue
                message = self.mail_message(raw, today)
                message.via_thread = True
                found.append(message)
        return found

    def poll_changes(self, query: MailQuery, state: dict) -> tuple[bool, dict, set[str]]:
        """(changed, new state, thread ids with added mail) from users.history.list.

        The first call only records the current historyId (the caller then lists normally).
        An expired historyId (404) asks for one bounded re-list.
        """
        start = state.get("history_id")
        if not start:
            profile = self._get(f"{API}/profile")
            return True, {"history_id": str(profile.get("historyId") or "")}, set()
        threads: set[str] = set()
        added = 0
        newest = start
        token = None
        for _ in range(MAX_PAGES):
            params = {"startHistoryId": start, "historyTypes": "messageAdded", "maxResults": 500}
            if token:
                params["pageToken"] = token
            response = self._request(f"{API}/history", params)
            if response.status == 404:
                # The historyId is too old: start again from the current one and list once.
                profile = self._get(f"{API}/profile")
                return True, {"history_id": str(profile.get("historyId") or "")}, set()
            page = check_response(response, self.label).json()
            for entry in page.get("history", []) or []:
                for item in entry.get("messagesAdded", []) or []:
                    message = item.get("message") or {}
                    if "DRAFT" in (message.get("labelIds") or []):
                        continue
                    added += 1
                    if message.get("threadId"):
                        threads.add(str(message["threadId"]))
            newest = str(page.get("historyId") or newest)
            token = page.get("nextPageToken")
            if not token:
                break
        return added > 0, {"history_id": newest}, threads

    def fetch(self, today: datetime.date) -> FetchOutcome:
        return self.fetch_source(self.options_query(), today)

    def preview_source(self, query: MailQuery, today: datetime.date, limit: int = 10) -> dict:
        refs = self._refs(query)
        rows = []
        for ref in refs[:limit]:
            message = self._get(
                f"{API}/messages/{ref['id']}",
                {"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            )
            headers = self._headers_of(message)
            rows.append(
                {
                    "date": self._date_of(message, today),
                    "subject": headers.get("subject", NO_SUBJECT),
                    "sender": headers.get("from", ""),
                }
            )
        stats = new_mail_stats()
        stats["matched"] = len(refs)
        return {"rows": rows, "stats": stats, "summary": preview_summary(stats, query.scope_text())}

    def preview(self, today: datetime.date, limit: int = 10) -> dict:
        return self.preview_source(self.options_query(), today, limit)
