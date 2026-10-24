from __future__ import annotations

import base64
import datetime
import urllib.parse

from decision_agent.connectors.base import (
    Connector,
    ConnectorError,
    FetchOutcome,
    OptionSpec,
    SecretSpec,
    check_response,
)
from decision_agent.connectors.http import HttpError
from decision_agent.sources.model import ParsedDocument, Section, Table

FIELDS = "summary,status,assignee,duedate,fixVersions,issuetype"
MAX_PAGES = 20


class JiraConnector(Connector):
    id = "jira"
    label = "Jira"
    description = "JQL로 고른 이슈를 작업(Task) 산출물로 읽습니다."
    reads = "JQL에 해당하는 이슈의 키, 제목, 상태, 담당자, 마감일, 버전만 읽습니다. 이슈를 만들거나 고치지 않습니다."
    guide = (
        "id.atlassian.com/manage-profile/security/api-tokens 에서 API 토큰을 만듭니다.",
        "Jira 주소(예: https://팀이름.atlassian.net)와 계정 이메일을 입력합니다.",
        "읽을 범위를 JQL로 적습니다. 예: project = APP AND updated >= -30d",
    )
    options = (
        OptionSpec("base_url", "Jira 주소", help="https://팀이름.atlassian.net", required=True),
        OptionSpec("email", "계정 이메일", required=True),
        OptionSpec("jql", "JQL", default="updated >= -30d ORDER BY updated DESC", required=True),
        OptionSpec("max_issues", "최대 이슈 수", "number", 200),
    )
    secrets = (SecretSpec("api_token", "API 토큰"),)

    def fetch(self, today: datetime.date) -> FetchOutcome:
        base = self.text_option("base_url").rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConnectorError("Jira 주소는 https:// 로 시작해야 합니다.")
        raw = f"{self.text_option('email')}:{self.secret('api_token')}".encode()
        headers = {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii"), "Accept": "application/json"}
        limit = self.int_option("max_issues", 200, 1, 2000)
        params: dict = {"jql": self.text_option("jql"), "fields": FIELDS, "maxResults": min(100, limit)}
        rows: list[list[str]] = []
        for _ in range(MAX_PAGES):
            try:
                response = self.ctx.http.request(
                    "GET", f"{base}/rest/api/3/search/jql", headers=headers, params=params
                )
            except HttpError as exc:
                raise ConnectorError(str(exc)) from exc
            if response.status == 400:
                raise ConnectorError("Jira가 JQL을 이해하지 못했습니다. JQL을 확인하세요.")
            body = check_response(response, "Jira").json()
            for issue in body.get("issues", []):
                fields = issue.get("fields") or {}
                versions = ", ".join(v.get("name", "") for v in fields.get("fixVersions") or [])
                rows.append(
                    [
                        issue.get("key", ""),
                        fields.get("summary", ""),
                        (fields.get("status") or {}).get("name", ""),
                        (fields.get("assignee") or {}).get("displayName", ""),
                        fields.get("duedate") or "",
                        versions,
                    ]
                )
            token = body.get("nextPageToken")
            if not token or body.get("isLast") or len(rows) >= limit:
                break
            params = {**params, "nextPageToken": token}
        rows = rows[:limit]
        docs: dict[str, ParsedDocument] = {}
        if rows:
            table = Table(
                headers=["key", "summary", "status", "assignee", "due", "release"],
                rows=rows,
                first_row_location=1,
            )
            doc = ParsedDocument(path="", fmt="connector", sections=[Section(name="issues", tables=[table])])
            doc.meta["kind"] = "TASK"
            docs["issues"] = doc
        return FetchOutcome(docs, f"이슈 {len(rows)}건")
