"""Start / stop the two generation models as real OS processes.

Each model is launched through its repository entry script with an explicit
``CUDA_VISIBLE_DEVICES`` value chosen by the operator.  The manager records
the PID, process group, start tick, GPU index and log path in
``web/data/state.json`` so ownership survives console restarts and PID reuse
is detected rather than killing an unrelated process.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ModelManager:
    def __init__(
        self,
        settings: Settings,
        gpu_probe: GpuProbe,
        catalog: dict[str, ModelSpec] | None = None,
        process_finder=None,
    ) -> None:
        self.settings = settings
        self.gpu_probe = gpu_probe
        self.catalog = catalog or CATALOG
        self._process_finder = process_finder or proc.find_processes_by_markers
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
                for key in ("pid", "pgid", "gpu_index", "started_at", "start_ticks", "log_path", "last_status", "last_error", "updated_at"):
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

    def _reconcile(self) -> None:
        """Bring persisted ownership in line with the processes actually alive."""
        with self._lock:
            changed = False
            for model_id, state in self._states.items():
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
        """Persist a failure note once when an owned process died."""
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
            self._mark_failed_if_needed(model_id, state)
            managed = self._managed_identity(model_id, state)
            listener = proc.listener_pid(spec.port)
            port_is_open = proc.port_open(spec.port)
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
            env = os.environ.copy()
            env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
            env["PYTHONUNBUFFERED"] = "1"
            env.setdefault("TOKENIZERS_PARALLELISM", "false")
            if access_host is not None:
                # The entry script passes this to SGLang / ComfyUI; it defaults
                # to loopback when a model is started outside the console.
                env["GEN_STUDIO_MODEL_HOST"] = access_host
            cwd = self.settings.repo_root / spec.working_dir_rel
            if not cwd.is_dir():
                raise StartError(f"模型工作目录不存在：{cwd}")

            with open(log_path, "ab") as log_handle:
                bind_label = access_host or "脚本默认（127.0.0.1）"
                header = (
                    f"\n===== [{_now_iso()}] start {model_id} on GPU {gpu_index} "
                    f"({gpu.name}), bind {bind_label} via web console =====\n"
                )
                log_handle.write(header.encode("utf-8"))
                log_handle.flush()
                try:
                    process = subprocess.Popen(
                        ["bash", str(script)],
                        cwd=str(cwd),
                        env=env,
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
                state.gpu_index = None
                state.last_status = "failed"
                state.last_error = f"启动进程立即退出（退出码 {process.returncode}）：{detail}"
                self._persist()
                raise StartError(f"{spec.name} 启动失败：{state.last_error}")

            info = proc.proc_info(process.pid)
            state.pid = process.pid
            state.pgid = info.pgid if info else process.pid
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
            state.gpu_index = None
            state.updated_at = time.time()
            self._procs.pop(model_id, None)
            self._persist()

            if not port_free:
                raise StopError(
                    f"{spec.name} 的进程已退出，但端口 {spec.port} 仍被占用（可能有其它监听进程）"
                )
            return f"已停止 {spec.name}（{source}，PID {pid}）"
