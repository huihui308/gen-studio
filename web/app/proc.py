"""Small /proc and socket helpers used to observe and stop real processes.

The console must be honest about what it can see, so every helper returns
``None`` rather than guessing when a value is unavailable.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    pgid: int
    state: str
    start_ticks: int
    cmdline: str


def _read_text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def proc_info(pid: int) -> ProcInfo | None:
    """Read a /proc entry; return ``None`` when the process does not exist."""
    if pid <= 0:
        return None
    stat = _read_text(f"/proc/{pid}/stat")
    if not stat:
        return None
    # comm may contain spaces/newlines/parentheses: split at the last ')'.
    closing = stat.rfind(")")
    if closing == -1:
        return None
    fields = stat[closing + 2 :].split()
    # Field 3 is state, fields 4/5 are ppid/pgrp, field 22 is starttime.
    # After splitting from field 3, indexes are shifted by 3.
    try:
        state = fields[0]
        ppid = int(fields[1])
        pgid = int(fields[2])
        start_ticks = int(fields[19])
    except (IndexError, ValueError):
        return None
    cmdline = (_read_text(f"/proc/{pid}/cmdline") or "").replace("\x00", " ").strip()
    return ProcInfo(pid=pid, ppid=ppid, pgid=pgid, state=state, start_ticks=start_ticks, cmdline=cmdline)


def is_alive(pid: int) -> bool:
    info = proc_info(pid)
    return info is not None and info.state not in {"Z", "X"}


def proc_start_epoch(pid: int) -> float | None:
    info = proc_info(pid)
    if info is None:
        return None
    uptime_raw = _read_text("/proc/uptime")
    if not uptime_raw:
        return None
    try:
        uptime = float(uptime_raw.split()[0])
    except (ValueError, IndexError):
        return None
    return time.time() - (uptime - info.start_ticks / _CLK_TCK)


def proc_environ(pid: int) -> dict[str, str]:
    raw = _read_text(f"/proc/{pid}/environ")
    if not raw:
        return {}
    result: dict[str, str] = {}
    for entry in raw.split("\x00"):
        if "=" in entry:
            key, value = entry.split("=", 1)
            result[key] = value
    return result


def proc_cuda_visible_devices(pid: int) -> str | None:
    """Return the process' CUDA_VISIBLE_DEVICES value, when readable."""
    value = proc_environ(pid).get("CUDA_VISIBLE_DEVICES")
    return value if value not in (None, "") else None


def proc_stdout_path(pid: int) -> str | None:
    """Resolve where a process writes stdout, when it is a regular file."""
    try:
        target = os.readlink(f"/proc/{pid}/fd/1")
    except OSError:
        return None
    if target.startswith("/") and Path(target).is_file():
        return target
    return None


def marker_match(cmdline: str, markers: tuple[str, ...]) -> bool:
    """True when every marker appears in the process command line."""
    haystack = cmdline.lower()
    return all(marker.lower() in haystack for marker in markers)


_SHELL_BASENAMES = {
    "bash", "sh", "dash", "zsh", "fish", "ksh",
    "nohup", "sudo", "setsid", "timeout", "env",
}


def find_processes_by_markers(markers: tuple[str, ...], exclude_pids: tuple[int, ...] = ()) -> list[ProcInfo]:
    """Processes whose command line matches every marker.

    This is the fallback used when an externally started model is still loading
    weights and has not opened its HTTP port yet, so ``listener_pid`` cannot see
    it. Shell wrappers are skipped: their command line can quote the markers
    without being the model process itself.
    """
    excluded = {os.getpid(), os.getppid(), *exclude_pids}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    matches: list[ProcInfo] = []
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in excluded:
            continue
        raw = _read_text(f"/proc/{pid}/cmdline")
        if not raw:
            continue
        parts = [part for part in raw.split("\x00") if part]
        if not parts or os.path.basename(parts[0]) in _SHELL_BASENAMES:
            continue
        if not marker_match(" ".join(parts), markers):
            continue
        info = proc_info(pid)
        if info is not None and info.state not in {"Z", "X"}:
            matches.append(info)
    return matches


def listener_pid(port: int) -> int | None:
    """PID listening on ``127.0.0.1:port`` (or ``*:port``), if discoverable."""
    ss = shutil.which("ss")
    if ss:
        try:
            completed = subprocess.run(
                [ss, "-ltnpH", f"sport = :{port}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            matches = re.findall(r"pid=(\d+)", completed.stdout)
            if matches:
                return int(matches[0])
    # Fallback: look for the socket inode in /proc/<pid>/fd.
    inode = _listening_inode(port)
    if inode is None:
        return None
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            entries = list(fd_dir.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target == f"socket:[{inode}]":
                return int(pid_dir.name)
    return None


def _listening_inode(port: int) -> str | None:
    target = f"{port:04X}"
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        text = _read_text(table)
        if not text:
            continue
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            local, state, inode = parts[1], parts[3], parts[9]
            if state == "0A" and local.rsplit(":", 1)[-1] == target:
                return inode
    return None


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def signal_process(pid: int, pgid: int | None, sig: int) -> None:
    """Signal the process group when it is safe to do so, otherwise the PID."""
    own_group = os.getpgrp()
    if pgid and pgid > 0 and pgid != own_group:
        try:
            os.killpg(pgid, sig)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def terminate(pid: int, pgid: int | None = None, *, grace_seconds: float = 20.0, kill_wait_seconds: float = 5.0) -> bool:
    """SIGTERM then SIGKILL. Returns True when the process is gone."""
    if not is_alive(pid):
        return True
    signal_process(pid, pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.2)
    signal_process(pid, pgid, signal.SIGKILL)
    deadline = time.monotonic() + kill_wait_seconds
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.2)
    return not is_alive(pid)
