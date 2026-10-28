from __future__ import annotations

import datetime
import urllib.parse

from decision_agent.connectors.base import (
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
    mail_document,
    mail_summary,
    new_mail_stats,
    preview_summary,
    strip_html,
)
from decision_agent.connectors.http import HttpError
from decision_agent.connectors.oauth import OAuthApp

GRAPH = "https://graph.microsoft.com/v1.0"
MAX_PAGES = 10

MICROSOFT_GUIDE = (
    "Azure 포털(Microsoft Entra)의 '앱 등록'에서 새 앱을 만듭니다.",
    "지원 계정 유형을 선택하고, 플랫폼은 '모바일 및 데스크톱 애플리케이션'에 리디렉션 URI http://localhost 를 추가합니다.",
    "API 권한에서 위임된 권한 Calendars.Read 또는 Mail.Read 를 추가합니다 (읽기 전용).",
    "애플리케이션(클라이언트) ID를 아래에 입력하고 '계정 연결'을 누릅니다.",
)


class _MicrosoftBase(Connector):
    uses_oauth = True
    scopes: tuple[str, ...] = ()
    secrets: tuple[SecretSpec, ...] = ()

    def oauth_app(self) -> OAuthApp:
        tenant = self.text_option("tenant") or "common"
        base = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0"
        return OAuthApp(
            auth_url=f"{base}/authorize",
            token_url=f"{base}/token",
            scopes=(*self.scopes, "offline_access"),
            client_id=self.text_option("client_id"),
            redirect_host="localhost",
        )

    def _get(self, url: str, params: dict | None = None, prefer: str = "") -> dict:
        if not url.startswith("https://graph.microsoft.com/"):
            # A next-page link must never send the access token to another host.
            raise ConnectorError("허용되지 않은 주소입니다.")
        token = self.ctx.oauth.access_token(self.id, self.oauth_app())
        headers = {"Authorization": f"Bearer {token}"}
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = self.ctx.http.request("GET", url, headers=headers, params=params)
        except HttpError as exc:
            raise ConnectorError(str(exc)) from exc
        return check_response(response, self.label).json()


class OutlookCalendar(_MicrosoftBase):
    id = "outlook_calendar"
    label = "Outlook 캘린더"
    description = "Outlook(Microsoft 365) 일정을 일정 산출물로, 회의 본문을 회의록으로 읽습니다."
    reads = "일정 제목, 날짜, 본문 텍스트, 장소만 읽습니다 (Calendars.Read). 일정을 만들거나 고치지 않습니다."
    guide = MICROSOFT_GUIDE
    scopes = ("Calendars.Read",)
    options = (
        OptionSpec("client_id", "애플리케이션(클라이언트) ID", required=True),
        OptionSpec("tenant", "테넌트", default="common", help="개인과 회사 계정 모두면 common"),
        OptionSpec("timezone", "시간대", default="Asia/Seoul"),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("days_ahead", "미래 일수", "number", 60),
    )

    def fetch(self, today: datetime.date) -> FetchOutcome:
        back = self.int_option("days_back", 14, 0, 365)
        ahead = self.int_option("days_ahead", 60, 0, 365)
        start = today - datetime.timedelta(days=back)
        end = today + datetime.timedelta(days=ahead)
        params: dict | None = {
            "startDateTime": f"{start.isoformat()}T00:00:00",
            "endDateTime": f"{end.isoformat()}T23:59:59",
            "$top": 100,
            "$select": "subject,start,location,body,isCancelled",
            "$orderby": "start/dateTime",
        }
        prefer = f'outlook.timezone="{self.text_option("timezone") or "Asia/Seoul"}", outlook.body-content-type="text"'
        url = f"{GRAPH}/me/calendarView"
        events: list[CalendarEvent] = []
        for _ in range(MAX_PAGES):
            body = self._get(url, params, prefer)
            for item in body.get("value", []):
                content = (item.get("body") or {}).get("content", "")
                if (item.get("body") or {}).get("contentType") == "html":
                    content = strip_html(content)
                events.append(
                    CalendarEvent(
                        summary=item.get("subject", ""),
                        date=((item.get("start") or {}).get("dateTime") or "")[:10],
                        time=clock_of((item.get("start") or {}).get("dateTime") or ""),
                        description=content.strip(),
                        location=(item.get("location") or {}).get("displayName", ""),
                        cancelled=bool(item.get("isCancelled")),
                    )
                )
            url = body.get("@odata.nextLink") or ""
            params = None
            if not url or not url.startswith(GRAPH):
                break
        return FetchOutcome(calendar_documents(events), f"일정 {len(events)}건")


