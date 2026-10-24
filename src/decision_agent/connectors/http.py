from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_BODY_BYTES = 20 * 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class HttpError(RuntimeError):
    """Network failure or a response the caller cannot use."""


@dataclass
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError("서비스가 JSON이 아닌 응답을 돌려줬습니다.") from exc

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Http(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        json_body: Any = None,
        timeout: float = 30.0,
    ) -> HttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects, so an Authorization header cannot be forwarded to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibHttp:
    """Plain standard-library HTTP client. https only, except for the local machine."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        json_body: Any = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        local = f"[{host}]" in LOOPBACK_HOSTS or host in LOOPBACK_HOSTS
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise HttpError("https 주소만 사용할 수 있습니다.")
        if params:
            query = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}, doseq=True
            )
            url = f"{url}{'&' if parsed.query else '?'}{query}"
        data: bytes | None = None
        send_headers = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            send_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            send_headers.setdefault("Content-Type", "application/json")
        send_headers.setdefault("User-Agent", "decision-agent/1.0")
        request = urllib.request.Request(url, data=data, headers=send_headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_BODY_BYTES + 1)
                if len(body) > MAX_BODY_BYTES:
                    raise HttpError("응답이 너무 큽니다.")
                return HttpResponse(response.status, body, dict(response.headers.items()))
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY_BYTES) if exc.fp is not None else b""
            return HttpResponse(exc.code, body, dict(exc.headers.items()) if exc.headers else {})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HttpError(f"연결하지 못했습니다: {getattr(exc, 'reason', exc)}") from exc
