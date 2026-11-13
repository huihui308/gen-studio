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
import math
import os
import threading
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


# ───────────────────────── device metrics（仪表盘数据源） ─────────────────────────
#: 每个指标的 (偏高, 告警) 阈值，除 temp_c 外单位为 %。
LEVELS: dict[str, tuple[float, float]] = {
    "cpu": (70.0, 90.0),
    "memory": (75.0, 90.0),
    "swap": (80.0, 95.0),
    "gpu_util": (70.0, 90.0),
    "gpu_mem": (80.0, 95.0),
    "temp_c": (75.0, 90.0),
    "disk": (80.0, 95.0),
}


def level(metric: str, value: float | int | None) -> str | None:
    """分级配色：正常 ok / 偏高 warn / 告警 danger；取不到值返回 None。"""

    if value is None:
        return None
    warn_at, danger_at = LEVELS[metric]
    if value >= danger_at:
        return "danger"
    if value >= warn_at:
        return "warn"
    return "ok"


#: 后台 CPU 采样周期（秒）与样本过期阈值（秒）。
CPU_SAMPLE_INTERVAL = 5.0
CPU_SAMPLE_STALE_S = 15.0


def _calc_cpu_percent(prev: tuple[float, int, int], curr: tuple[float, int, int]) -> float | None:
    """由两次 /proc/stat 读数算占用率；窗口无效（回拨/挂起/无增量）返回 None。

    prev/curr 均为 (时刻, 总 jiffies, 空闲 jiffies)；空闲含 iowait。
    """

    prev_at, prev_total, prev_idle = prev
    now, total, idle = curr
    window = now - prev_at
    if window <= 0 or window > 60:
        return None                        # 挂起恢复或时钟回拨：不冒充当前值
    delta_total = total - prev_total
    if delta_total <= 0:
        return None
    delta_idle = idle - prev_idle
    return round(max(0.0, min(100.0, (1.0 - delta_idle / delta_total) * 100.0)), 1)


