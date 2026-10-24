from __future__ import annotations

import datetime
import re

from decision_agent.connectors.base import (
    Connector,
    ConnectorError,
    FetchOutcome,
    OptionSpec,
    SecretSpec,
    check_response,
)
from decision_agent.connectors.http import HttpError
from decision_agent.sources.model import Block, ParsedDocument, Section

API = "https://slack.com/api/conversations.history"
MAX_PAGES = 10
ERRORS = {
    "invalid_auth": "토큰이 올바르지 않습니다.",
    "not_authed": "토큰이 없습니다.",
    "token_revoked": "토큰이 취소되었습니다.",
    "missing_scope": "토큰에 channels:history(비공개는 groups:history) 권한이 없습니다.",
    "channel_not_found": "채널 ID를 찾을 수 없습니다.",
    "not_in_channel": "봇이 그 채널에 초대되어 있지 않습니다. 채널에서 /invite 로 초대하세요.",
}


def clean_slack(text: str) -> str:
    text = re.sub(r"<@[A-Z0-9]+(?:\|([^>]+))?>", lambda m: "@" + (m.group(1) or "user"), text)
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", "", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


class SlackConnector(Connector):
    id = "slack"
    label = "Slack"
    description = "지정한 채널의 최근 대화에서 결정 문장을 읽습니다."
    reads = "지정한 채널 ID의 최근 메시지 텍스트만 읽습니다 (봇을 초대한 채널만 가능). 메시지를 보내거나 지우지 않습니다."
    guide = (
        "api.slack.com/apps 에서 앱을 만들고 Bot Token Scopes에 channels:history (비공개 채널은 groups:history)만 추가합니다.",
        "워크스페이스에 설치하고 Bot User OAuth Token(xoxb-...)을 아래에 저장합니다.",
        "읽을 채널에서 /invite @앱이름 으로 초대하고, 채널 ID(C로 시작)를 입력합니다.",
    )
    options = (
        OptionSpec("channels", "채널 ID (한 줄에 하나)", "list", [], "채널 ID를 지정해야만 읽습니다.", True),
        OptionSpec("days_back", "과거 일수", "number", 7),
        OptionSpec("max_messages", "채널당 최대 메시지 수", "number", 300),
    )
    secrets = (SecretSpec("bot_token", "Bot 토큰"),)

    def fetch(self, today: datetime.date) -> FetchOutcome:
        days = self.int_option("days_back", 7, 1, 90)
        limit = self.int_option("max_messages", 300, 1, 2000)
        oldest = datetime.datetime.combine(today - datetime.timedelta(days=days), datetime.time.min)
        headers = {"Authorization": f"Bearer {self.secret('bot_token')}"}
        docs: dict[str, ParsedDocument] = {}
        total = 0
        for channel in self.list_option("channels"):
            messages: list[dict] = []
            cursor = None
            for _ in range(MAX_PAGES):
                params = {"channel": channel, "oldest": f"{oldest.timestamp():.0f}", "limit": 200, "cursor": cursor}
                try:
                    response = self.ctx.http.request("GET", API, headers=headers, params=params)
                except HttpError as exc:
                    raise ConnectorError(str(exc)) from exc
                body = check_response(response, "Slack").json()
                if not body.get("ok"):
                    code = str(body.get("error", ""))
                    raise ConnectorError(f"Slack: {ERRORS.get(code, code or '알 수 없는 오류')}")
                messages.extend(body.get("messages", []))
                cursor = (body.get("response_metadata") or {}).get("next_cursor")
                if not cursor or len(messages) >= limit:
                    break
            by_day: dict[str, list[str]] = {}
            for message in sorted(messages[:limit], key=lambda m: float(m.get("ts", 0))):
                if message.get("subtype"):
                    continue
                text = clean_slack(message.get("text", ""))
                if len(text) < 2:
                    continue
                day = datetime.datetime.fromtimestamp(float(message.get("ts", 0))).date().isoformat()
                by_day.setdefault(day, []).append(text)
            section = Section(name=f"channel {channel}")
            for day, texts in by_day.items():
                section.blocks.append(Block(text=day, kind="heading", level=2))
                section.blocks.extend(Block(text=t, kind="para") for t in texts)
                total += len(texts)
            if section.blocks:
                doc = ParsedDocument(path="", fmt="connector", sections=[section])
                doc.meta["kind"] = "MEETING"
                docs[f"channel {channel}"] = doc
        return FetchOutcome(docs, f"메시지 {total}건")