class OutlookMail(_MicrosoftBase):
    id = "outlook_mail"
    label = "Outlook 메일"
    description = "지정한 메일 폴더의 본문에서 결정 문장을 읽습니다."
    reads = (
        "지정한 폴더의 최근 메일 제목, 날짜, 본문 텍스트만 읽습니다 (Mail.Read). "
        "Microsoft의 읽기 권한은 메일 전체 범위이지만 이 프로그램은 지정한 폴더만 조회합니다. "
        "메일을 보내거나 지우거나 읽음 표시를 바꾸지 않습니다."
    )
    guide = MICROSOFT_GUIDE
    scopes = ("Mail.Read",)
    options = (
        OptionSpec("client_id", "애플리케이션(클라이언트) ID", required=True),
        OptionSpec("tenant", "테넌트", default="common"),
        OptionSpec("folder", "읽을 메일 폴더 이름", help="예: 회의록. 받은 편지함 하위 폴더도 찾습니다.", required=True),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("max_messages", "최대 메일 수", "number", 200),
    )

    def _folder_id(self, name: str) -> str:
        wanted = name.strip().lower()
        well_known = {"inbox": "inbox", "받은 편지함": "inbox", "archive": "archive", "보관": "archive"}
        if wanted in well_known:
            return well_known[wanted]
        for url in (f"{GRAPH}/me/mailFolders", f"{GRAPH}/me/mailFolders/inbox/childFolders"):
            body = self._get(url, {"$top": 100, "$select": "id,displayName"})
            for folder in body.get("value", []):
                if folder.get("displayName", "").strip().lower() == wanted:
                    return folder["id"]
        raise ConnectorError(f"메일 폴더 '{name}'을(를) 찾지 못했습니다. 이름을 확인하세요.")

    def _items(self, today: datetime.date, select: str, limit: int) -> list[dict]:
        folder = self._folder_id(self.text_option("folder"))
        days = self.int_option("days_back", 14, 1, 365)
        since = (today - datetime.timedelta(days=days)).isoformat()
        prefer = 'outlook.body-content-type="text"'
        url: str | None = f"{GRAPH}/me/mailFolders/{urllib.parse.quote(folder, safe='')}/messages"
        params: dict | None = {
            "$top": min(limit, 100),
            "$select": select,
            "$filter": f"receivedDateTime ge {since}T00:00:00Z",
            "$orderby": "receivedDateTime desc",
        }
        items: list[dict] = []
        while url and len(items) < limit:
            body = self._get(url, params, prefer)
            items.extend(body.get("value", []))
            url, params = body.get("@odata.nextLink"), None
        return items[:limit]

    def _scope(self) -> str:
        return f"폴더: {self.text_option('folder')}, 최근 {self.int_option('days_back', 14, 1, 365)}일"

    @staticmethod
    def mail_message(item: dict, today: datetime.date) -> MailMessage:
        content = (item.get("body") or {}).get("content", "")
        if (item.get("body") or {}).get("contentType") == "html":
            content = strip_html(content)
        address = (item.get("from") or {}).get("emailAddress") or {}
        subject = item.get("subject") or NO_SUBJECT
        return MailMessage(
            msg_id=str(item.get("id") or subject),
            date=(item.get("receivedDateTime") or "")[:10] or today.isoformat(),
            subject=subject,
            sender=address.get("address", ""),
            sender_name=address.get("name", ""),
            body=content,
            thread_id=str(item.get("conversationId") or ""),
        )

    def fetch(self, today: datetime.date) -> FetchOutcome:
        limit = self.int_option("max_messages", 200, 1, 1000)
        items = self._items(today, "id,subject,receivedDateTime,from,body,conversationId", limit)
        stats = new_mail_stats()
        stats["matched"] = len(items)
        docs = {}
        for item in items:
            made = mail_document(self.mail_message(item, today), stats)
            if made is not None:
                docs[made[0]] = made[1]
        return FetchOutcome(docs, mail_summary(stats, self._scope()), stats)

    def preview(self, today: datetime.date, limit: int = 10) -> dict:
        items = self._items(
            today, "id,subject,receivedDateTime,from", self.int_option("max_messages", 200, 1, 1000)
        )
        rows = []
        for item in items[:limit]:
            mail = self.mail_message(item, today)
            rows.append({"date": mail.date, "subject": mail.subject, "sender": mail.sender})
        stats = new_mail_stats()
        stats["matched"] = len(items)
        return {"rows": rows, "stats": stats, "summary": preview_summary(stats, self._scope())}
