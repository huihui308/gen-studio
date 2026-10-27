"""Asynchronous H3 text-to-video generation jobs.

The console talks to the local SGLang deployment over its OpenAI-style
``/v1/videos`` API.  A generation takes minutes, so the HTTP request only
creates a job; a background thread polls H3, downloads the finished MP4 and
keeps the page's progress endpoint up to date.

Everything in this module uses the Python standard library.  That keeps the
runtime dependency list unchanged and makes the job lifecycle easy to test
with an injected fake client.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import Settings

#: H3 target resolutions exposed by this UI: the short edge of the frame.
RESOLUTIONS: tuple[int, ...] = (480, 736)

#: H3's documented clip range.  Values are whole seconds.
DURATIONS: tuple[int, ...] = tuple(range(4, 16))

DEFAULT_RESOLUTION = 480
DEFAULT_DURATION = 10

#: The local deployment is tested with 16:9.  Keeping it fixed avoids adding
#: an aspect-ratio control that was not requested.
ASPECT_RATIO = "16:9"

NUM_INFERENCE_STEPS = 20
FLOW_SHIFT = 12.0
AUDIO_FLOW_SHIFT = 3.0
DEFAULT_SEED = 1101

MAX_PROMPT_LENGTH = 20_000
H3_DEFAULT_BASE_URL = "http://127.0.0.1:30010"
DEFAULT_POLL_INTERVAL = 3.0
DEFAULT_MAX_WAIT_SECONDS = 90 * 60
DEFAULT_HTTP_TIMEOUT = 30.0
DEFAULT_DOWNLOAD_TIMEOUT = 5 * 60


class GenerationError(Exception):
    """A user-facing generation failure."""


class GenerationValidationError(GenerationError):
    """The submitted prompt or parameters are invalid; nothing was started."""


class H3Error(GenerationError):
    """The H3 service could not be reached or returned malformed data."""


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _error_text(payload: Any) -> str | None:
    """Extract a useful message from H3's error field without trusting it."""

    if payload is None:
        return None
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "code"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            return json.dumps(payload, ensure_ascii=False)[:400]
        except (TypeError, ValueError):
            return None
    return str(payload)[:400]


def _json_error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return text[:400] or "（空响应）"
    message = _error_text(payload)
    return message or text[:400] or "（空响应）"


def validate_request(
    prompt: str | None,
    resolution: str | int | None,
    duration: str | int | None,
) -> tuple[str, int, int]:
    """Validate a generation request without silently changing any value."""

    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise GenerationValidationError("提示词不能为空，请先输入要生成的画面内容")
    if len(clean_prompt) > MAX_PROMPT_LENGTH:
        raise GenerationValidationError(
            f"提示词过长：最多 {MAX_PROMPT_LENGTH} 个字符，当前 {len(clean_prompt)} 个"
        )

    try:
        clean_resolution = int(str(resolution).strip())
    except (TypeError, ValueError):
        raise GenerationValidationError("分辨率必须是 480 或 736") from None
    if clean_resolution not in RESOLUTIONS:
        allowed = " / ".join(str(item) for item in RESOLUTIONS)
        raise GenerationValidationError(
            f"分辨率只支持 {allowed}，收到的是 {clean_resolution}"
        )

    try:
        clean_duration = int(str(duration).strip())
    except (TypeError, ValueError):
        raise GenerationValidationError("时长必须是 4 到 15 之间的整数秒") from None
    if clean_duration not in DURATIONS:
        raise GenerationValidationError(
            f"时长只支持 {DURATIONS[0]}–{DURATIONS[-1]} 秒，收到的是 {clean_duration} 秒"
        )

    return clean_prompt, clean_resolution, clean_duration


@dataclass(frozen=True)
class H3Status:
    status: str
    progress: int | None = None
    error: str | None = None


