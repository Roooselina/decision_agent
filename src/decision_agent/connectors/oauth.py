from __future__ import annotations

import base64
import hashlib
import json
import secrets as pysecrets
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from decision_agent.connectors.base import ConnectorError, SecretStore
from decision_agent.connectors.http import Http, HttpError

PENDING_SECONDS = 600
EXPIRY_MARGIN = 90
# Scopes a token response may add or leave out without changing what can be read.
_IGNORED_SCOPES = frozenset({"offline_access", "openid", "profile", "email"})
MISSING_SCOPE = "읽기 권한 체크가 빠졌습니다. 다시 연결하고 권한 항목을 모두 선택하세요."
EXPIRED = "로그인이 만료되었습니다. 다시 연결하세요; 테스트 모드의 Google 앱은 7일 뒤 만료됩니다"


def _scope_name(scope: str) -> str:
    # Microsoft may answer "https://graph.microsoft.com/Mail.Read" for "Mail.Read".
    return scope.strip().rstrip("/").rsplit("/", 1)[-1].casefold()


def missing_scopes(required: tuple[str, ...], granted: str | list[str]) -> list[str]:
    """Required scopes that the granted list lacks (the user can untick them on consent)."""
    items = granted.split() if isinstance(granted, str) else list(granted)
    have = {_scope_name(s) for s in items}
    return [s for s in required if _scope_name(s) not in _IGNORED_SCOPES and _scope_name(s) not in have]


@dataclass(frozen=True)
class OAuthApp:
    """Describes one OAuth 2.0 client (authorization code flow with PKCE, loopback redirect)."""

    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id: str
    client_secret: str = ""
    redirect_host: str = "127.0.0.1"
    extra_params: dict[str, str] = field(default_factory=dict)


@dataclass
class _Pending:
    connector_id: str
    verifier: str
    app: OAuthApp
    redirect_uri: str
    created: float


def _pkce_pair() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.decode("ascii").rstrip("=")


class OAuthManager:
    """Runs the browser login for read-only access and keeps the tokens in the credential store.

    Only a refresh token and a short-lived access token are stored, in the operating system
    credential store, never in the settings file. The callback address is the program's own
    local web server, so no extra port is opened.
    """

    def __init__(
        self,
        http: Http,
        secrets: SecretStore,
        port: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.http = http
        self.secrets = secrets
        self.port = port
        self.clock = clock
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    @staticmethod
    def secret_name(connector_id: str) -> str:
        return f"connector.{connector_id}.oauth"

    def begin(self, connector_id: str, app: OAuthApp) -> str:
        if not app.client_id:
            raise ConnectorError("클라이언트 ID를 먼저 입력하세요.")
        verifier, challenge = _pkce_pair()
        state = pysecrets.token_urlsafe(24)
        redirect_uri = f"http://{app.redirect_host}:{self.port}/oauth/callback"
        with self._lock:
            now = self.clock()
            self._pending = {
                k: v for k, v in self._pending.items() if now - v.created < PENDING_SECONDS
            }
            self._pending[state] = _Pending(connector_id, verifier, app, redirect_uri, now)
        params = {
            "response_type": "code",
            "client_id": app.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(app.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **app.extra_params,
        }
        return f"{app.auth_url}?{urllib.parse.urlencode(params)}"

    def complete(self, state: str, code: str) -> str:
        """Finish the login. Returns the connector id. A state value works exactly once."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self.clock() - pending.created > PENDING_SECONDS:
            raise ConnectorError("로그인 요청이 만료되었거나 올바르지 않습니다. 다시 시도하세요.")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": pending.app.client_id,
            "code_verifier": pending.verifier,
        }
        if pending.app.client_secret:
            form["client_secret"] = pending.app.client_secret
        token = self._token_request(pending.app, form)
        if not token.get("refresh_token"):
            raise ConnectorError("로그인은 되었지만 계속 읽을 수 있는 권한(refresh token)을 받지 못했습니다.")
        granted = token.get("scope")
        if granted is not None and missing_scopes(pending.app.scopes, granted):
            # Google lets the user untick a permission; such a login is not stored.
            raise ConnectorError(MISSING_SCOPE)
        self._store(pending.connector_id, token, token["refresh_token"])
        return pending.connector_id

    def connected(self, connector_id: str, app: OAuthApp) -> bool:
        """A login is stored and, when the grant was recorded, it covers the required scopes."""
        raw = self.secrets.get(self.secret_name(connector_id))
        if not raw:
            return False
        try:
            saved = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(saved, dict):
            return False
        granted = saved.get("scopes")
        # Logins stored before scopes were recorded keep working.
        return granted is None or not missing_scopes(app.scopes, granted)

    def access_token(self, connector_id: str, app: OAuthApp) -> str:
        raw = self.secrets.get(self.secret_name(connector_id))
        if not raw:
            raise ConnectorError("계정이 연결되어 있지 않습니다.")
        try:
            saved = json.loads(raw)
        except ValueError as exc:
            raise ConnectorError("저장된 로그인 정보가 손상되었습니다. 계정을 다시 연결하세요.") from exc
        if saved.get("access_token") and saved.get("expires_at", 0) - EXPIRY_MARGIN > self.clock():
            return saved["access_token"]
        form = {
            "grant_type": "refresh_token",
            "refresh_token": saved.get("refresh_token", ""),
            "client_id": app.client_id,
        }
        if app.client_secret:
            form["client_secret"] = app.client_secret
        token = self._token_request(app, form)
        self._store(connector_id, token, token.get("refresh_token") or saved.get("refresh_token", ""))
        return token["access_token"]

    def disconnect(self, connector_id: str) -> None:
        self.secrets.delete(self.secret_name(connector_id))

    def _store(self, connector_id: str, token: dict, refresh: str) -> None:
        record = {
            "refresh_token": refresh,
            "access_token": token.get("access_token", ""),
            "expires_at": self.clock() + float(token.get("expires_in", 0) or 0),
        }
        if token.get("scope") is not None:
            record["scopes"] = str(token["scope"]).split()
        else:
            previous = self.secrets.get(self.secret_name(connector_id))
            try:
                kept = json.loads(previous).get("scopes") if previous else None
            except (ValueError, AttributeError):
                kept = None
            if kept is not None:
                record["scopes"] = kept
        self.secrets.set(self.secret_name(connector_id), json.dumps(record))

    def _token_request(self, app: OAuthApp, form: dict[str, str]) -> dict:
        try:
            response = self.http.request("POST", app.token_url, form=form)
        except HttpError as exc:
            raise ConnectorError(str(exc)) from exc
        if not response.ok:
            detail = ""
            reason = ""
            try:
                body = response.json()
                reason = str(body.get("error") or "")
                detail = str(body.get("error_description") or body.get("error") or "")[:160]
            except Exception:  # noqa: BLE001 - the error body is optional detail only
                detail = ""
            if reason == "invalid_grant":
                raise ConnectorError(f"{EXPIRED} ({detail})" if detail and detail != reason else EXPIRED)
            raise ConnectorError(f"로그인 서버가 요청을 거절했습니다 ({response.status}). {detail}".strip())
        body = response.json()
        if not body.get("access_token"):
            raise ConnectorError("로그인 서버가 접근 토큰을 주지 않았습니다.")
        return body
