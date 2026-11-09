"""Synchronous image generation through the local Qwen-Image HTTP service.

The console proxies ``POST /generate`` instead of letting the browser call the
service directly: the console is served from a different origin/port and its
CSP restricts ``connect-src`` to ``'self'``, so a direct cross-origin fetch is
blocked.  Proxying also keeps the service address configurable in one place
(``GEN_STUDIO_IMAGE_API_BASE``) and lets the page reuse the console session
and CSRF check.

The upstream call is synchronous (one image takes tens of seconds and the
service exposes no progress endpoint), so the HTTP request here blocks until
the image is done; the page shows elapsed time plus an indeterminate progress
bar while waiting.

Standard library only, matching the rest of the console.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

#: Image sizes offered by this page.  The service accepts a 64-2048 pixel
#: budget on a 32-pixel grid; these presets are the ones measured on this box.
RESOLUTIONS: tuple[int, ...] = (768, 1024, 1328, 1536, 2048)
DEFAULT_RESOLUTION = 1024

#: The Turbo checkpoint runs at cfg=1, so a negative prompt would be ignored;
#: it is deliberately not exposed.
MAX_PROMPT_LENGTH = 20_000

#: Reference image limits (the service itself rejects non-images and >16 files).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_UPLOAD_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

DEFAULT_BASE_URL = "http://127.0.0.1:30021"
DEFAULT_GENERATE_TIMEOUT = 600.0
DEFAULT_HEALTH_TIMEOUT = 3.0


class ImageError(Exception):
    """A user-facing image generation failure."""

    status_code = 502


class ImageValidationError(ImageError):
    """The submitted prompt, parameters or upload are invalid."""

    status_code = 400


class ImageServiceError(ImageError):
    """The image service could not be reached or returned malformed data."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_request(
    prompt: str | None,
    resolution: str | int | None,
    seed: str | int | None,
) -> tuple[str, int, int | None]:
    """Validate the form without silently changing any value."""

    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise ImageValidationError("提示词不能为空，请先输入要生成的画面内容")
    if len(clean_prompt) > MAX_PROMPT_LENGTH:
        raise ImageValidationError(
            f"提示词过长：最多 {MAX_PROMPT_LENGTH} 个字符，当前 {len(clean_prompt)} 个"
        )

    try:
        clean_resolution = int(str(resolution).strip())
    except (TypeError, ValueError):
        raise ImageValidationError("分辨率必须是整数") from None
    if clean_resolution not in RESOLUTIONS:
        allowed = " / ".join(str(item) for item in RESOLUTIONS)
        raise ImageValidationError(
            f"分辨率只支持 {allowed}，收到的是 {clean_resolution}"
        )

    raw_seed = (str(seed).strip() if seed is not None else "")
    if not raw_seed:
        return clean_prompt, clean_resolution, None
    try:
        clean_seed = int(raw_seed)
    except ValueError:
        raise ImageValidationError("随机种子必须是非负整数，留空表示每次随机") from None
    if clean_seed < 0 or clean_seed > 2**63 - 1:
        raise ImageValidationError("随机种子超出范围：0 到 2^63-1")
    return clean_prompt, clean_resolution, clean_seed


def sniff_image(data: bytes) -> str | None:
    """Return the image extension from magic bytes, or ``None`` if not an image."""

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def validate_upload(filename: str | None, content_type: str | None, data: bytes) -> str:
    """Validate an uploaded reference image and return its filename to send."""

    if not data:
        raise ImageValidationError("参考图文件是空的，请重新选择")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageValidationError(
            f"参考图过大：最多 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB，"
            f"当前 {len(data) / (1024 * 1024):.1f} MB"
        )
    extension = sniff_image(data)
    if extension is None:
        raise ImageValidationError("参考图不是支持的图片格式（仅 png / jpg / webp / gif）")
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared and declared not in ALLOWED_UPLOAD_TYPES:
        # Declared type can lie, but an obviously wrong one is worth reporting.
        raise ImageValidationError(f"参考图的类型不受支持：{declared}")
    return f"reference{extension}"


