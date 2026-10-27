"""FastAPI application: login, password settings, model control and status.

Scope covers the console requirements: authentication, password change,
starting/stopping the two supported generation models on an explicitly
selected GPU, a run-status page, the business-style UI, and the H3
text-to-video generation page (submit, progress, playback).  There is no
gallery or multi-user workspace feature here.
"""

from __future__ import annotations

import hmac
import os
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import (
    AuthError,
    InvalidCredentials,
    PasswordPolicyError,
    Session,
    SessionStore,
    UserStore,
    validate_new_password,
    PASSWORD_MIN_LENGTH,
)
from .catalog import CATALOG, MODEL_ORDER
from .config import APP_NAME, APP_SUBTITLE, SESSION_COOKIE, Settings
from .generation import (
    ASPECT_RATIO,
    DEFAULT_DURATION,
    DEFAULT_RESOLUTION,
    DURATIONS,
    RESOLUTIONS,
    GenerationError,
    GenerationManager,
    GenerationValidationError,
)
from .gpu import GpuProbe
from .manager import ControlError, ModelManager
from .modelaccess import ModelAccessError, ModelAccessStore
from .status import StatusCollector

BASE_DIR = Path(__file__).resolve().parent


class AuthRequired(Exception):
    """Raised by the auth dependency; converted into redirect or JSON 401."""


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/control"


