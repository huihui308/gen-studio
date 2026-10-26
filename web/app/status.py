"""Collect what can actually be observed about each model.

Honesty rules (requirement 4):
* every value carries ``available`` and, when unavailable, a concrete ``note``
  saying why it cannot be read;
* metrics come from a real source (process table, ``nvidia-smi``, the model's
  own HTTP API) — never from a placeholder;
* fields are grouped per model because the two backends expose different
  capabilities (SGLang video/audio vs. ComfyUI image).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from . import proc
from .catalog import CATALOG, MODEL_ORDER, ModelSpec
from .config import Settings
from .gpu import GpuProbe
from .manager import Detection, ModelManager


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _human_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = int(max(0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def field(label: str, value: str | None = None, *, available: bool = False, source: str | None = None, note: str | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "available": available,
        "source": source,
        "note": note,
    }


def unavailable(label: str, reason: str, source: str | None = None) -> dict[str, Any]:
    return field(label, None, available=False, source=source, note=reason)


class _HttpResult:
    def __init__(self, ok: bool, status_code: int | None, data: Any, latency_ms: int | None, error: str | None) -> None:
        self.ok = ok
        self.status_code = status_code
        self.data = data
        self.latency_ms = latency_ms
        self.error = error


def _http_get_json(url: str, timeout: float) -> _HttpResult:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(64_000)
            latency = int((time.perf_counter() - started) * 1000)
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                data = None
            return _HttpResult(True, response.status, data, latency, None)
    except urllib.error.HTTPError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return _HttpResult(False, exc.code, None, latency, f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return _HttpResult(False, None, None, None, f"连接失败：{exc.reason}")
    except (TimeoutError, OSError) as exc:
        return _HttpResult(False, None, None, None, f"请求失败：{exc}")


class StatusCollector:
    def __init__(self, settings: Settings, gpu_probe: GpuProbe, manager: ModelManager) -> None:
        self.settings = settings
        self.gpu_probe = gpu_probe
        self.manager = manager

    def collect(self) -> dict[str, Any]:
        started = time.time()
        listing = self.gpu_probe.list()
        gpu_payload = {
            "available": listing.available,
            "error": listing.error,
            "items": [
                {
                    "index": gpu.index,
                    "name": gpu.name,
                    "memory_total_mb": gpu.memory_total_mb,
                    "memory_used_mb": gpu.memory_used_mb,
                    "memory_free_mb": gpu.memory_free_mb,
                    "utilization_pct": gpu.utilization_pct,
                    "temperature_c": gpu.temperature_c,
                }
                for gpu in listing.gpus
            ],
        }
        models = {model_id: self._model(model_id, listing) for model_id in MODEL_ORDER}
        return {
            "generated_at": _iso(started),
            "generated_at_local": datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds"),
            "gpus": gpu_payload,
            "models": models,
        }

    # ------------------------------------------------------------------ model
    def _model(self, model_id: str, listing) -> dict[str, Any]:
        spec = CATALOG[model_id]
        detection = self.manager.detect(model_id)
        state = self.manager.state(model_id)
        health = self._probe_health(spec, detection)

        state_info = self._state_info(spec, detection, state, health)
        gpu_info = self._gpu_info(listing, detection)
        runtime_fields = self._runtime_fields(spec, detection, health, gpu_info)
        capability_fields = self._capability_fields(spec, detection, health)

        log_path = detection.log_path
        log_tail = self._read_log(log_path)

        return {
            "id": spec.id,
            "name": spec.name,
            "vendor": spec.vendor,
            "summary": spec.summary,
            "backend": spec.backend,
            "port": spec.port,
            "health_label": spec.health_label,
            "deployment_note": spec.deployment_note,
            "state": state_info,
            "health": health,
            "gpu": gpu_info,
            "capability_fields": capability_fields,
            "runtime_fields": runtime_fields,
            "log_path": log_path,
            "log_tail": log_tail,
            "log_available": bool(log_tail),
        }

    # ----------------------------------------------------------------- health
    def _probe_health(self, spec: ModelSpec, detection: Detection) -> dict[str, Any]:
        running = detection.managed or detection.unmanaged or detection.port_open
        base = {
            "url": spec.health_url,
            "label": spec.health_label,
            "ok": None,
            "latency_ms": None,
            "note": None,
            "checked_at": _iso(time.time()),
        }
        if not running:
            base["note"] = "服务未运行，未发起健康检查"
            return base
        result = _http_get_json(spec.health_url, self.settings.health_timeout_seconds)
        base["latency_ms"] = result.latency_ms
        if result.ok:
            base["ok"] = True
            if spec.id == "minimax-h3" and isinstance(result.data, dict):
                status_value = result.data.get("status")
                base["note"] = f"响应 status={status_value}" if status_value else "HTTP 200"
            elif spec.id == "qwen-image2.0" and isinstance(result.data, dict):
                version = (result.data.get("system") or {}).get("comfyui_version")
                base["note"] = f"ComfyUI {version}" if version else "HTTP 200"
            else:
                base["note"] = "HTTP 200"
        else:
            base["ok"] = False
            base["note"] = result.error or "健康检查失败"
        return base

    # ------------------------------------------------------------------ state
    def _state_info(self, spec: ModelSpec, detection: Detection, state, health: dict[str, Any]) -> dict[str, Any]:
        age = (time.time() - detection.start_epoch) if detection.start_epoch else None
        if detection.managed:
            if health.get("ok"):
                code, label, tone = "running", "运行中", "success"
                note = "由本系统启动，健康检查通过"
            elif age is not None and age > spec.startup_timeout_seconds:
                code, label, tone = "unhealthy", "进程存活 · 健康检查失败", "danger"
                note = f"启动已超过 {spec.startup_timeout_seconds // 60} 分钟仍未通过健康检查：{health.get('note')}"
            else:
                code, label, tone = "starting", "启动中", "warning"
                note = "模型正在加载，健康检查尚未通过"
        elif detection.unmanaged:
            if detection.marker_match and not detection.port_open:
                code, label, tone = "starting", "启动中（外部进程）", "warning"
                note = "该进程由控制台之外启动，端口尚未监听（模型加载中）；停止时会先校验命令行"
            elif detection.marker_match:
                code, label, tone = "external", "运行中（外部进程）", "warning"
                note = "该进程由控制台之外启动；停止时会先校验命令行是否属于本模型"
            else:
                code, label, tone = "conflict", "端口冲突", "danger"
                note = f"端口 {spec.port} 被其它进程占用：{detection.cmdline or '无法读取命令行'}"
        else:
            if state.last_status == "failed" and state.last_error:
                code, label, tone = "failed", "已停止 · 上次启动失败", "danger"
                note = state.last_error
            else:
                code, label, tone = "stopped", "已停止", "neutral"
                note = None

        uptime = None
        if detection.pid and detection.start_epoch:
            uptime = max(0.0, time.time() - detection.start_epoch)

        return {
            "code": code,
            "label": label,
            "tone": tone,
            "managed": detection.managed,
            "pid": detection.pid,
            "gpu_index": detection.gpu_index,
            "gpu_source": detection.gpu_source,
            "started_at": _iso(detection.start_epoch),
            "uptime_seconds": int(uptime) if uptime is not None else None,
            "uptime_label": _human_duration(uptime),
            "note": note,
            "port": spec.port,
            "port_open": detection.port_open,
        }

    # -------------------------------------------------------------------- gpu
    def _gpu_info(self, listing, detection: Detection) -> dict[str, Any]:
        info = {
            "available": False,
            "index": detection.gpu_index,
            "name": None,
            "memory_total_mb": None,
            "memory_used_mb": None,
            "memory_free_mb": None,
            "utilization_pct": None,
            "temperature_c": None,
            "note": None,
            "source": detection.gpu_source,
        }
        if detection.gpu_index is None:
            info["note"] = "未记录到该模型的显卡分配（未运行，或无法从进程环境读取 CUDA_VISIBLE_DEVICES）"
            return info
        if not listing.available:
            info["note"] = f"无法读取显卡实时状态：{listing.error}"
            return info
        gpu = next((item for item in listing.gpus if item.index == detection.gpu_index), None)
        if gpu is None:
            info["note"] = f"显卡 {detection.gpu_index} 当前不在 nvidia-smi 列表中"
            return info
        info.update(
            available=True,
            name=gpu.name,
            memory_total_mb=gpu.memory_total_mb,
            memory_used_mb=gpu.memory_used_mb,
            memory_free_mb=gpu.memory_free_mb,
            utilization_pct=gpu.utilization_pct,
            temperature_c=gpu.temperature_c,
            note=None,
        )
        return info

    # --------------------------------------------------------------- fields
    def _runtime_fields(self, spec: ModelSpec, detection: Detection, health: dict[str, Any], gpu: dict[str, Any]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []

        if health.get("ok"):
            fields.append(
                field("服务健康", f"正常（{health['latency_ms']} ms）", available=True, source=spec.health_label)
            )
        elif health.get("ok") is False:
            starting = (
                (detection.managed or detection.unmanaged)
                and detection.start_epoch is not None
                and (time.time() - detection.start_epoch) < spec.startup_timeout_seconds
            )
            if starting:
                # A just-spawned model legitimately refuses connections while it
                # loads; that is "not ready yet", not a health failure.
                fields.append(
                    field(
                        "服务健康",
                        "未就绪（加载中）",
                        available=True,
                        source=spec.health_label,
                        note=f"健康检查尚未通过：{health.get('note')}",
                    )
                )
            else:
                fields.append(
                    field("服务健康", "异常", available=True, source=spec.health_label, note=health.get("note"))
                )
        else:
            fields.append(unavailable("服务健康", health.get("note") or "服务未运行", spec.health_label))

        if detection.pid:
            owner = "本系统启动" if detection.managed else "外部进程"
            fields.append(field("运行进程", f"PID {detection.pid}（{owner}）", available=True, source="/proc/<pid>"))
            duration = _human_duration(time.time() - detection.start_epoch) if detection.start_epoch else None
            fields.append(
                field("运行时长", duration, available=duration is not None,
                      source="/proc/<pid>/stat + /proc/uptime")
            )
        else:
            fields.append(unavailable("运行进程", "当前没有正在运行的进程", "/proc"))
            fields.append(unavailable("运行时长", "当前没有正在运行的进程"))

        fields.append(
            field(
                "服务端口",
                f"127.0.0.1:{spec.port}（{'监听中' if detection.port_open else '未监听'}）",
                available=True,
                source="socket / ss",
            )
        )

        if gpu["available"]:
            fields.append(
                field(
                    "显卡占用",
                    f"GPU {gpu['index']} · {gpu['name']}",
                    available=True,
                    source="nvidia-smi",
                )
            )
            total = gpu["memory_total_mb"]
            used = gpu["memory_used_mb"]
            free = gpu["memory_free_mb"]
            fields.append(
                field(
                    "显存占用",
                    f"{used} / {total} MiB（剩余 {free} MiB）" if None not in (used, total, free) else None,
                    available=None not in (used, total),
                    source="nvidia-smi",
                    note=None if None not in (used, total) else "nvidia-smi 未返回该字段",
                )
            )
            fields.append(
                field(
                    "GPU 利用率",
                    f"{gpu['utilization_pct']} %" if gpu["utilization_pct"] is not None else None,
                    available=gpu["utilization_pct"] is not None,
                    source="nvidia-smi",
                    note=None if gpu["utilization_pct"] is not None else "该显卡未上报利用率",
                )
            )
            fields.append(
                field(
                    "GPU 温度",
                    f"{gpu['temperature_c']} °C" if gpu["temperature_c"] is not None else None,
                    available=gpu["temperature_c"] is not None,
                    source="nvidia-smi",
                    note=None if gpu["temperature_c"] is not None else "该显卡未上报温度",
                )
            )
        else:
            reason = gpu.get("note") or "无法读取显卡信息"
            fields.append(unavailable("显卡占用", reason, "nvidia-smi"))
            fields.append(unavailable("显存占用", reason, "nvidia-smi"))
            fields.append(unavailable("GPU 利用率", reason, "nvidia-smi"))
            fields.append(unavailable("GPU 温度", reason, "nvidia-smi"))

        if spec.id == "minimax-h3":
            fields.append(
                unavailable(
                    "队列 / 当前任务",
                    "SGLang 0.5.22 未暴露队列或任务查询接口，无法获取该指标",
                    "SGLang API",
                )
            )
            fields.append(
                unavailable(
                    "生成耗时统计",
                    "后端未暴露生成耗时指标；本系统不发起生成请求",
                    "SGLang API",
                )
            )
        else:
            queue = self._qwen_queue(spec, detection)
            if queue is None:
                fields.append(unavailable("队列运行 / 等待", "无法读取 ComfyUI 队列（或服务未运行）", "GET /queue"))
            else:
                running, pending, current_prompt = queue
                fields.append(
                    field("队列运行 / 等待", f"{running} / {pending}", available=True, source="GET /queue")
                )
                if current_prompt:
                    fields.append(
                        field("当前任务", f"prompt_id {current_prompt}", available=True, source="GET /queue")
                    )
                else:
                    fields.append(
                        field("当前任务", "空闲（队列中无运行任务）", available=True, source="GET /queue")
                    )
        return fields

    def _capability_fields(self, spec: ModelSpec, detection: Detection, health: dict[str, Any]) -> list[dict[str, Any]]:
        fields = [field(item.label, item.value, available=True, source="模型能力（部署配置）") for item in spec.capabilities]
        if not health.get("ok"):
            reason = "服务未运行，运行期能力信息不可用" if health.get("ok") is None else health.get("note") or "健康检查失败"
            api = "SGLang API" if spec.id == "minimax-h3" else "ComfyUI API"
            fields.append(unavailable("后端运行期信息", reason, api))
            return fields

        if spec.id == "minimax-h3":
            info = _http_get_json(f"http://127.0.0.1:{spec.port}/server_info", self.settings.health_timeout_seconds)
            models = _http_get_json(f"http://127.0.0.1:{spec.port}/v1/models", self.settings.health_timeout_seconds)
            if info.ok and isinstance(info.data, dict):
                fields.append(
                    field("已加载模型", str(info.data.get("served_model_name") or info.data.get("model_path") or ""),
                          available=True, source="GET /server_info")
                )
                fields.append(field("SGLang 版本", str(info.data.get("version")), available=bool(info.data.get("version")), source="GET /server_info"))
                tp = info.data.get("tp_size")
                dp = info.data.get("dp_size")
                if tp is not None or dp is not None:
                    fields.append(field("并行度", f"TP={tp} · DP={dp}", available=True, source="GET /server_info"))
            else:
                fields.append(unavailable("已加载模型", f"读取失败：{info.error or '响应异常'}", "GET /server_info"))
            if models.ok and isinstance(models.data, dict) and models.data.get("data"):
                entry = models.data["data"][0]
                task_type = entry.get("task_type")
                supported = entry.get("supported_task_types")
                fields.append(
                    field(
                        "运行期任务类型",
                        f"{task_type}" + (f"（支持 {', '.join(supported)}）" if isinstance(supported, list) else ""),
                        available=bool(task_type),
                        source="GET /v1/models",
                    )
                )
                fields.append(field("推理管线", str(entry.get("pipeline_name")), available=bool(entry.get("pipeline_name")), source="GET /v1/models"))
                precision = f"DiT {entry.get('dit_precision')} · VAE {entry.get('vae_precision')}"
                fields.append(field("推理精度", precision, available=bool(entry.get("dit_precision") or entry.get("vae_precision")), source="GET /v1/models"))
            else:
                fields.append(unavailable("运行期任务类型", f"读取失败：{models.error or '响应异常'}", "GET /v1/models"))
        else:
            stats = _http_get_json(f"http://127.0.0.1:{spec.port}/system_stats", self.settings.health_timeout_seconds)
            if stats.ok and isinstance(stats.data, dict):
                system = stats.data.get("system") or {}
                fields.append(field("ComfyUI 版本", str(system.get("comfyui_version")), available=bool(system.get("comfyui_version")), source="GET /system_stats"))
                fields.append(field("PyTorch 版本", str(system.get("pytorch_version")), available=bool(system.get("pytorch_version")), source="GET /system_stats"))
                devices = stats.data.get("devices") or []
                if devices:
                    device = devices[0]
                    total = device.get("vram_total")
                    free = device.get("vram_free")
                    fields.append(
                        field(
                            "后端可见显存",
                            f"总 {round(total / 1024 / 1024)} MiB · 空闲 {round(free / 1024 / 1024)} MiB" if total and free else None,
                            available=bool(total and free),
                            source="GET /system_stats",
                        )
                    )
                else:
                    fields.append(unavailable("后端可见显存", "响应中没有 devices 字段", "GET /system_stats"))
            else:
                fields.append(unavailable("后端运行期信息", f"读取失败：{stats.error or '响应异常'}", "GET /system_stats"))
        return fields

    def _qwen_queue(self, spec: ModelSpec, detection: Detection) -> tuple[int, int, str | None] | None:
        if not (detection.managed or detection.unmanaged or detection.port_open):
            return None
        result = _http_get_json(f"http://127.0.0.1:{spec.port}/queue", self.settings.health_timeout_seconds)
        if not result.ok or not isinstance(result.data, dict):
            return None
        running = result.data.get("queue_running") or []
        pending = result.data.get("queue_pending") or []
        current_prompt = None
        if running and isinstance(running[0], list) and len(running[0]) > 1:
            current_prompt = str(running[0][1])
        return len(running), len(pending), current_prompt

    def _read_log(self, path: str | None) -> list[str]:
        if not path:
            return []
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 16_384))
                chunk = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = [line.rstrip() for line in chunk.splitlines() if line.strip()]
        return lines[-10:]