class H3Client:
    """Minimal synchronous client for the local SGLang ``/v1/videos`` API."""

    def __init__(
        self,
        base_url: str = H3_DEFAULT_BASE_URL,
        *,
        model: str | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        download_timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.download_timeout = download_timeout
        self._resolved_model: str | None = None
        self._model_lock = threading.Lock()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request_json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self._url(path), data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            raw = exc.read()
            raise H3Error(
                f"H3 返回 HTTP {exc.code}：{_json_error_detail(raw)}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise H3Error(
                f"无法连接 H3 服务（{self.base_url}），请确认模型已启动：{exc}"
            ) from exc

        if status >= 400:
            raise H3Error(f"H3 返回 HTTP {status}：{_json_error_detail(raw)}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise H3Error("H3 返回的响应不是有效 JSON，无法解析任务信息") from exc
        if not isinstance(data, dict):
            raise H3Error("H3 返回的任务信息格式异常（顶层不是对象）")
        return data

    def _model_id(self) -> str:
        if self.model:
            return self.model
        with self._model_lock:
            if self._resolved_model:
                return self._resolved_model
            data = self._request_json("GET", "/v1/models")
            models = data.get("data")
            if not isinstance(models, list) or not models:
                raise H3Error("H3 /v1/models 未返回可用的模型 ID")
            first = models[0]
            model_id = first.get("id") if isinstance(first, dict) else None
            if not isinstance(model_id, str) or not model_id.strip():
                raise H3Error("H3 /v1/models 返回的模型 ID 格式异常")
            self._resolved_model = model_id
            return model_id

    def submit(self, prompt: str, resolution: int, duration: int) -> str:
        payload = {
            "model": self._model_id(),
            "prompt": prompt,
            "seconds": duration,
            "task": "t2va",
            "conditions": [],
            "target": {
                "short_edge": resolution,
                "aspect_ratio": ASPECT_RATIO,
                "duration_seconds": float(duration),
            },
            "num_outputs_per_prompt": 1,
            "num_inference_steps": NUM_INFERENCE_STEPS,
            "flow_shift": FLOW_SHIFT,
            "audio_flow_shift": AUDIO_FLOW_SHIFT,
            "seed": DEFAULT_SEED,
        }
        data = self._request_json("POST", "/v1/videos", payload)
        job_id = data.get("id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise H3Error("H3 未返回有效的任务 ID，无法跟踪生成进度")
        return job_id

    def status(self, job_id: str) -> H3Status:
        data = self._request_json("GET", f"/v1/videos/{quote(job_id, safe='')}")
        raw_status = data.get("status")
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise H3Error("H3 返回的任务状态为空或格式异常")
        progress_value = data.get("progress")
        progress: int | None = None
        if isinstance(progress_value, (int, float)) and not isinstance(progress_value, bool):
            progress = max(0, min(100, int(progress_value)))
        return H3Status(
            status=raw_status.strip().lower(),
            progress=progress,
            error=_error_text(data.get("error")),
        )

    def download(self, job_id: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        request = Request(
            self._url(f"/v1/videos/{quote(job_id, safe='')}/content"),
            method="GET",
            headers={"Accept": "video/mp4,*/*"},
        )
        content_type = ""
        total = 0
        try:
            with urlopen(request, timeout=self.download_timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                with temporary.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        total += len(chunk)
            # A JSON body with HTTP 200 is a common failure shape; reject it
            # instead of showing an error document inside a <video> element.
            if total == 0:
                raise H3Error("下载视频失败：H3 返回的视频文件为空")
            if "json" in content_type.lower():
                raise H3Error("下载视频失败：H3 返回的是错误信息而不是视频文件")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            temporary.replace(destination)
            return total
        except HTTPError as exc:
            raw = exc.read()
            raise H3Error(
                f"下载视频失败：H3 返回 HTTP {exc.code}：{_json_error_detail(raw)}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise H3Error(f"下载视频失败：无法连接 H3（{exc}）") from exc
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass


@dataclass
class GenerationJob:
    id: str
    prompt: str
    resolution: int
    duration: int
    status: str = "queued"
    stage: str = "已创建任务"
    progress: int | None = None
    h3_job_id: str | None = None
    video_path: Path | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None

    def public(self) -> dict[str, Any]:
        elapsed = ((self.completed_at or _now()) - self.created_at).total_seconds()
        return {
            "id": self.id,
            "prompt": self.prompt,
            "resolution": self.resolution,
            "duration": self.duration,
            "aspect_ratio": ASPECT_RATIO,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "elapsed_seconds": max(0, int(elapsed)),
            "video_url": f"/api/generate/{self.id}/video" if self.video_path else None,
        }


class GenerationManager:
    """Create jobs, poll H3 in a daemon thread and retain results in memory."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: H3Client | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        output_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or H3Client(
            os.environ.get("GEN_STUDIO_H3_BASE_URL", H3_DEFAULT_BASE_URL),
            model=str(settings.repo_root / "h3" / "models" / "MiniMax-H3"),
        )
        self.poll_interval = max(0.01, float(poll_interval))
        self.max_wait_seconds = max(1.0, float(max_wait_seconds))
        self.output_dir = Path(output_dir) if output_dir else settings.data_dir / "generated"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.output_dir.chmod(0o700)
        except OSError:
            pass
        self._jobs: dict[str, GenerationJob] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- creation
    def start(
        self, prompt: str | None, resolution: str | int | None, duration: str | int | None
    ) -> dict[str, Any]:
        clean_prompt, clean_resolution, clean_duration = validate_request(
            prompt, resolution, duration
        )
        job = GenerationJob(
            id=uuid.uuid4().hex,
            prompt=clean_prompt,
            resolution=clean_resolution,
            duration=clean_duration,
        )
        try:
            h3_job_id = self.client.submit(
                clean_prompt, clean_resolution, clean_duration
            )
        except H3Error as exc:
            raise GenerationError(f"提交生成任务失败：{exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise GenerationError(f"提交生成任务失败：{exc}") from exc

        job.h3_job_id = h3_job_id
        job.status = "queued"
        job.stage = "已提交，等待 H3 排队"
        job.updated_at = _now()
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._poll_job,
            args=(job.id,),
            name=f"h3-generation-{job.id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.public()

    # ----------------------------------------------------------------- reads
    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return job.public()

    def video_path(self, job_id: str) -> Path | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "completed" or job.video_path is None:
                return None
            path = job.video_path.resolve()
            try:
                path.relative_to(self.output_dir.resolve())
            except ValueError:
                return None
            return path if path.is_file() else None

    # --------------------------------------------------------------- worker
    def _update(self, job_id: str, **changes: Any) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = _now()
            return True

    def _finish(self, job_id: str, **changes: Any) -> None:
        changes.setdefault("completed_at", _now())
        self._update(job_id, **changes)

    def _fail(self, job_id: str, message: str) -> None:
        self._finish(
            job_id,
            status="failed",
            stage="生成失败",
            error=message[:1000],
        )

    def _poll_job(self, job_id: str) -> None:
        deadline = time.monotonic() + self.max_wait_seconds
        consecutive_errors = 0
        try:
            while True:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is None or job.h3_job_id is None:
                        return
                    h3_job_id = job.h3_job_id
                if time.monotonic() > deadline:
                    self._fail(
                        job_id,
                        f"生成超时：超过 {int(self.max_wait_seconds // 60)} 分钟仍未完成",
                    )
                    return
                time.sleep(self.poll_interval)
                try:
                    status = self.client.status(h3_job_id)
                except H3Error as exc:
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        self._fail(job_id, f"读取 H3 进度失败：{exc}")
                        return
                    continue
                except Exception as exc:  # pragma: no cover - defensive boundary
                    self._fail(job_id, f"读取 H3 进度失败：{exc}")
                    return

                consecutive_errors = 0
                if status.status == "completed":
                    self._update(
                        job_id,
                        progress=100,
                        stage="生成完成，正在下载视频",
                    )
                    break
                if status.status == "failed":
                    self._fail(job_id, status.error or "H3 任务执行失败，未返回原因")
                    return
                if status.status in {"queued", "pending"}:
                    stage = "H3 已接收（排队或生成中，不返回百分比）"
                elif status.status in {"in_progress", "running", "processing"}:
                    stage = "H3 生成中"
                else:
                    stage = f"H3 状态：{status.status}"
                self._update(
                    job_id,
                    stage=stage,
                    progress=status.progress if status.progress is not None else None,
                )

            destination = self.output_dir / f"{job_id}.mp4"
            try:
                size = self.client.download(h3_job_id, destination)
            except H3Error as exc:
                self._fail(job_id, f"视频下载失败：{exc}")
                return
            except Exception as exc:  # pragma: no cover - defensive boundary
                self._fail(job_id, f"视频下载失败：{exc}")
                return
            if size <= 0:
                self._fail(job_id, "视频下载失败：文件为空")
                return
            self._finish(
                job_id,
                status="completed",
                stage="已完成",
                progress=100,
                video_path=destination,
                error=None,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._fail(job_id, f"生成过程出现内部错误：{exc}")