def _read_proc_stat() -> tuple[int, int]:
    """(总 jiffies, 空闲 jiffies)，空闲含 iowait——与 top 的 idle 口径一致。"""

    with open("/proc/stat", encoding="ascii") as handle:
        parts = handle.readline().split()
    values = [int(item) for item in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            fields = rest.split()
            if fields:
                info[key] = int(fields[0])          # 单位 kB
    return info


#: 参与容量统计的本地磁盘文件系统（/proc/mounts 的 fs type 白名单；
#: 排除 tmpfs/overlay/vfat 等伪文件系统与 EFI 分区）。
DISK_FS_TYPES = frozenset(
    {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "jfs", "reiserfs", "ntfs3", "exfat"}
)


def _read_mounts() -> list[tuple[str, str, str]]:
    """(设备, 挂载点, 文件系统)——只保留真实磁盘文件系统。

    同一设备的 bind 挂载只取 /proc/mounts 里的第一条，避免重复行。
    """

    mounts: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    with open("/proc/mounts", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mount, fstype = parts[0], parts[1], parts[2]
            if fstype not in DISK_FS_TYPES or device in seen:
                continue
            seen.add(device)
            mounts.append((device, mount, fstype))
    return mounts


def _disk_usage(mount: str) -> tuple[int, int, int, float]:
    """(总量字节, 已用字节, 可用字节, 使用率 %)——口径与 df(1) 一致。

    已用 = 总块数 - 空闲块数（含 root 预留）；可用 = 普通用户可见的 bavail；
    使用率 = ceil(已用 / (已用 + 可用) × 100)，与 df(1) 的 Use% 同一公式
    （df 向上取整，本机四个挂载点实测 39/1/33/38 全对齐）。
    """

    st = os.statvfs(mount)
    total = st.f_blocks * st.f_frsize
    free = st.f_bfree * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    used = max(0, total - free)
    percent = float(math.ceil(used / (used + avail) * 100.0)) if (used + avail) > 0 else 0.0
    return total, used, avail, percent


def _gpu_vram_percent(gpu) -> float | None:
    total = gpu.memory_total_mb
    used = gpu.memory_used_mb
    if not total or used is None:
        return None
    return round(max(0.0, min(100.0, used / total * 100.0)), 1)


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
        # CPU 占用率是差值指标：由后台线程按固定节奏独立采样，与页面轮询解耦——
        # 标签页隐藏、页面关闭、控制台部署期间照常出数，页面只读最近样本。
        #（旧实现靠 collect() 推进采样点，断档 >60 秒就留空，被用户报「CPU 不更新」。）
        self._cpu_lock = threading.Lock()
        self._cpu_prev: tuple[float, int, int] | None = None        # (时刻, 总, 空闲)
        self._cpu_sample: tuple[float, float] | None = None         # (采样时刻, 占用率 %)
        try:
            total, idle = _read_proc_stat()
            self._cpu_prev = (time.time(), total, idle)
        except (OSError, ValueError, IndexError):
            self._cpu_prev = None
        threading.Thread(target=self._cpu_sampler, daemon=True, name="cpu-sampler").start()

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
                    "vram_percent": _gpu_vram_percent(gpu),
                    "vram_tone": level("gpu_mem", _gpu_vram_percent(gpu)),
                    "util_tone": level("gpu_util", gpu.utilization_pct),
                    "temp_tone": level("temp_c", gpu.temperature_c),
                }
                for gpu in listing.gpus
            ],
        }
        models = {model_id: self._model(model_id, listing) for model_id in MODEL_ORDER}
        return {
            "generated_at": _iso(started),
            "generated_at_local": datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds"),
            "gpus": gpu_payload,
            "device": {"cpu": self._cpu(), "memory": self._memory(), "disk": self._disk()},
            "models": models,
        }

    # ---------------------------------------------------------------- device
    def _cpu_sampler(self) -> None:
        """后台线程：固定节奏采 /proc/stat，产出 _cpu_sample（页面只读它）。"""

        time.sleep(0.75)                            # 让服务一起来就有第一个差值窗口
        while True:
            try:
                total, idle = _read_proc_stat()
            except (OSError, ValueError, IndexError):
                time.sleep(CPU_SAMPLE_INTERVAL)
                continue
            now = time.time()
            with self._cpu_lock:
                prev, self._cpu_prev = self._cpu_prev, (now, total, idle)
                if prev is None:
                    time.sleep(CPU_SAMPLE_INTERVAL)
                    continue
                percent = _calc_cpu_percent(prev, (now, total, idle))
                if percent is not None:             # 窗口无效只重置，不产出假数
                    self._cpu_sample = (now, percent)
            time.sleep(CPU_SAMPLE_INTERVAL)

    def _cpu(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "available": False, "percent": None, "level": None,
            "cores": os.cpu_count(), "load": [None, None, None],
            "note": None, "source": "/proc/stat",
        }
        try:
            base["load"] = [round(item, 2) for item in os.getloadavg()]
        except OSError:
            pass                                    # 个别环境无 loadavg，保持 None
        with self._cpu_lock:
            sample = self._cpu_sample
        if sample is None:
            base["note"] = "CPU 采样启动中，稍后刷新即可显示"
            return base
        sampled_at, percent = sample
        age = time.time() - sampled_at
        if age > CPU_SAMPLE_STALE_S:
            base["note"] = f"CPU 采样线程滞后 {int(age)} 秒，暂不显示（下次刷新重试）"
            return base
        base.update(available=True, percent=percent, level=level("cpu", percent), note=None)
        return base

    def _memory(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "available": False, "note": None, "source": "/proc/meminfo",
            "total_mb": None, "used_mb": None, "available_mb": None, "percent": None,
            "level": None, "swap_total_mb": None, "swap_used_mb": None,
            "swap_percent": None, "swap_level": None,
        }
        try:
            info = _read_meminfo()
            total = info["MemTotal"]
        except (OSError, KeyError, ValueError) as exc:
            base["note"] = f"读取 /proc/meminfo 失败：{exc}"
            return base
        if total <= 0:
            base["note"] = "/proc/meminfo 的 MemTotal 无效"
            return base
        free = info.get("MemFree", 0)
        buffers = info.get("Buffers", 0)
        cache = info.get("Cached", 0) + info.get("SReclaimable", 0)
        used = max(0, total - free - buffers - cache)          # 与 free(1) 的 used 口径一致
        percent = round(used / total * 100.0, 1)
        swap_total = info.get("SwapTotal", 0)
        swap_used = max(0, swap_total - info.get("SwapFree", 0))
        swap_percent = round(swap_used / swap_total * 100.0, 1) if swap_total > 0 else None
        base.update(
            available=True, total_mb=total // 1024, used_mb=used // 1024,
            available_mb=info.get("MemAvailable", free) // 1024, percent=percent,
            level=level("memory", percent),
            swap_total_mb=swap_total // 1024, swap_used_mb=swap_used // 1024,
            swap_percent=swap_percent, swap_level=level("swap", swap_percent),
        )
        return base

    def _disk(self) -> dict[str, Any]:
        """磁盘容量：/proc/mounts 给挂载清单，statvfs 给每个挂载点的用量。"""

        base: dict[str, Any] = {
            "available": False, "note": None,
            "source": "/proc/mounts + statvfs", "items": [],
        }
        try:
            mounts = _read_mounts()
        except OSError as exc:
            base["note"] = f"读取 /proc/mounts 失败：{exc}"
            return base
        if not mounts:
            base["note"] = "/proc/mounts 里没有可统计的磁盘文件系统"
            return base
        items: list[dict[str, Any]] = []
        for device, mount, fstype in mounts:
            item: dict[str, Any] = {
                "mount": mount, "device": device, "fs": fstype,
                "available": False, "note": None,
                "total_mb": None, "used_mb": None, "avail_mb": None,
                "percent": None, "level": None,
            }
            try:
                total, used, avail, percent = _disk_usage(mount)
            except OSError as exc:
                item["note"] = f"读取 {mount} 容量失败：{exc}"
                items.append(item)
                continue
            if total <= 0:
                item["note"] = f"{mount} 的容量为 0（挂载未就绪或不支持 statvfs）"
                items.append(item)
                continue
            item.update(
                available=True, note=None,
                total_mb=total // (1024 * 1024),
                used_mb=used // (1024 * 1024),
                avail_mb=avail // (1024 * 1024),
                percent=percent, level=level("disk", percent),
            )
            items.append(item)
        base["items"] = items
        if not any(item["available"] for item in items):
            base["note"] = "所有挂载点都读不到容量"
            return base
        base["available"] = True
        return base

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
                note = (
                    f"由独立 systemd 单元 {detection.unit_name} 管理，健康检查通过"
                    if detection.unit_name
                    else "由本系统启动，健康检查通过"
                )
            elif age is not None and age > spec.startup_timeout_seconds:
                code, label, tone = "unhealthy", "进程存活 · 健康检查失败", "danger"
                note = f"启动已超过 {spec.startup_timeout_seconds // 60} 分钟仍未通过健康检查：{health.get('note')}"
            else:
                code, label, tone = "starting", "启动中", "warning"
                note = (
                    f"由独立 systemd 单元 {detection.unit_name} 管理，模型正在加载"
                    if detection.unit_name
                    else "模型正在加载，健康检查尚未通过"
                )
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
            "unit_name": detection.unit_name,
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
                f"端口 {spec.port}（{'监听中' if detection.port_open else '未监听'}）",
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
