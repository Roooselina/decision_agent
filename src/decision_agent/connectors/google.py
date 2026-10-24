from __future__ import annotations

import base64
import datetime
import urllib.parse

from decision_agent.connectors.base import (
    CalendarEvent,
    Connector,
    ConnectorError,
    FetchOutcome,
    OptionSpec,
    SecretSpec,
    calendar_documents,
    check_response,
    clean_message_text,
    clock_of,
    message_document,
    strip_html,
    unique_name,
)
from decision_agent.connectors.http import HttpError
from decision_agent.connectors.oauth import OAuthApp

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_PAGES = 10

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

    def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            response = self.ctx.http.request("GET", url, headers=self._headers(), params=params)
        except HttpError as exc:
            raise ConnectorError(str(exc)) from exc
        return check_response(response, self.label).json()


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
    description = "지정한 라벨의 메일 본문에서 결정 문장을 읽습니다."
    reads = (
        "지정한 라벨이 붙은 최근 메일의 제목, 날짜, 본문 텍스트만 읽습니다. "
        "Google의 읽기 권한은 메일 전체 범위이지만 이 프로그램은 지정한 라벨만 조회합니다. "
        "메일을 보내거나 지우거나 읽음 표시를 바꾸지 않습니다."
    )
    guide = GOOGLE_GUIDE
    scope = GMAIL_SCOPE
    options = (
        OptionSpec("client_id", "클라이언트 ID", required=True),
        OptionSpec("label", "읽을 라벨 이름", help="예: 회의록. 라벨을 지정해야만 읽습니다.", required=True),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("max_messages", "최대 메일 수", "number", 50),
    )

    @staticmethod
    def _decode(data: str) -> str:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")

    def _body_text(self, part: dict) -> str:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if mime == "text/plain" and data:
            return self._decode(data)
        plain = ""
        html_text = ""
        for child in part.get("parts", []) or []:
            found = self._body_text(child)
            if found and child.get("mimeType", "").startswith("text/html"):
                html_text = html_text or found
            elif found:
                plain = plain or found
        if plain:
            return plain
        if mime == "text/html" and data:
            return strip_html(self._decode(data))
        return strip_html(html_text) if html_text else ""

    def fetch(self, today: datetime.date) -> FetchOutcome:
        label = self.text_option("label")
        days = self.int_option("days_back", 14, 1, 365)
        limit = self.int_option("max_messages", 50, 1, 200)
        query = f'label:"{label}" newer_than:{days}d'
        listing = self._get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            {"q": query, "maxResults": limit},
        )
        docs = {}
        for ref in listing.get("messages", [])[:limit]:
            message = self._get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{ref['id']}",
                {"format": "full"},
            )
            payload = message.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            stamp = int(message.get("internalDate", "0") or 0) / 1000.0
            date = datetime.datetime.fromtimestamp(stamp).date().isoformat() if stamp else today.isoformat()
            subject = headers.get("subject", "(제목 없음)")
            lines = clean_message_text(self._body_text(payload))
            if not lines:
                continue
            doc = message_document(subject, date, headers.get("from", ""), lines)
            docs[unique_name(f"{date} {subject}", ref["id"])] = doc
        return FetchOutcome(docs, f"메일 {len(docs)}건")