def _multipart_body(
    fields: dict[str, str], upload: tuple[str, bytes] | None
) -> tuple[bytes, str]:
    """Build a minimal multipart/form-data body (stdlib has no encoder)."""

    boundary = f"----genstudio{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    if upload is not None:
        filename, data = upload
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


@dataclass(frozen=True)
class GeneratedImage:
    filename: str
    subfolder: str
    width: int | None
    height: int | None
    b64: str | None

    def public(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "subfolder": self.subfolder,
            "width": self.width,
            "height": self.height,
            "b64": self.b64,
        }


class ImageClient:
    """Minimal client for the local Qwen-Image ``/generate`` endpoint."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_GENERATE_TIMEOUT,
        health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.health_timeout = float(health_timeout)

    # ------------------------------------------------------------------ health
    def healthy(self) -> bool:
        request = Request(f"{self.base_url}/health", method="GET")
        try:
            with urlopen(request, timeout=self.health_timeout) as response:
                raw = response.read()
            return bool(json.loads(raw.decode("utf-8")).get("ok"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return False

    # ---------------------------------------------------------------- generate
    def generate(
        self,
        prompt: str,
        resolution: int,
        *,
        seed: int | None = None,
        upload: tuple[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """Submit one generation; returns ``{"image": …, "elapsed_s": …}``."""

        fields = {"prompt": prompt, "resolution": str(resolution), "return_b64": "true"}
        if seed is not None:
            fields["seed"] = str(seed)

        if upload is not None:
            body, content_type = _multipart_body(fields, upload)
        else:
            body = json.dumps(fields).encode("utf-8")
            content_type = "application/json"

        request = Request(
            f"{self.base_url}/generate",
            data=body,
            method="POST",
            headers={"Content-Type": content_type, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise ImageServiceError(
                f"生图服务返回 HTTP {exc.code}：{_error_detail(exc.read())}"
            ) from exc
        except (TimeoutError, OSError) as exc:
            # urlopen raises socket.timeout (a TimeoutError) for slow calls.
            raise ImageServiceError(
                f"等待生图超时（{int(self.timeout)} 秒）：{exc}", status_code=504
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise ImageServiceError(
                    f"等待生图超时（{int(self.timeout)} 秒）：{reason}", status_code=504
                ) from exc
            raise ImageServiceError(
                f"无法连接生图服务（{self.base_url}），请确认服务已启动：{reason}"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ImageServiceError("生图服务返回的响应不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ImageServiceError("生图服务返回格式异常（顶层不是对象）")
        if not payload.get("ok"):
            error = payload.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else None
            raise ImageServiceError(f"生图失败：{message or '服务未返回原因'}")

        images = payload.get("images")
        if not isinstance(images, list) or not images:
            raise ImageServiceError("生图服务没有返回任何图片")
        first = images[0]
        if not isinstance(first, dict) or not first.get("filename"):
            raise ImageServiceError("生图服务返回的图片信息不完整（缺少文件名）")

        b64 = first.get("b64")
        image = GeneratedImage(
            filename=str(first["filename"]),
            subfolder=str(first.get("subfolder") or ""),
            width=_as_int(first.get("width")),
            height=_as_int(first.get("height")),
            b64=b64 if isinstance(b64, str) and b64 else None,
        )
        return {
            "image": image.public(),
            "elapsed_s": payload.get("elapsed_s"),
            "seed": payload.get("seed"),
            "resolution": payload.get("resolution"),
        }


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return text[:400] or "（空响应）"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return text[:400]


def decode_b64_image(b64: str) -> bytes | None:
    """Decode a base64 payload, tolerating a ``data:image/png;base64,`` prefix."""

    text = b64
    if "base64," in text[:120]:
        text = text.split("base64,", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        return None
