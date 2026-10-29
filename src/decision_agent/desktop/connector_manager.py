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
from decision_agent.connectors.mailfilter import MailQuery
from decision_agent.connectors.oauth import MISSING_SCOPE, OAuthManager
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
        # Connected account and label names per connector, in memory only: shown in the
        # settings card so the user can tell which account is read, never logged or saved.
        self.accounts: dict[str, dict] = {}

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
                self.oauth.connected(connector_id, connector.oauth_app()) if cls.uses_oauth else None
            )
            info["problems"] = connector.problems()
            if cls.uses_oauth and not info["connected"] and self.secrets.get(connector.secret_key("oauth")):
                info["problems"] = [*info["problems"], MISSING_SCOPE]
            info["status"] = statuses.get(connector_id)
            info["account"] = self.accounts.get(connector_id, {}).get("account", "")
            info["label_names"] = self.accounts.get(connector_id, {}).get("labels", [])
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

    def start_send_consent(self) -> str:
        """The Google consent that adds gmail.send to the Gmail login (only from the setting)."""
        connector = self.instance("gmail")
        self.oauth.port = self.get_settings().ui_port
        return self.oauth.begin("gmail", connector.send_app())

    def send_granted(self) -> bool:
        from decision_agent.connectors.google import GMAIL_SEND_SCOPE

        return GMAIL_SEND_SCOPE in (self.oauth.granted_scopes("gmail") or [])

    def finish_login(self, state: str, code: str) -> str:
        connector_id = self.oauth.complete(state, code)
        self.accounts.pop(connector_id, None)
        self.remember_account(connector_id)
        return connector_id

    def remember_account(self, connector_id: str, connector: Connector | None = None) -> None:
        """Look up the connected account once and keep it in memory for the settings card."""
        connector = connector or self.instance(connector_id)
        try:
            info = connector.account_info()
        except ConnectorError:
            return
        except Exception:  # noqa: BLE001 - the account line is optional
            log.warning("account lookup failed for %s", connector_id)
            return
        if info:
            self.accounts[connector_id] = info

    def forget(self, connector_id: str) -> None:
        """Remove every stored secret of one connector."""
        connector = self.instance(connector_id)
        for spec in connector.secrets:
            self.secrets.delete(connector.secret_key(spec.name))
        self.secrets.delete(connector.secret_key("oauth"))
        self.accounts.pop(connector_id, None)

    # fetching

    def fetch(self, connector_id: str) -> tuple[FetchOutcome | None, dict]:
        """Run one connector. Returns the documents (or None) and a status record."""
        return self._run(connector_id, lambda connector: connector.fetch(self.today()))

    def fetch_source(
        self, connector_id: str, query: MailQuery, known: set[str], threads: set[str] | None = None
    ) -> tuple[FetchOutcome | None, dict]:
        """Run one mail source on its connector's login."""
        return self._run(
            connector_id, lambda connector: connector.fetch_source(query, self.today(), known, threads)
        )

    def preview_source(self, connector_id: str, query: MailQuery) -> dict:
        """Headers of up to ten mails of one source. Nothing is stored, no model is called."""
        connector = self.instance(connector_id)
        problems = connector.problems()
        if problems:
            raise ConnectorError(problems[0])
        result = connector.preview_source(query, self.today())
        if connector_id not in self.accounts:
            self.remember_account(connector_id, connector)
        result["account"] = self.accounts.get(connector_id, {}).get("account", "")
        result["label_names"] = self.accounts.get(connector_id, {}).get("labels", [])
        return result

    def _run(self, connector_id: str, call) -> tuple[FetchOutcome | None, dict]:
        stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
        connector = self.instance(connector_id)
        problems = connector.problems()
        if problems:
            return None, {"at": stamp, "ok": False, "message": problems[0], "summary": ""}
        try:
            outcome = call(connector)
        except ConnectorError as exc:
            return None, {"at": stamp, "ok": False, "message": str(exc), "summary": ""}
        except Exception as exc:
            log.exception("connector %s failed", connector_id)
            return None, {"at": stamp, "ok": False, "message": f"예기치 않은 오류: {exc}"[:200], "summary": ""}
        if connector_id not in self.accounts:
            self.remember_account(connector_id, connector)
        elif getattr(connector, "label_names", None) is not None:
            self.accounts[connector_id]["labels"] = list(connector.label_names)
        status = {"at": stamp, "ok": True, "message": "", "summary": outcome.summary}
        if outcome.stats:
            status["stats"] = dict(outcome.stats)
        return outcome, status

    def preview(self, connector_id: str) -> dict:
        """Up to ten matching mails, headers only. Nothing is stored and no model is called."""
        connector = self.instance(connector_id)
        problems = connector.problems()
        if problems:
            raise ConnectorError(problems[0])
        result = connector.preview(self.today())
        if connector_id not in self.accounts:
            self.remember_account(connector_id, connector)
        result["account"] = self.accounts.get(connector_id, {}).get("account", "")
        result["label_names"] = self.accounts.get(connector_id, {}).get("labels", [])
        return result