def _redirect(path: str, *, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    params = []
    if notice:
        params.append(f"notice={quote(notice)}")
    if error:
        params.append(f"error={quote(error)}")
    location = path + ("?" + "&".join(params) if params else "")
    return RedirectResponse(location, status_code=303)


def _redirect(path: str, *, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    params = []
    if notice:
        params.append(f"notice={quote(notice)}")
    if error:
        params.append(f"error={quote(error)}")
    location = path + ("?" + "&".join(params) if params else "")
    return RedirectResponse(location, status_code=303)


def _lan_ip() -> str | None:
    """Best-effort local address of the default route; ``None`` when offline.

    A UDP socket does not send packets, it only asks the kernel which source
    address would be used.  Never guessed from the hostname.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return address if address and not address.startswith("127.") else None


def _console_port() -> int:
    raw = os.environ.get("GEN_STUDIO_PORT", "8090")
    try:
        port = int(raw)
    except ValueError:
        return 8090
    return port if 1 <= port <= 65535 else 8090


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()

    users = UserStore(settings.auth_file)
    sessions = SessionStore(settings.session_ttl_seconds)
    gpu_probe = GpuProbe(settings.nvidia_smi)
    manager = ModelManager(settings, gpu_probe)
    collector = StatusCollector(settings, gpu_probe, manager)
    model_access_store = ModelAccessStore(settings.data_dir / "model_access.json")
    generation_manager = GenerationManager(settings)

    app = FastAPI(
        title=f"{APP_NAME} 控制台",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.users = users
    app.state.sessions = sessions
    app.state.gpu_probe = gpu_probe
    app.state.manager = manager
    app.state.collector = collector
    app.state.model_access_store = model_access_store
    app.state.generation_manager = generation_manager

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["gib"] = lambda mb: f"{mb / 1024:.1f}" if mb is not None else "—"

    def _localtime(value: str | None) -> str | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value

    templates.env.filters["localtime"] = _localtime

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    # ------------------------------------------------------------- middleware
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            host = request.headers.get("host", "")
            if urlsplit(origin).netloc != host:
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ------------------------------------------------------------- utilities
    def get_session(request: Request) -> Session | None:
        return sessions.get(request.cookies.get(SESSION_COOKIE))

    def require_user(request: Request) -> Session:
        session = get_session(request)
        if session is None:
            raise AuthRequired()
        return session

    def csrf_ok(session: Session, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, session.csrf_token)

    @app.exception_handler(AuthRequired)
    async def auth_required_handler(request: Request, exc: AuthRequired):
        accepts_json = "application/json" in request.headers.get("accept", "")
        if request.url.path.startswith("/api/") or accepts_json:
            return JSONResponse({"detail": "未登录", "login_url": "/login"}, status_code=401)
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)

    def render(
        request: Request,
        template: str,
        *,
        active: str = "",
        status_code: int = 200,
        session: Session | None = None,
        **context,
    ) -> HTMLResponse:
        base = {
            "app_name": APP_NAME,
            "app_subtitle": APP_SUBTITLE,
            "active": active,
            "username": session.username if session else None,
            "csrf_token": session.csrf_token if session else "",
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        }
        base.update(context)
        return templates.TemplateResponse(
            request=request,
            name=template,
            context=base,
            status_code=status_code,
        )

    def control_context(request: Request, session: Session) -> dict:
        listing = request.app.state.gpu_probe.list()
        cards = []
        for model_id in MODEL_ORDER:
            spec = CATALOG[model_id]
            detection = manager.detect(model_id)
            state = manager.state(model_id)
            # The control page reports ownership (who runs the process); the
            # status page owns detailed health and uptime.
            if detection.managed:
                if detection.unit_name:
                    label, tone = "运行中（systemd 单元）", "success"
                else:
                    label, tone = "运行中（本系统）", "success"
            elif detection.unmanaged:
                if detection.marker_match and not detection.port_open:
                    label, tone = "启动中（外部进程）", "warning"
                elif detection.marker_match:
                    label, tone = "运行中（外部进程）", "warning"
                else:
                    label, tone = "端口冲突", "danger"
            elif state.last_status == "failed":
                label, tone = "已停止（上次启动失败）", "danger"
            else:
                label, tone = "已停止", "neutral"

            can_start = (
                listing.available
                and not detection.port_open
                and not detection.managed
                and not detection.unmanaged
            )
            can_stop = bool(detection.managed or (detection.unmanaged and detection.marker_match))
            cards.append(
                {
                    "spec": spec,
                    "state_label": label,
                    "tone": tone,
                    "pid": detection.pid,
                    "managed": detection.managed,
                    "gpu_index": detection.gpu_index,
                    "gpu_source": detection.gpu_source,
                    "start_epoch": detection.start_epoch,
                    "port_open": detection.port_open,
                    "unit_name": detection.unit_name,
                    "note": detection.note or (state.last_error if state.last_status == "failed" else None),
                    "can_start": can_start,
                    "can_stop": can_stop,
                    "failed": state.last_status == "failed",
                    "access_host": request.app.state.model_access_store.get(model_id),
                }
            )
        return {
            "active": "control",
            "gpus": listing,
            "cards": cards,
            "access_error": request.app.state.model_access_store.error,
            "default_password": users.is_default_password(session.username),
        }

    # ----------------------------------------------------------------- pages
    @app.get("/", include_in_schema=False)
    async def root(request: Request):
        if get_session(request) is None:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/control", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        session = get_session(request)
        if session is not None:
            return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
        return render(request, "login.html", next_path=_safe_next(request.query_params.get("next")))

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next_path: str = Form("/control"),
    ):
        username = username.strip()
        if not users.verify(username, password):
            return render(
                request,
                "login.html",
                status_code=401,
                error="用户名或密码不正确",
                next_path=_safe_next(next_path),
                username_value=username,
            )
        session = sessions.create(username)
        response = RedirectResponse(_safe_next(next_path), status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session.token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request, csrf_token: str = Form("")):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/control", error="会话校验失败，请重试")
        sessions.delete(session.token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/control", response_class=HTMLResponse)
    async def control_page(request: Request):
        session = require_user(request)
        return render(request, "control.html", session=session, **control_context(request, session))

    @app.get("/control/{model_id}/access")
    async def access_get(request: Request, model_id: str):
        require_user(request)
        return _redirect(
            "/control",
            error=(
                "模型访问地址需要通过页面表单保存（POST）；"
                "直接访问该地址不会修改任何配置。"
            ),
        )

    @app.post("/control/{model_id}/access")
    async def access_submit(
        request: Request,
        model_id: str,
        access_host: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/control", error="会话校验失败，请刷新页面后重试")
        if model_id not in CATALOG:
            return _redirect("/control", error=f"未知模型：{model_id}")
        store = request.app.state.model_access_store
        try:
            store.set(model_id, access_host)
        except ModelAccessError as exc:
            return _redirect("/control", error=str(exc))
        spec = CATALOG[model_id]
        saved = store.get(model_id)
        detection = manager.detect(model_id)
        note = f"{spec.name} 的模型访问地址已保存为 {saved}，下次启动时生效"
        if detection.pid or detection.port_open:
            note += "；当前进程仍在运行，需先停止并重新启动该模型后才会按新地址监听"
        return _redirect("/control", notice=note)

    @app.get("/control/{model_id}/start")
    async def start_get(request: Request, model_id: str):
        require_user(request)
        return _redirect(
            "/control",
            error="启动模型请使用页面上的「启动模型」按钮（POST）；直接访问不会启动任何进程。",
        )

    @app.post("/control/{model_id}/start")
    async def start_model(
        request: Request,
        model_id: str,
        gpu_index: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/control", error="会话校验失败，请刷新页面后重试")
        if model_id not in CATALOG:
            return _redirect("/control", error=f"未知模型：{model_id}")
        raw = gpu_index.strip()
        if not raw:
            return _redirect("/control", error="必须选择一张显卡后才能启动（显卡为必选项）")
        try:
            index = int(raw)
        except ValueError:
            return _redirect("/control", error=f"显卡参数无效：{raw}")
        access_host = request.app.state.model_access_store.get(model_id)
        try:
            manager.start(model_id, index, access_host)
        except ControlError as exc:
            return _redirect("/control", error=str(exc))
        spec = CATALOG[model_id]
        unit_note = " 的独立 systemd 单元" if manager.uses_systemd_units() else ""
        return _redirect(
            "/control",
            notice=(
                f"已在 GPU {index} 上启动 {spec.name}{unit_note}（访问地址 {access_host}:{spec.port}），"
                "加载中；请在运行状态页查看进度"
            ),
        )

    @app.get("/control/{model_id}/stop")
    async def stop_get(request: Request, model_id: str):
        require_user(request)
        return _redirect(
            "/control",
            error="停止模型请使用页面上的「停止模型」按钮（POST）；直接访问不会停止任何进程。",
        )

    @app.post("/control/{model_id}/stop")
    async def stop_model(request: Request, model_id: str, csrf_token: str = Form("")):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/control", error="会话校验失败，请刷新页面后重试")
        if model_id not in CATALOG:
            return _redirect("/control", error=f"未知模型：{model_id}")
        try:
            message = manager.stop(model_id)
        except ControlError as exc:
            return _redirect("/control", error=str(exc))
        return _redirect("/control", notice=message)

    @app.get("/generate", response_class=HTMLResponse)
    async def generate_page(request: Request):
        session = require_user(request)
        detection = manager.detect("minimax-h3")
        return render(
            request,
            "generate.html",
            session=session,
            active="generate",
            resolutions=RESOLUTIONS,
            durations=DURATIONS,
            default_resolution=DEFAULT_RESOLUTION,
            default_duration=DEFAULT_DURATION,
            aspect_ratio=ASPECT_RATIO,
            h3_port_open=detection.port_open,
            h3_loading=bool(detection.pid and not detection.port_open),
        )

    @app.post("/api/generate")
    async def api_generate(
        request: Request,
        prompt: str = Form(""),
        resolution: str = Form(""),
        duration: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return JSONResponse(
                {"error": "会话校验失败，请刷新页面后重试"}, status_code=403
            )
        try:
            job = request.app.state.generation_manager.start(
                prompt, resolution, duration
            )
        except GenerationValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except GenerationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse({"job": job}, status_code=201)

    @app.get("/api/generate/{job_id}/video")
    async def api_generate_video(request: Request, job_id: str):
        require_user(request)
        path = request.app.state.generation_manager.video_path(job_id)
        if path is None:
            return JSONResponse(
                {"error": "视频尚未生成完成，或任务不存在"}, status_code=404
            )
        return FileResponse(
            path,
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'inline; filename="h3-{job_id}.mp4"',
                "Cache-Control": "private, no-store",
            },
        )

    @app.get("/api/generate/{job_id}")
    async def api_generate_status(request: Request, job_id: str):
        require_user(request)
        job = request.app.state.generation_manager.get(job_id)
        if job is None:
            return JSONResponse(
                {"error": "生成任务不存在或已过期"}, status_code=404
            )
        return JSONResponse({"job": job})

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request):
        session = require_user(request)
        payload = collector.collect()
        return render(request, "status.html", session=session, active="status", payload=payload)

    @app.get("/status/partial", response_class=HTMLResponse)
    async def status_partial(request: Request):
        session = require_user(request)
        payload = collector.collect()
        return render(request, "_status_body.html", session=session, payload=payload)

    @app.get("/api/status")
    async def api_status(request: Request):
        require_user(request)
        return JSONResponse(collector.collect())

    @app.get("/password", response_class=HTMLResponse)
    async def password_page(request: Request):
        session = require_user(request)
        return render(
            request,
            "password.html",
            session=session,
            active="password",
            updated_at=users.updated_at(session.username),
            default_password=users.is_default_password(session.username),
            min_length=PASSWORD_MIN_LENGTH,
        )

    @app.post("/password", response_class=HTMLResponse)
    async def password_submit(
        request: Request,
        current_password: str = Form(""),
        new_password: str = Form(""),
        confirm_password: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/password", error="会话校验失败，请刷新页面后重试")
        try:
            validate_new_password(new_password, current_password, confirm_password)
            users.change_password(session.username, current_password, new_password)
        except PasswordPolicyError as exc:
            return _redirect("/password", error=str(exc))
        except (InvalidCredentials, AuthError) as exc:
            return _redirect("/password", error=str(exc))
        revoked = sessions.revoke_others(session.username, session.token)
        note = "密码已更新，当前会话保持登录"
        if revoked:
            note += f"；已注销其它 {revoked} 个会话"
        return _redirect("/password", notice=note)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return JSONResponse({"status": "ok"})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return RedirectResponse("/static/favicon.svg", status_code=307)

    return app


app = create_app()
