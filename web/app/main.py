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

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
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
from .imagegen import (
    ASPECT_RATIOS as IMAGE_ASPECT_RATIOS,
    DEFAULT_ASPECT as IMAGE_DEFAULT_ASPECT,
    DEFAULT_RESOLUTION as IMAGE_DEFAULT_RESOLUTION,
    RESOLUTIONS as IMAGE_RESOLUTIONS,
    ImageClient,
    ImageError,
    ImageServiceError,
    ImageValidationError,
    preview_size as preview_image_size,
    resolve_size as resolve_image_size,
    snap_resolution,
    validate_request as validate_image_request,
    validate_upload,
)
from .apikeys import ApiKeyError, ApiKeyStore, MODE_KEY, MODE_OPEN
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


def _image_service_port(base_url: str) -> int:
    """Port of the image service parsed from its base URL; 30021 when unclear."""

    try:
        port = urlsplit(base_url or "").port
    except ValueError:
        port = None
    return port if isinstance(port, int) and 1 <= port <= 65535 else 30021


def _api_mode_label(mode: str, enabled_count: int) -> str:
    """Headline for the access mode agreed with the image service."""

    if mode == MODE_OPEN:
        return "开放（未配置 API Key，局域网免凭证可调用）"
    if mode == MODE_KEY:
        return f"需 Key（{enabled_count} 个可用）"
    return "已锁定（无可用 Key，仅本机回环可调用）"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()

    users = UserStore(settings.auth_file)
    sessions = SessionStore(settings.session_ttl_seconds, settings.sessions_file)
    gpu_probe = GpuProbe(settings.nvidia_smi)
    manager = ModelManager(settings, gpu_probe)
    collector = StatusCollector(settings, gpu_probe, manager)
    model_access_store = ModelAccessStore(settings.data_dir / "model_access.json")
    api_key_store = ApiKeyStore(
        settings.data_dir / "api_keys.json",
        settings.data_dir / "api_keys_usage.json",
    )
    generation_manager = GenerationManager(settings)
    # The console authenticates itself with the lowest-id enabled API key so
    # it keeps working when the service base URL is a LAN address.
    image_client = ImageClient(
        os.environ.get("GEN_STUDIO_IMAGE_API_BASE", "http://127.0.0.1:30021"),
        api_key_provider=api_key_store.first_enabled_key,
    )

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
    app.state.api_key_store = api_key_store
    app.state.generation_manager = generation_manager
    app.state.image_client = image_client

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
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
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

    def apikeys_context(request: Request) -> dict:
        """Rows, access mode and Base URLs for the API Keys page."""

        store = request.app.state.api_key_store
        usage = store.usage()
        rows = []
        for item in store.list():
            stat = usage.get(item.id, {})
            rows.append(
                {
                    "id": item.id,
                    "key": item.key,
                    "masked": item.masked,
                    "description": item.description,
                    "created_at": item.created_at,
                    "enabled": item.enabled,
                    "calls": stat.get("calls", 0),
                    "last_used_at": stat.get("last_used_at"),
                }
            )
        mode, enabled_count = store.access_mode()
        lan_ip = _lan_ip()
        lan_note = None
        if not lan_ip:
            lan_ip = "127.0.0.1"
            lan_note = "未能检测到局域网地址，已回退到 127.0.0.1，下列地址仅本机可用"
        port = _image_service_port(getattr(request.app.state.image_client, "base_url", ""))
        return {
            "api_keys": rows,
            "api_mode": mode,
            "api_mode_label": _api_mode_label(mode, enabled_count),
            "api_mode_tone": "success" if mode == MODE_KEY else "warn",
            "api_store_error": store.error,
            "base_openai": f"http://{lan_ip}:{port}/v1",
            "base_native": f"http://{lan_ip}:{port}",
            "lan_note": lan_note,
            "service_port": port,
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

    @app.get("/image", response_class=HTMLResponse)
    async def image_page(request: Request):
        session = require_user(request)
        client = request.app.state.image_client
        healthy = await run_in_threadpool(client.healthy)
        return render(
            request,
            "image.html",
            session=session,
            active="image",
            resolutions=IMAGE_RESOLUTIONS,
            default_resolution=IMAGE_DEFAULT_RESOLUTION,
            aspect_ratios=(("", "自动（无参考图=方形，有参考图=跟随参考图）"),
                           *[(key, key) for key in IMAGE_ASPECT_RATIOS]),
            default_aspect=IMAGE_DEFAULT_ASPECT,
            api_base=client.base_url,
            api_healthy=healthy,
        )

    @app.get("/api/image/size")
    async def api_image_size(request: Request, resolution: str = "1024", aspect: str = ""):
        """Effective canvas for the current form values (32-grid snapping)."""

        require_user(request)
        try:
            value = int(str(resolution).strip())
        except (TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "error": "分辨率必须是整数"}, status_code=400
            )
        if value not in IMAGE_RESOLUTIONS:
            allowed = " / ".join(str(item) for item in IMAGE_RESOLUTIONS)
            return JSONResponse(
                {"ok": False, "error": f"分辨率只支持 {allowed}，收到的是 {value}"},
                status_code=400,
            )
        try:
            preview = preview_image_size(value, aspect)
        except ImageValidationError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, **preview})

    @app.post("/api/image/generate")
    async def api_image_generate(
        request: Request,
        prompt: str = Form(""),
        resolution: str = Form(""),
        seed: str = Form(""),
        aspect: str = Form(""),
        csrf_token: str = Form(""),
        file: UploadFile | None = File(None),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return JSONResponse(
                {"ok": False, "error": "会话校验失败，请刷新页面后重试"}, status_code=403
            )
        try:
            clean_prompt, clean_resolution, clean_seed = validate_image_request(
                prompt, resolution, seed
            )
            snapped = snap_resolution(clean_resolution)
            size = resolve_image_size(aspect, snapped)
            upload: tuple[str, bytes] | None = None
            if file is not None:
                data = await file.read()
                upload = (validate_upload(file.filename, file.content_type, data), data)
            # The upstream call blocks for tens of seconds: keep it off the
            # event loop so the rest of the console stays responsive.
            result = await run_in_threadpool(
                request.app.state.image_client.generate,
                clean_prompt,
                snapped,
                seed=clean_seed,
                upload=upload,
                width=size[0] if size else None,
                height=size[1] if size else None,
            )
        except ImageValidationError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except ImageServiceError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status_code)
        except ImageError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        payload = {"ok": True}
        payload.update(result)
        return JSONResponse(payload)

    # -------------------------------------------------------------- API Keys
    @app.get("/apikeys", response_class=HTMLResponse)
    async def apikeys_page(request: Request):
        session = require_user(request)
        return render(
            request,
            "apikeys.html",
            session=session,
            active="apikeys",
            **apikeys_context(request),
        )

    @app.post("/apikeys", response_class=HTMLResponse)
    async def apikeys_create(
        request: Request,
        description: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/apikeys", error="会话校验失败，请刷新页面后重试")
        store = request.app.state.api_key_store
        try:
            created = store.create(description)
        except ApiKeyError as exc:
            return _redirect("/apikeys", error=str(exc))
        return _redirect(
            "/apikeys",
            notice=f"已创建 API Key #{created.id}（{created.masked}），可在列表中复制完整密钥",
        )

    @app.get("/apikeys/{key_id}/edit")
    async def apikeys_edit_get(request: Request, key_id: int):
        require_user(request)
        return _redirect(
            "/apikeys",
            error="修改描述请使用列表行内的「编辑」表单（POST）；直接访问不会修改任何配置。",
        )

    @app.post("/apikeys/{key_id}/edit")
    async def apikeys_edit(
        request: Request,
        key_id: int,
        description: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/apikeys", error="会话校验失败，请刷新页面后重试")
        store = request.app.state.api_key_store
        try:
            store.edit(key_id, description)
        except ApiKeyError as exc:
            return _redirect("/apikeys", error=str(exc))
        return _redirect("/apikeys", notice=f"已更新 API Key #{key_id} 的描述")

    @app.get("/apikeys/{key_id}/toggle")
    async def apikeys_toggle_get(request: Request, key_id: int):
        require_user(request)
        return _redirect(
            "/apikeys",
            error="启用 / 停用请使用列表中的状态按钮（POST）；直接访问不会改变任何状态。",
        )

    @app.post("/apikeys/{key_id}/toggle")
    async def apikeys_toggle(request: Request, key_id: int, csrf_token: str = Form("")):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/apikeys", error="会话校验失败，请刷新页面后重试")
        store = request.app.state.api_key_store
        try:
            updated = store.toggle(key_id)
        except ApiKeyError as exc:
            return _redirect("/apikeys", error=str(exc))
        state = "启用中" if updated.enabled else "已停用"
        return _redirect("/apikeys", notice=f"API Key #{key_id} 当前状态：{state}")

    @app.get("/apikeys/{key_id}/reset")
    async def apikeys_reset_get(request: Request, key_id: int):
        require_user(request)
        return _redirect(
            "/apikeys",
            error="重置密钥请使用列表中的「重置」按钮（POST）；直接访问不会更换任何密钥。",
        )

    @app.post("/apikeys/{key_id}/reset")
    async def apikeys_reset(request: Request, key_id: int, csrf_token: str = Form("")):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/apikeys", error="会话校验失败，请刷新页面后重试")
        store = request.app.state.api_key_store
        try:
            store.reset(key_id)
        except ApiKeyError as exc:
            return _redirect("/apikeys", error=str(exc))
        return _redirect("/apikeys", notice=f"API Key #{key_id} 已重置，旧密钥立即失效")

    @app.get("/apikeys/{key_id}/delete")
    async def apikeys_delete_get(request: Request, key_id: int):
        require_user(request)
        return _redirect(
            "/apikeys",
            error="删除请使用列表中的「删除」按钮（POST）；直接访问不会删除任何密钥。",
        )

    @app.post("/apikeys/{key_id}/delete")
    async def apikeys_delete(request: Request, key_id: int, csrf_token: str = Form("")):
        session = require_user(request)
        if not csrf_ok(session, csrf_token):
            return _redirect("/apikeys", error="会话校验失败，请刷新页面后重试")
        store = request.app.state.api_key_store
        try:
            store.delete(key_id)
        except ApiKeyError as exc:
            return _redirect("/apikeys", error=str(exc))
        return _redirect("/apikeys", notice=f"API Key #{key_id} 已删除")

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
