from __future__ import annotations

import base64
import binascii
import html
import json
import logging
import re
import secrets as pysecrets
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from pydantic import BaseModel, Field

from decision_agent import __version__
from decision_agent.connectors.base import ConnectorError
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.errors import UserError
from decision_agent.desktop.settings import FolderNeedsMerge, FolderRejected
from decision_agent.desktop.webpage import PAGE, QUICK_PAGE, static_assets

log = logging.getLogger(__name__)

COOKIE = "da_session"
EDITABLE_FIELDS = {
    "allow_external_send", "provider", "model_name", "notify", "analysis_mode",
    "debounce_seconds", "rescan_minutes", "current_release", "exclude_patterns",
    "max_file_mb", "max_llm_calls_per_cycle", "kind_overrides", "ollama_url", "embedding_model",
    "connector_target", "connector_interval_minutes", "hotkey_enabled", "hotkey",
    "digest_enabled", "digest_time", "display_name", "profile_color", "theme", "language",
    "onboarding_done",
}
PURGE_CONFIRM = "모두 삭제"
PAGE_HASH = re.compile(r"^#/[a-z-]{0,30}$")
DATE_PARAM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CSP = (
    "default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
MAX_IMPORT_CHARS = 40 * 1024 * 1024
NEXT_PAGES = {"/", "/quick"}


class FindingStatusBody(BaseModel):
    root: str
    topic: str
    artifact_id: str
    status: str
    snooze_hours: int = 0


class AnswerBody(BaseModel):
    root: str
    topic: str
    text: str = Field(min_length=1, max_length=2000)


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class AddFolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    merge: bool = False


class ExcludedBody(BaseModel):
    root: str = Field(min_length=1, max_length=1024)
    excluded: list[str] = Field(default_factory=list, max_length=1000)


class OpenBody(BaseModel):
    target: str = Field(min_length=1, max_length=1024)


class PurgeBody(BaseModel):
    confirm: str = Field(max_length=40)


class AutostartBody(BaseModel):
    enabled: bool


class ActivateBody(BaseModel):
    page: str = Field(default="", max_length=40)


class PauseBody(BaseModel):
    paused: bool


class RescanBody(BaseModel):
    root: str | None = None


class KeyBody(BaseModel):
    value: str = Field(min_length=8, max_length=512)


class ConnectorConfigBody(BaseModel):
    id: str = Field(max_length=40)
    enabled: bool
    options: dict = Field(default_factory=dict)


class ConnectorSecretBody(BaseModel):
    id: str = Field(max_length=40)
    name: str = Field(max_length=40)
    value: str = Field(default="", max_length=4096)


class ConnectorIdBody(BaseModel):
    id: str = Field(max_length=40)


class OAuthStartBody(BaseModel):
    id: str = Field(max_length=40)
    open_browser: bool = False


class NoteBody(BaseModel):
    text: str = Field(min_length=2, max_length=1000)
    date: str | None = Field(default=None, max_length=10)


class ImportBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)


class TextBody(BaseModel):
    text: str = Field(min_length=2, max_length=1000)


