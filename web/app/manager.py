"""Start / stop the two generation models.

In production each model is launched in its own transient systemd user unit so
that restarting the web console does not terminate a running model.  The
manager writes the per-start environment (GPU and listen host), starts the
unit, and records the unit name plus the observed PID in
``web/data/state.json``.  Tests and non-systemd environments can still use the
legacy in-process ``Popen`` launcher when units are disabled.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import proc
from .catalog import CATALOG, ModelSpec, get_spec
from .config import Settings
from .gpu import GpuProbe
from .modelaccess import MODEL_ACCESS_HOSTS
from .units import SystemdUserUnits, UnitError, UnitStatus


class ControlError(Exception):
    """User-facing control failure (start or stop refused)."""


class StartError(ControlError):
    pass


class StopError(ControlError):
    pass


@dataclass
class ModelState:
    """Persisted ownership record for one model."""

    model_id: str
    pid: int | None = None
    pgid: int | None = None
    gpu_index: int | None = None
    started_at: float | None = None
    start_ticks: int | None = None
    log_path: str | None = None
    unit_name: str | None = None
    last_status: str = "stopped"  # stopped | starting | failed
    last_error: str | None = None
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Detection:
    """Live observation of what is actually running for a model."""

    model_id: str
    managed: bool
    pid: int | None
    pgid: int | None
    cmdline: str | None
    start_epoch: float | None
    gpu_index: int | None
    gpu_source: str | None
    listener_pid: int | None
    unmanaged: bool
    marker_match: bool
    port_open: bool
    log_path: str | None
    note: str | None
    unit_name: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ModelManager:
    def __init__(
        self,
        settings: Settings,
        gpu_probe: GpuProbe,
        catalog: dict[str, ModelSpec] | None = None,
        process_finder=None,
        unit_controller: SystemdUserUnits | None = None,
    ) -> None:
        self.settings = settings
        self.gpu_probe = gpu_probe
        self.catalog = catalog or CATALOG
        self._process_finder = process_finder or proc.find_processes_by_markers
        self._units = unit_controller
        if self._units is None and settings.model_units_enabled:
            self._units = SystemdUserUnits()
        self._use_units = bool(self._units is not None and self._units.available())
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._states: dict[str, ModelState] = {}
        self.settings.ensure_dirs()
        self._load()
        self._reconcile()

    # ------------------------------------------------------------------ state
    def _load(self) -> None:
        path = self.settings.state_file
        raw: dict[str, Any] | None = None
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                corrupted = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
                try:
                    path.replace(corrupted)
                except OSError:
                    pass
                raw = None
        models_raw = (raw or {}).get("models") if isinstance(raw, dict) else None
        for model_id in self.catalog:
            entry = (models_raw or {}).get(model_id) if isinstance(models_raw, dict) else None
            if isinstance(entry, dict):
                state = ModelState(model_id=model_id)
                for key in ("pid", "pgid", "gpu_index", "started_at", "start_ticks", "log_path", "unit_name", "last_status", "last_error", "updated_at"):
                    if key in entry:
                        setattr(state, key, entry[key])
                self._states[model_id] = state
            else:
                self._states[model_id] = ModelState(model_id=model_id)

    def _persist(self) -> None:
        payload = {
            "version": 1,
            "models": {model_id: asdict(state) for model_id, state in self._states.items()},
            "updated_at": _now_iso(),
        }
        path = self.settings.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def state(self, model_id: str) -> ModelState:
        with self._lock:
            return self._states[model_id]

    def _unit_name(self, model_id: str) -> str:
        assert self._units is not None
        return self._units.unit_name(model_id)

    def _unit_status(self, model_id: str) -> UnitStatus | None:
        if not self._use_units or self._units is None:
            return None
        try:
            return self._units.status(self._unit_name(model_id))
        except UnitError:
            return None

    def uses_systemd_units(self) -> bool:
        return self._use_units

    def _reconcile(self) -> None:
        """Bring persisted ownership in line with the units/processes alive."""
        with self._lock:
            changed = False
            for model_id, state in self._states.items():
                unit_status = self._unit_status(model_id)
                if unit_status is not None and unit_status.active:
                    state.unit_name = unit_status.name
                    pid = unit_status.main_pid
                    if pid is not None:
                        info = proc.proc_info(pid)
                        state.pid = pid
                        state.pgid = info.pgid if info else pid
                        state.start_ticks = info.start_ticks if info else state.start_ticks
                    if state.last_status not in {"starting", "failed"}:
                        state.last_status = "starting"
                    changed = True
                    continue

                if state.unit_name:
                    # The console now owns this model through a unit, but the
                    # unit is no longer active.  Do not fall back to a stale
                    # PID from the unit's previous run.
                    if state.last_status == "starting":
                        state.last_status = "failed"
                        state.last_error = "systemd 单元已停止（控制台重启后发现模型已退出）"
                    state.pid = None
                    state.pgid = None
                    state.unit_name = None
                    changed = True
                    continue

                if state.pid is None:
                    continue
                info = proc.proc_info(state.pid)
                alive = info is not None and info.state not in {"Z", "X"}
                identity_ok = self._identity_ok(model_id, state, info)
                if alive and identity_ok:
                    continue
                if alive and not identity_ok:
                    state.last_error = "记录的 PID 已被其它进程复用，已忽略该记录"
                elif state.last_status in {"starting", "failed"}:
                    if state.last_status == "starting":
                        state.last_status = "failed"
                        state.last_error = "控制台重启时发现模型进程已退出（无法取得退出码）"
                state.pid = None
                state.pgid = None
                changed = True
            if changed:
                self._persist()

    # --------------------------------------------------------------- detection
    def _identity_ok(self, model_id: str, state: ModelState, info: proc.ProcInfo | None) -> bool:
        """Is ``info`` still the process this console started for ``model_id``?

        PID plus the kernel start tick protect against PID reuse.  Command-line
        markers identify the real server, but entry scripts ``exec`` into it,
        so during a short window after launch the markers may not be visible
        yet; in that window our own fresh record is trusted.
        """
        if info is None or info.state in {"Z", "X"}:
            return False
        if state.start_ticks is not None and info.start_ticks != state.start_ticks:
            return False
        if proc.marker_match(info.cmdline, self.catalog[model_id].process_markers):
            return True
        if state.last_status == "starting" and state.started_at:
            return (time.time() - state.started_at) < 120.0
        return False

    def _managed_identity(self, model_id: str, state: ModelState) -> bool:
        if state.pid is None:
            return False
        return self._identity_ok(model_id, state, proc.proc_info(state.pid))

    def _mark_failed_if_needed(self, model_id: str, state: ModelState) -> None:
        """Persist a failure note once when an owned process/unit died."""
        unit_status = self._unit_status(model_id)
        if unit_status is not None and unit_status.active:
            return
        if state.unit_name and unit_status is not None:
            if state.last_status == "starting":
                state.last_status = "failed"
                state.last_error = "systemd 单元已停止（模型进程退出）"
                self._persist()
            return
        if state.pid is None:
            return
        if proc.is_alive(state.pid) and self._managed_identity(model_id, state):
            return
        if state.last_status == "starting":
            exit_code = None
            handle = self._procs.get(model_id)
            if handle is not None and handle.poll() is not None:
                exit_code = handle.returncode
            state.last_status = "failed"
            state.last_error = (
                f"进程已退出（退出码 {exit_code}）" if exit_code is not None else "进程已退出"
            )
            self._persist()

    def detect(self, model_id: str) -> Detection:
        spec = self.catalog[model_id]
        state = self.state(model_id)
        with self._lock:
            listener = proc.listener_pid(spec.port)
            port_is_open = proc.port_open(spec.port)
            unit_status = self._unit_status(model_id)
            if unit_status is not None and unit_status.active:
                pid = unit_status.main_pid or listener
                info = proc.proc_info(pid) if pid else None
                gpu_env = proc.proc_cuda_visible_devices(pid) if pid else None
                gpu_index = (
                    int(gpu_env)
                    if gpu_env and gpu_env.isdigit()
                    else state.gpu_index
                )
                return Detection(
                    model_id=model_id,
                    managed=True,
                    pid=pid,
                    pgid=info.pgid if info else (pid or None),
                    cmdline=info.cmdline if info else None,
                    start_epoch=state.started_at or (proc.proc_start_epoch(pid) if pid else None),
                    gpu_index=gpu_index,
                    gpu_source=(
                        f"进程环境 CUDA_VISIBLE_DEVICES={gpu_env}"
                        if gpu_env
                        else ("systemd 单元启动记录" if gpu_index is not None else None)
                    ),
                    listener_pid=listener,
                    unmanaged=False,
                    marker_match=False,
                    port_open=port_is_open,
                    log_path=state.log_path or str(self.settings.model_log_path(model_id)),
                    note=f"由独立 systemd 单元 {unit_status.name} 管理；重启控制台不会停止模型",
                    unit_name=unit_status.name,
                )

            self._mark_failed_if_needed(model_id, state)
            managed = self._managed_identity(model_id, state)
            managed_pid = state.pid if managed else None
            managed_info = proc.proc_info(managed_pid) if managed_pid else None

            unmanaged_pid: int | None = None
            unmanaged_info: proc.ProcInfo | None = None
            if not managed:
                if listener is not None:
                    unmanaged_pid = listener
                    unmanaged_info = proc.proc_info(listener)
                else:
                    # An externally started model may still be loading weights
                    # and has no listening port yet; find it by its command
                    # line so the UI says "starting" instead of "stopped".
                    candidates = self._process_finder(spec.process_markers)
                    if candidates:
                        unmanaged_info = candidates[0]
                        unmanaged_pid = unmanaged_info.pid
            unmanaged_match = bool(
                unmanaged_info and proc.marker_match(unmanaged_info.cmdline, spec.process_markers)
            )

            if managed:
                pid = managed_pid
                pgid = state.pgid or (managed_info.pgid if managed_info else None)
                cmdline = managed_info.cmdline if managed_info else None
                gpu_index = state.gpu_index
                gpu_source = "本系统启动记录" if gpu_index is not None else None
                start_epoch = state.started_at or proc.proc_start_epoch(pid) if pid else None
                log_path = state.log_path
                note = None
            elif unmanaged_pid is not None:
                pid = unmanaged_pid
                pgid = unmanaged_info.pgid if unmanaged_info else None
                cmdline = unmanaged_info.cmdline if unmanaged_info else None
                gpu_env = proc.proc_cuda_visible_devices(pid) if pid else None
                gpu_index = int(gpu_env) if gpu_env and gpu_env.isdigit() else None
                gpu_source = f"进程环境 CUDA_VISIBLE_DEVICES={gpu_env}" if gpu_env else None
                start_epoch = proc.proc_start_epoch(pid)
                # Use the process' real stdout target (e.g. h3/logs/serve.log)
                # instead of guessing a path.
                log_path = proc.proc_stdout_path(pid) or (state.log_path if state.last_status in {"starting", "failed"} else None)
                note = "该进程由控制台之外的命令启动（未被本系统接管）"
            else:
                pid = None
                pgid = None
                cmdline = None
                gpu_index = None
                gpu_source = None
                start_epoch = None
                log_path = None
                note = state.last_error if state.last_status == "failed" else None

            return Detection(
                model_id=model_id,
                managed=managed,
                pid=pid,
                pgid=pgid,
                cmdline=cmdline,
                start_epoch=start_epoch,
                gpu_index=gpu_index,
                gpu_source=gpu_source,
                listener_pid=listener,
                unmanaged=bool(unmanaged_pid is not None),
                marker_match=unmanaged_match,
                port_open=port_is_open,
                log_path=log_path,
                note=note,
            )

    # -------------------------------------------------------------------- logs
    def log_tail(self, model_id: str, lines: int = 12) -> list[str]:
        state = self.state(model_id)
        path = state.log_path or str(self.settings.model_log_path(model_id))
        if not path or not Path(path).exists():
            return []
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 32_768))
                chunk = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        cleaned = [line.rstrip() for line in chunk.splitlines() if line.strip()]
        return cleaned[-lines:]

    # ------------------------------------------------------------------- start
    def start(
        self, model_id: str, gpu_index: int | None, access_host: str | None = None
    ) -> ModelState:
        spec = get_spec(model_id)
        if spec is None:
            raise StartError(f"未知模型：{model_id}")
        if access_host is not None and access_host not in MODEL_ACCESS_HOSTS:
            raise StartError("模型访问地址只能是 127.0.0.1 或 0.0.0.0")

        with self._lock:
            state = self._states[model_id]
            detection = self.detect(model_id)
            if detection.managed:
                raise StartError(f"{spec.name} 已在运行（PID {detection.pid}），请先停止")
            if detection.port_open:
                owner = f"PID {detection.listener_pid}" if detection.listener_pid else "未知进程"
                raise StartError(
                    f"端口 {spec.port} 已被占用（{owner}），无法启动 {spec.name}；请先停止占用该端口的进程"
                )
            if detection.unmanaged:
                raise StartError(
                    f"{spec.name} 已有控制台之外启动的进程（PID {detection.pid}）正在加载，"
                    "无法重复启动；请先停止该进程，或等待其完成加载"
                )
            if state.last_status == "failed":
                # A fresh start is allowed; clear the previous failure note.
                state.last_error = None

            listing = self.gpu_probe.list()
            if not listing.available:
                raise StartError(
                    f"无法获取显卡信息：{listing.error or '未知原因'}。已阻止启动，不会使用默认显卡。"
                )
            if gpu_index is None:
                raise StartError("必须选择一张显卡后才能启动（显卡为必选项）")
            if not isinstance(gpu_index, int):
                raise StartError("显卡参数无效（必须为整数索引）")
            gpu = next((item for item in listing.gpus if item.index == gpu_index), None)
            if gpu is None:
                available = ", ".join(str(item.index) for item in listing.gpus)
                raise StartError(f"所选显卡 {gpu_index} 不存在；当前可用显卡：{available}")

            script = spec.script_path(self.settings)
            if not script.exists():
                raise StartError(f"启动脚本不存在：{script}")

            log_path = self.settings.model_log_path(model_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            base_env = os.environ.copy()
            base_env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
            base_env["PYTHONUNBUFFERED"] = "1"
            base_env.setdefault("TOKENIZERS_PARALLELISM", "false")
            if access_host is not None:
                # The entry script passes this to SGLang / ComfyUI; it defaults
                # to loopback when a model is started outside the console.
                base_env["GEN_STUDIO_MODEL_HOST"] = access_host
            cwd = self.settings.repo_root / spec.working_dir_rel
            if not cwd.is_dir():
                raise StartError(f"模型工作目录不存在：{cwd}")

            bind_label = access_host or "脚本默认（127.0.0.1）"
            header = (
                f"\n===== [{_now_iso()}] start {model_id} on GPU {gpu_index} "
                f"({gpu.name}), bind {bind_label} via web console =====\n"
            )
            with open(log_path, "ab") as log_handle:
                log_handle.write(header.encode("utf-8"))
                log_handle.flush()

            if self._use_units and self._units is not None:
                unit = self._unit_name(model_id)
                # Pass only the environment the model needs; inheriting the
                # console's whole environment into a transient unit would also
                # leak unrelated variables.
                unit_env = {
                    "CUDA_VISIBLE_DEVICES": str(gpu_index),
                    "PYTHONUNBUFFERED": "1",
                    "TOKENIZERS_PARALLELISM": base_env.get("TOKENIZERS_PARALLELISM", "false"),
                }
                if access_host is not None:
                    unit_env["GEN_STUDIO_MODEL_HOST"] = access_host
                try:
                    self._units.start(unit, script, cwd, unit_env, log_path)
                except UnitError as exc:
                    state.last_status = "failed"
                    state.last_error = f"无法启动 systemd 单元：{exc}"
                    self._persist()
                    raise StartError(f"无法启动 {spec.name}：{exc}") from exc

                deadline = time.monotonic() + 3.0
                unit_status = None
                while time.monotonic() < deadline:
                    unit_status = self._unit_status(model_id)
                    if unit_status is not None and unit_status.active and unit_status.main_pid:
                        break
                    time.sleep(0.1)
                if (
                    unit_status is None
                    or not unit_status.active
                    or unit_status.main_pid is None
                ):
                    tail = self.log_tail(model_id, lines=6)
                    detail = " | ".join(tail[-3:]) if tail else "日志为空"
                    state.pid = None
                    state.pgid = None
                    state.unit_name = unit
                    state.gpu_index = None
                    state.last_status = "failed"
                    state.last_error = f"systemd 单元启动失败（未进入 active 状态）：{detail}"
                    self._persist()
                    raise StartError(f"{spec.name} 启动失败：{state.last_error}")

                pid = unit_status.main_pid
                info = proc.proc_info(pid)
                state.pid = pid
                state.pgid = info.pgid if info else pid
                state.unit_name = unit
                state.gpu_index = gpu_index
                state.started_at = time.time()
                state.start_ticks = info.start_ticks if info else None
                state.log_path = str(log_path)
                state.last_status = "starting"
                state.last_error = None
                state.updated_at = time.time()
                self._procs.pop(model_id, None)
                self._persist()
                return state

            try:
                with open(log_path, "ab") as log_handle:
                    process = subprocess.Popen(
                        ["bash", str(script)],
                        cwd=str(cwd),
                        env=base_env,
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
            except OSError as exc:
                state.last_status = "failed"
                state.last_error = f"无法启动进程：{exc}"
                self._persist()
                raise StartError(f"无法启动 {spec.name}：{exc}") from exc

            # Catch scripts that fail immediately (missing env, bad flags, ...).
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.1)
            if process.poll() is not None:
                tail = self.log_tail(model_id, lines=6)
                detail = " | ".join(tail[-3:]) if tail else "日志为空"
                state.pid = None
                state.pgid = None
                state.unit_name = None
                state.gpu_index = None
                state.last_status = "failed"
                state.last_error = f"启动进程立即退出（退出码 {process.returncode}）：{detail}"
                self._persist()
                raise StartError(f"{spec.name} 启动失败：{state.last_error}")

            info = proc.proc_info(process.pid)
            state.pid = process.pid
            state.pgid = info.pgid if info else process.pid
            state.unit_name = None
            state.gpu_index = gpu_index
            state.started_at = time.time()
            state.start_ticks = info.start_ticks if info else None
            state.log_path = str(log_path)
            state.last_status = "starting"
            state.last_error = None
            state.updated_at = time.time()
            self._procs[model_id] = process
            self._persist()
            return state

    # -------------------------------------------------------------------- stop
    def stop(self, model_id: str) -> str:
        spec = get_spec(model_id)
        if spec is None:
            raise StopError(f"未知模型：{model_id}")

        with self._lock:
            state = self._states[model_id]
            detection = self.detect(model_id)
            unit_status = self._unit_status(model_id)

            if unit_status is not None and unit_status.active and self._units is not None:
                unit = unit_status.name
                try:
                    stopped = self._units.stop(unit)
                except UnitError as exc:
                    raise StopError(f"停止 systemd 单元 {unit} 失败：{exc}") from exc
                if not stopped:
                    raise StopError(f"systemd 单元 {unit} 在超时后仍未停止")

                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    if not proc.port_open(spec.port):
                        break
                    time.sleep(0.2)
                port_free = not proc.port_open(spec.port)

                state.last_status = "stopped"
                state.last_error = None
                state.pid = None
                state.pgid = None
                state.unit_name = None
                state.gpu_index = None
                state.updated_at = time.time()
                self._procs.pop(model_id, None)
                self._persist()

                if not port_free:
                    raise StopError(
                        f"{spec.name} 的 systemd 单元已停止，但端口 {spec.port} 仍被占用"
                    )
                return f"已停止 {spec.name}（systemd 单元 {unit}）"

            if detection.managed and detection.pid:
                pid = detection.pid
                pgid = detection.pgid
                source = "本系统启动"
            elif detection.unmanaged and detection.pid:
                if not detection.marker_match:
                    raise StopError(
                        f"端口 {spec.port} 被非 {spec.name} 进程占用（PID {detection.pid}），拒绝停止"
                    )
                pid = detection.pid
                pgid = detection.pgid
                source = "控制台外的进程（命令与模型匹配，已确认后停止）"
            else:
                # Nothing to stop: normalise the record.
                state.last_status = "stopped"
                state.last_error = None
                state.pid = None
                state.pgid = None
                state.unit_name = None
                state.gpu_index = None
                state.updated_at = time.time()
                self._persist()
                return f"{spec.name} 当前未运行，无需停止"

            gone = proc.terminate(pid, pgid, grace_seconds=20.0, kill_wait_seconds=5.0)
            if not gone:
                raise StopError(f"已发送 SIGKILL，但进程 PID {pid} 仍未退出")

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if not proc.port_open(spec.port):
                    break
                time.sleep(0.2)
            port_free = not proc.port_open(spec.port)

            state.last_status = "stopped"
            state.last_error = None
            state.pid = None
            state.pgid = None
            state.unit_name = None
            state.gpu_index = None
            state.updated_at = time.time()
            self._procs.pop(model_id, None)
            self._persist()

            if not port_free:
                raise StopError(
                    f"{spec.name} 的进程已退出，但端口 {spec.port} 仍被占用（可能有其它监听进程）"
                )
            return f"已停止 {spec.name}（{source}，PID {pid}）"
