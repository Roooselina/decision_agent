from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from typing import Any

from decision_agent.connectors.base import (
    Connector,
    ConnectorContext,
    ConnectorError,
    FetchOutcome,
    SecretStore,
)
from decision_agent.connectors.http import Http, UrllibHttp
from decision_agent.connectors.oauth import OAuthManager
from decision_agent.connectors.registry import CONNECTORS, describe
from decision_agent.desktop.secrets import KeyringSecrets, SecretStoreUnavailable
from decision_agent.desktop.settings import AppSettings

log = logging.getLogger(__name__)


class ConnectorManager:
    """Builds connectors from settings, keeps their secrets, and runs the OAuth login.

    Secrets (tokens, passwords, client secrets) go only to the credential store. The settings
    file holds just the non-secret options such as the folder or label to read.
    """

    def __init__(
        self,
        get_settings: Callable[[], AppSettings],
        http: Http | None = None,
        secrets: SecretStore | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
    ) -> None:
        self.get_settings = get_settings
        self.http = http or UrllibHttp()
        self.secrets = secrets or KeyringSecrets()
        self.today = today
        self.oauth = OAuthManager(self.http, self.secrets, get_settings().ui_port)

    # lookup

    def cls(self, connector_id: str) -> type[Connector]:
        try:
            return CONNECTORS[connector_id]
        except KeyError as exc:
            raise ConnectorError("알 수 없는 연결입니다.") from exc

    def instance(self, connector_id: str) -> Connector:
        settings = self.get_settings().connectors.get(connector_id)
        options = settings.options if settings else {}
        ctx = ConnectorContext(http=self.http, secrets=self.secrets, oauth=self.oauth)
        return self.cls(connector_id)(options, ctx)

    def enabled_ids(self) -> list[str]:
        return [i for i, c in self.get_settings().connectors.items() if c.enabled and i in CONNECTORS]

    # settings form data

    def overview(self, statuses: dict[str, dict | None]) -> list[dict]:
        rows = []
        for connector_id, cls in CONNECTORS.items():
            info = describe(cls)
            connector = self.instance(connector_id)
            saved = self.get_settings().connectors.get(connector_id)
            info["enabled"] = bool(saved and saved.enabled)
            info["values"] = dict(connector.opts)
            info["has_secrets"] = {
                s.name: bool(self.secrets.get(connector.secret_key(s.name))) for s in cls.secrets
            }
            info["connected"] = (
                bool(self.secrets.get(connector.secret_key("oauth"))) if cls.uses_oauth else None
            )
            info["problems"] = connector.problems()
            info["status"] = statuses.get(connector_id)
            rows.append(info)
        return rows

    def coerce_options(self, connector_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Keep only known options and convert them to the declared type."""
        cleaned: dict[str, Any] = {}
        for spec in self.cls(connector_id).options:
            if spec.name not in raw:
                continue
            value = raw[spec.name]
            if spec.kind == "number":
                try:
                    cleaned[spec.name] = int(value)
                except (TypeError, ValueError) as exc:
                    raise ConnectorError(f"'{spec.label}'은(는) 숫자여야 합니다.") from exc
            elif spec.kind == "list":
                items = value.splitlines() if isinstance(value, str) else list(value or [])
                cleaned[spec.name] = [str(i).strip() for i in items if str(i).strip()][:50]
            elif spec.kind == "bool":
                cleaned[spec.name] = bool(value)
            else:
                cleaned[spec.name] = str(value or "").strip()[:500]
        return cleaned

    # secrets and login

    def save_secret(self, connector_id: str, name: str, value: str) -> None:
        connector = self.instance(connector_id)
        if name not in {s.name for s in connector.secrets}:
            raise ConnectorError("저장할 수 없는 항목입니다.")
        try:
            self.secrets.set(connector.secret_key(name), value.strip())
        except SecretStoreUnavailable as exc:
            raise ConnectorError(str(exc)) from exc

    def delete_secret(self, connector_id: str, name: str) -> None:
        connector = self.instance(connector_id)
        if name in {s.name for s in connector.secrets}:
            self.secrets.delete(connector.secret_key(name))

    def start_login(self, connector_id: str) -> str:
        connector = self.instance(connector_id)
        if not connector.uses_oauth:
            raise ConnectorError("이 연결은 로그인 창이 필요 없습니다.")
        self.oauth.port = self.get_settings().ui_port
        return self.oauth.begin(connector_id, connector.oauth_app())

    def finish_login(self, state: str, code: str) -> str:
        return self.oauth.complete(state, code)

    def forget(self, connector_id: str) -> None:
        """Remove every stored secret of one connector."""
        connector = self.instance(connector_id)
        for spec in connector.secrets:
            self.secrets.delete(connector.secret_key(spec.name))
        self.secrets.delete(connector.secret_key("oauth"))

    # fetching

    def fetch(self, connector_id: str) -> tuple[FetchOutcome | None, dict]:
        """Run one connector. Returns the documents (or None) and a status record."""
        stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
        connector = self.instance(connector_id)
        problems = connector.problems()
        if problems:
            return None, {"at": stamp, "ok": False, "message": problems[0], "summary": ""}
        try:
            outcome = connector.fetch(self.today())
        except ConnectorError as exc:
            return None, {"at": stamp, "ok": False, "message": str(exc), "summary": ""}
        except Exception as exc:
            log.exception("connector %s failed", connector_id)
            return None, {"at": stamp, "ok": False, "message": f"예기치 않은 오류: {exc}"[:200], "summary": ""}
        return outcome, {"at": stamp, "ok": True, "message": "", "summary": outcome.summary}