class DraftBody(BaseModel):
    root: str
    topic: str
    artifact_id: str


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def create_web_app(
    controller: DecisionAgentApp,
    token: str,
    port: int,
    url_opener: Callable[[str], object] = webbrowser.open,
) -> FastAPI:
    """Local web UI. It listens on loopback only and guards against browser-based attacks.

    Defences: the Host header must name the loopback address (blocks DNS rebinding), every
    API call needs a secret header that other web pages cannot read or set, and responses carry
    a strict content security policy. Document text is always inserted as text, never as HTML.
    """
    app = FastAPI(title="Decision Agent", docs_url=None, redoc_url=None, openapi_url=None)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return PlainTextResponse("허용되지 않은 호스트입니다.", status_code=400)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    def require_token(request: Request) -> None:
        given = request.headers.get("x-da-token", "")
        if not pysecrets.compare_digest(given, token):
            raise HTTPException(status_code=403, detail="인증이 필요합니다.")

    auth = [Depends(require_token)]

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, t: str | None = None, next: str = "/"):
        if t is not None:
            if not pysecrets.compare_digest(t, token):
                return PlainTextResponse("인증이 필요합니다.", status_code=403)
            response = RedirectResponse(next if next in NEXT_PAGES else "/", status_code=303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
            return response
        cookie = request.cookies.get(COOKIE, "")
        if not pysecrets.compare_digest(cookie, token):
            return PlainTextResponse(
                "트레이 아이콘의 '대시보드 열기'로 접속해 주세요.", status_code=403
            )
        nonce = pysecrets.token_urlsafe(16)
        html = PAGE.replace("__NONCE__", nonce).replace("__TOKEN__", json.dumps(token))
        response = HTMLResponse(html)
        response.headers["Content-Security-Policy"] = CSP.format(nonce=nonce)
        return response

    def page_response(request: Request, template: str):
        cookie = request.cookies.get(COOKIE, "")
        if not pysecrets.compare_digest(cookie, token):
            return PlainTextResponse("트레이 아이콘의 '대시보드 열기'로 접속해 주세요.", status_code=403)
        nonce = pysecrets.token_urlsafe(16)
        body = template.replace("__NONCE__", nonce).replace("__TOKEN__", json.dumps(token))
        response = HTMLResponse(body)
        response.headers["Content-Security-Policy"] = CSP.format(nonce=nonce)
        return response

    assets = static_assets()

    @app.get("/static/{name}")
    async def static_file(name: str):
        """Bundled CSS, JS, and icons. Only files on the allow list are served, never a path."""
        asset = assets.get(name)
        if asset is None:
            raise HTTPException(status_code=404, detail="없는 파일입니다.")
        content, media_type = asset
        return Response(content, media_type=media_type)

    @app.get("/quick", response_class=HTMLResponse)
    async def quick_page(request: Request):
        return page_response(request, QUICK_PAGE)

    @app.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(code: str = "", state: str = "", error: str = ""):
        if error or not code or not state:
            message = "로그인이 취소되었거나 실패했습니다."
            ok = False
        else:
            try:
                connector_id = controller.connectors.finish_login(state, code)
                controller.run_connectors(connector_id)
                message = "계정이 연결되었습니다. 이 창을 닫고 대시보드로 돌아가세요."
                ok = True
            except ConnectorError as exc:
                message, ok = str(exc), False
        page = (
            "<!doctype html><meta charset='utf-8'><title>계정 연결</title>"
            f"<body style='font-family:sans-serif;padding:24px'><h2>{'연결 완료' if ok else '연결 실패'}</h2>"
            f"<p>{html.escape(message)}</p></body>"
        )
        response = HTMLResponse(page, status_code=200 if ok else 400)
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        return response

    @app.get("/api/ping")
    async def ping():
        return {"app": "decision-agent", "version": __version__}

    @app.get("/api/status", dependencies=auth)
    async def status():
        return controller.status()

    @app.get("/api/findings", dependencies=auth)
    async def findings(all: int = 0):
        return controller.findings(include_hidden=bool(all))

    @app.get("/api/questions", dependencies=auth)
    async def questions():
        return controller.questions()

    @app.get("/api/files", dependencies=auth)
    async def files():
        return controller.files()

    @app.post("/api/finding/status", dependencies=auth)
    async def finding_status(body: FindingStatusBody):
        try:
            ok = controller.set_finding_status(
                body.root, body.topic, body.artifact_id, body.status, body.snooze_hours
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="알 수 없는 상태입니다.") from exc
        if not ok:
            raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
        return {"ok": True}

    @app.post("/api/answer", dependencies=auth)
    async def answer(body: AnswerBody):
        if body.root not in controller.runtimes:
            raise HTTPException(status_code=404, detail="폴더를 찾을 수 없습니다.")
        controller.answer(body.root, body.topic, body.text)
        return {"ok": True}

    @app.post("/api/rescan", dependencies=auth)
    async def rescan(body: RescanBody):
        controller.rescan(body.root)
        return {"ok": True}

    @app.post("/api/pause", dependencies=auth)
    async def pause(body: PauseBody):
        if body.paused:
            controller.pause()
        else:
            controller.resume()
        return {"paused": controller.paused}

    @app.get("/api/activity", dependencies=auth)
    async def activity():
        return controller.activity.recent(150)

    @app.get("/api/calendar", dependencies=auth)
    def calendar(start: str = ""):
        """Read-only week of events for the home calendar. Titles only, no bodies or secrets."""
        if start and not DATE_PARAM.match(start):
            raise HTTPException(status_code=400, detail="날짜는 YYYY-MM-DD 형식이어야 합니다.")
        return controller.calendar_week(start or None)

    @app.get("/api/log", dependencies=auth)
    async def log_lines():
        return {"lines": controller.log_tail()}

    @app.post("/api/provider/test", dependencies=auth)
    def provider_test():
        return controller.test_provider()

    @app.get("/api/access", dependencies=auth)
    async def access():
        return controller.access_report()

    @app.get("/api/connectors", dependencies=auth)
    def connectors():
        return controller.connector_overview()

    @app.post("/api/connectors/config", dependencies=auth)
    def connector_config(body: ConnectorConfigBody):
        try:
            controller.save_connector_config(body.id, body.enabled, body.options)
        except (ConnectorError, UserError) as exc:
            raise _bad(exc) from exc
        return {"ok": True}

    @app.post("/api/connectors/secret", dependencies=auth)
    def connector_secret(body: ConnectorSecretBody):
        if len(body.value.strip()) < 1:
            raise HTTPException(status_code=400, detail="값을 입력하세요.")
        try:
            controller.connectors.save_secret(body.id, body.name, body.value)
        except ConnectorError as exc:
            raise _bad(exc) from exc
        return {"ok": True}

    @app.delete("/api/connectors/secret", dependencies=auth)
    def connector_secret_delete(body: ConnectorSecretBody):
        try:
            controller.connectors.delete_secret(body.id, body.name)
        except ConnectorError as exc:
            raise _bad(exc) from exc
        return {"ok": True}

    @app.post("/api/connectors/oauth/start", dependencies=auth)
    def connector_oauth_start(body: OAuthStartBody):
        try:
            url = controller.connectors.start_login(body.id)
        except ConnectorError as exc:
            raise _bad(exc) from exc
        opened = False
        if body.open_browser:
            # The login page opens in the user's own browser, never inside the app window.
            opened = bool(url_opener(url))
        return {"url": url, "opened": opened}

    @app.post("/api/connectors/run", dependencies=auth)
    def connector_run(body: ConnectorIdBody):
        try:
            controller.connectors.cls(body.id)
        except ConnectorError as exc:
            raise _bad(exc) from exc
        if not controller.run_connectors(body.id):
            raise HTTPException(status_code=400, detail="분석할 감시 폴더를 먼저 등록하세요.")
        return {"ok": True}

    @app.post("/api/connectors/disconnect", dependencies=auth)
    def connector_disconnect(body: ConnectorIdBody):
        try:
            controller.disconnect_connector(body.id)
        except (ConnectorError, UserError) as exc:
            raise _bad(exc) from exc
        return {"ok": True}

    @app.get("/api/notes", dependencies=auth)
    def notes_list():
        return controller.list_notes()

    @app.post("/api/notes", dependencies=auth)
    def notes_add(body: NoteBody):
        try:
            return controller.add_note(body.text, body.date)
        except (ConnectorError, UserError, ValueError) as exc:
            raise _bad(exc) from exc

    @app.delete("/api/notes/{note_id}", dependencies=auth)
    def notes_delete(note_id: str):
        return {"ok": controller.delete_note(note_id)}

    @app.post("/api/import/file", dependencies=auth)
    def import_file(body: ImportBody):
        if len(body.content_base64) > MAX_IMPORT_CHARS:
            raise HTTPException(status_code=413, detail="파일이 너무 큽니다.")
        try:
            data = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="파일 내용이 올바르지 않습니다.") from exc
        try:
            return {"ok": True, "name": controller.import_file(body.name, data)}
        except (ConnectorError, UserError) as exc:
            raise _bad(exc) from exc

    @app.post("/api/quick/preview", dependencies=auth)
    def quick_preview(body: TextBody):
        try:
            return controller.quick_preview(body.text)
        except UserError as exc:
            raise _bad(exc) from exc

    @app.get("/api/search", dependencies=auth)
    def search(q: str = ""):
        return controller.search(q[:200])

    @app.post("/api/finding/draft", dependencies=auth)
    def finding_draft(body: DraftBody):
        try:
            return controller.draft_fix(body.root, body.topic, body.artifact_id)
        except UserError as exc:
            raise _bad(exc) from exc

    @app.get("/api/digest", dependencies=auth)
    def digest_latest():
        return controller.latest_digest() or {"name": "", "markdown": ""}

    @app.post("/api/digest/now", dependencies=auth)
    def digest_now():
        return controller.digest_now()

    @app.get("/api/settings", dependencies=auth)
    async def get_settings():
        data = controller.settings.model_dump(mode="json")
        data["has_api_key"] = bool(secret_store.get_api_key())
        return data

    @app.post("/api/settings", dependencies=auth)
    def update_settings(payload: dict):
        unknown = set(payload) - EDITABLE_FIELDS
        if unknown:
            raise HTTPException(
                status_code=400, detail=f"변경할 수 없는 항목입니다: {', '.join(sorted(unknown))}"
            )
        if payload.get("connector_target"):
            allowed = {str(Path(p).resolve()) for p in controller.settings.allowed_folders}
            if str(Path(str(payload["connector_target"])).resolve()) not in allowed:
                raise HTTPException(status_code=400, detail="등록된 감시 폴더만 선택할 수 있습니다.")
        merged = controller.settings.model_dump(mode="json")
        merged.update(payload)
        try:
            new = type(controller.settings).model_validate(merged)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="설정 값이 올바르지 않습니다.") from exc
        controller.reconfigure(new)
        return {"ok": True}

    @app.get("/api/folders", dependencies=auth)
    def folder_list():
        return controller.folder_cards()

    @app.post("/api/folders/add", dependencies=auth)
    def add_folder(body: AddFolderBody):
        try:
            resolved = controller.add_folder(body.path, merge=body.merge)
        except FolderNeedsMerge as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "code": "merge",
                    "children": [str(c) for c in exc.children],
                },
            ) from exc
        except FolderRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "path": str(resolved)}

    @app.post("/api/folders/preview", dependencies=auth)
    def folder_preview(body: PathBody):
        return controller.preview_folder(body.path)

    @app.post("/api/folders/pick", dependencies=auth)
    def folder_pick():
        try:
            return {"path": controller.pick_folder()}
        except UserError as exc:
            raise _bad(exc) from exc

    @app.get("/api/folders/dropped", dependencies=auth)
    def folder_dropped():
        return {"path": controller.drops.take()}

    @app.get("/api/folders/tree", dependencies=auth)
    def folder_tree(root: str):
        try:
            return controller.folder_tree(root)
        except UserError as exc:
            raise _bad(exc) from exc

    @app.post("/api/folders/excluded", dependencies=auth)
    def folder_excluded(body: ExcludedBody):
        try:
            return {"ok": True, "excluded": controller.set_excluded(body.root, body.excluded)}
        except UserError as exc:
            raise _bad(exc) from exc

    @app.post("/api/folders/open", dependencies=auth)
    def folder_open(body: OpenBody):
        try:
            controller.open_location(body.target)
        except UserError as exc:
            raise _bad(exc) from exc
        return {"ok": True}

    @app.post("/api/folders/remove", dependencies=auth)
    def remove_folder(body: PathBody):
        return {"ok": controller.remove_folder(body.path)}

    @app.post("/api/secret/api-key", dependencies=auth)
    def set_key(body: KeyBody):
        try:
            secret_store.set_api_key(body.value.strip())
        except secret_store.SecretStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        controller.refresh_provider()
        return {"ok": True}

    @app.delete("/api/secret/api-key", dependencies=auth)
    def delete_key():
        secret_store.delete_api_key()
        controller.refresh_provider()
        return {"ok": True}

    @app.get("/api/notifications", dependencies=auth)
    def notifications():
        return controller.inbox.recent(30)

    @app.get("/api/diagnostics", dependencies=auth)
    def diagnostics():
        return {"text": controller.diagnostics()}

    @app.post("/api/data/purge", dependencies=auth)
    def purge(body: PurgeBody):
        if body.confirm != PURGE_CONFIRM:
            raise HTTPException(status_code=400, detail=f"확인 문구 '{PURGE_CONFIRM}'를 정확히 입력하세요.")
        return controller.purge_all_data()

    @app.get("/api/autostart", dependencies=auth)
    def autostart_get():
        auto = controller.autostart
        supported = bool(auto is not None and auto.supported)
        return {"supported": supported, "enabled": bool(supported and auto.is_enabled())}

    @app.post("/api/autostart", dependencies=auth)
    def autostart_set(body: AutostartBody):
        auto = controller.autostart
        if auto is None or not auto.supported:
            raise HTTPException(status_code=400, detail="자동 실행은 Windows에서만 지원합니다.")
        if body.enabled:
            auto.enable()
        else:
            auto.disable()
        return {"supported": True, "enabled": auto.is_enabled()}

    @app.post("/api/app/activate", dependencies=auth)
    def activate(body: ActivateBody):
        page = body.page if (body.page == "/quick" or PAGE_HASH.match(body.page)) else ""
        return {"ok": controller.activate(page)}

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "입력값이 올바르지 않습니다. 비어 있거나 너무 긴 항목이 없는지 확인하세요."}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("web error")
        return JSONResponse({"detail": "서버 오류가 발생했습니다."}, status_code=500)

    return app


class WebServer:
    """Runs uvicorn on a background thread, bound to 127.0.0.1 only."""

    def __init__(self, app: FastAPI, port: int) -> None:
        import uvicorn

        # log_config=None: use the program's own logging. uvicorn's default config writes to
        # stdout, which does not exist in the windowed exe and would stop the server at start.
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False, log_config=None
        )
        self.server = uvicorn.Server(config)
        self.thread: threading.Thread | None = None
        self.failed = False

    def start(self, timeout: float = 10.0) -> None:
        def run() -> None:
            try:
                self.server.run()
            except SystemExit:
                self.failed = True

        self.thread = threading.Thread(target=run, name="web-ui", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started:
                return
            if self.failed:
                break
            time.sleep(0.05)
        raise RuntimeError("웹 UI 서버를 시작하지 못했습니다. 포트가 이미 사용 중일 수 있습니다.")

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)
