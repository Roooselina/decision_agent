from __future__ import annotations

import datetime

from decision_agent.connectors.base import (
    Connector,
    ConnectorError,
    FetchOutcome,
    OptionSpec,
    SecretSpec,
    check_response,
    safe_name,
    unique_name,
)
from decision_agent.connectors.http import HttpError
from decision_agent.sources.model import Block, ParsedDocument, Section, Table

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_DEPTH = 2
MAX_BLOCKS = 800


def plain(rich: list[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in rich or []).strip()


def property_text(prop: dict) -> str:
    kind = prop.get("type", "")
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return plain(value)
    if kind in {"select", "status"}:
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return ", ".join(v.get("name", "") for v in value or [])
    if kind == "people":
        return ", ".join(v.get("name", "") for v in value or [])
    if kind == "date":
        return ((value or {}).get("start") or "")[:10]
    if kind in {"number", "url", "email", "phone_number"}:
        return "" if value is None else str(value)
    if kind == "checkbox":
        return "예" if value else "아니오"
    return ""


class NotionConnector(Connector):
    id = "notion"
    label = "Notion"
    description = "통합(integration)에 공유한 페이지와 데이터베이스를 읽습니다."
    reads = (
        "Notion 통합에 직접 공유한 페이지의 텍스트와 데이터베이스 행만 읽습니다. "
        "공유하지 않은 페이지는 읽을 수 없고, 페이지를 만들거나 고치지 않습니다."
    )
    guide = (
        "notion.so/profile/integrations 에서 '내부 통합'을 만들고 권한은 '콘텐츠 읽기'만 켭니다.",
        "읽을 페이지나 데이터베이스에서 '연결'로 그 통합을 추가합니다.",
        "통합의 비밀 토큰을 아래에 저장합니다.",
    )
    options = (
        OptionSpec("max_pages", "최대 페이지 수", "number", 30),
        OptionSpec("days_back", "최근 수정 일수", "number", 90, "이 기간 안에 수정된 것만 읽습니다."),
    )
    secrets = (SecretSpec("token", "통합 토큰"),)

    def _request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.secret('token')}", "Notion-Version": VERSION}
        try:
            response = self.ctx.http.request(
                method, f"{API}{path}", headers=headers, params=params, json_body=body
            )
        except HttpError as exc:
            raise ConnectorError(str(exc)) from exc
        return check_response(response, "Notion").json()

    def _children(self, block_id: str, depth: int, budget: list[int]) -> list[Block]:
        blocks: list[Block] = []
        cursor = None
        while budget[0] > 0:
            body = self._request(
                "GET", f"/blocks/{block_id}/children", params={"page_size": 100, "start_cursor": cursor}
            )
            for item in body.get("results", []):
                budget[0] -= 1
                kind = item.get("type", "")
                data = item.get(kind) or {}
                text = plain(data.get("rich_text", [])) if isinstance(data, dict) else ""
                if kind.startswith("heading") and text:
                    blocks.append(Block(text=text, kind="heading", level=int(kind[-1])))
                elif kind in {"bulleted_list_item", "numbered_list_item"} and text:
                    blocks.append(Block(text=text, kind="bullet"))
                elif kind == "to_do" and text:
                    blocks.append(Block(text=text, kind="bullet", checked=bool(data.get("checked"))))
                elif kind in {"paragraph", "quote", "callout", "toggle"} and text:
                    blocks.append(Block(text=text, kind="para"))
                if item.get("has_children") and depth < MAX_DEPTH and kind != "child_page":
                    blocks.extend(self._children(item["id"], depth + 1, budget))
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
        return blocks

    def _database_doc(self, database_id: str, title: str) -> ParsedDocument | None:
        body = self._request("POST", f"/databases/{database_id}/query", {"page_size": 100})
        rows_data = body.get("results", [])
        if not rows_data:
            return None
        names = list(rows_data[0].get("properties", {}))
        title_name = next(
            (n for n in names if rows_data[0]["properties"][n].get("type") == "title"), names[0]
        )
        headers = [title_name] + [n for n in names if n != title_name]
        rows = [
            [property_text(row.get("properties", {}).get(h, {})) for h in headers] for row in rows_data
        ]
        table = Table(headers=headers, rows=rows, first_row_location=1)
        doc = ParsedDocument(path="", fmt="connector", sections=[Section(name=title, tables=[table])])
        return doc

    def fetch(self, today: datetime.date) -> FetchOutcome:
        limit = self.int_option("max_pages", 30, 1, 200)
        days = self.int_option("days_back", 90, 1, 3650)
        oldest = (today - datetime.timedelta(days=days)).isoformat()
        found = self._request(
            "POST",
            "/search",
            {"page_size": min(limit, 100), "sort": {"direction": "descending", "timestamp": "last_edited_time"}},
        )
        docs: dict[str, ParsedDocument] = {}
        budget = [MAX_BLOCKS]
        for item in found.get("results", [])[:limit]:
            edited = (item.get("last_edited_time") or "")[:10]
            if edited and edited < oldest:
                continue
            if item.get("object") == "database":
                title = plain(item.get("title", [])) or "database"
                doc = self._database_doc(item["id"], title)
                label = f"{title} (database)"
            else:
                props = item.get("properties", {})
                title_prop = next((p for p in props.values() if p.get("type") == "title"), {})
                title = plain(title_prop.get("title", [])) or "untitled"
                blocks = self._children(item["id"], 1, budget)
                if not blocks:
                    continue
                doc = ParsedDocument(path="", fmt="connector", sections=[Section(name=title, blocks=blocks)])
                label = title
            if doc is None:
                continue
            if edited:
                doc.meta["date"] = edited
                doc.meta["modified"] = edited
            docs[unique_name(safe_name(label), item["id"])] = doc
        return FetchOutcome(docs, f"페이지와 데이터베이스 {len(docs)}개")
