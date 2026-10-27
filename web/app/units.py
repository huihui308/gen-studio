"""Run each model in its own systemd user unit.

The console must be restartable to deploy Python changes without killing the
models it operates.  Models therefore live in transient user units instead of
being children of the web process.  The console only writes the per-start
environment, starts the unit, and observes it with ``systemctl show``.

Only the Python standard library is used.  The class is deliberately small so
tests can replace it with a fake and exercise the manager without touching the
real systemd instance.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class UnitError(Exception):
    """A systemd user unit could not be started, inspected or stopped."""


@dataclass(frozen=True)
class UnitStatus:
    name: str
    load_state: str = "not-found"
    active_state: str = "inactive"
    sub_state: str = ""
    main_pid: int | None = None

    @property
    def active(self) -> bool:
        return self.active_state in {"active", "activating", "reloading"}


class SystemdUserUnits:
    """Thin, explicit wrapper around ``systemctl --user`` / ``systemd-run --user``."""

    UNIT_PREFIX = "gen-studio"

    def __init__(
        self,
        systemctl: str = "systemctl",
        runner: str = "systemd-run",
        env: dict[str, str] | None = None,
    ) -> None:
        self.systemctl = systemctl
        self.runner = runner
        self._base_env = dict(env or os.environ)
        # A console running as a user service normally gets this from
        # systemd, but setting it explicitly keeps CLI tests working too.
        self._base_env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

    def available(self) -> bool:
        return bool(shutil.which(self.systemctl) and shutil.which(self.runner))

    @classmethod
    def unit_name(cls, model_id: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in model_id)
        return f"{cls.UNIT_PREFIX}-{safe}"

    def _run(
        self,
        args: list[str],
        *,
        timeout: float = 15.0,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.systemctl, "--user", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._base_env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise UnitError(f"找不到 {self.systemctl}") from exc
        except subprocess.TimeoutExpired as exc:
            raise UnitError(f"systemctl --user {' '.join(args)} 超时") from exc

    def status(self, unit: str) -> UnitStatus:
        result = self._run(
            [
                "show",
                unit,
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
            ],
            timeout=8.0,
        )
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        raw_pid = values.get("MainPID", "")
        main_pid = int(raw_pid) if raw_pid.isdigit() and int(raw_pid) > 0 else None
        return UnitStatus(
            name=unit,
            load_state=values.get("LoadState", "not-found"),
            active_state=values.get("ActiveState", "inactive"),
            sub_state=values.get("SubState", ""),
            main_pid=main_pid,
        )

    def start(
        self,
        unit: str,
        script: Path,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
    ) -> None:
        if not self.available():
            raise UnitError("systemd 用户实例不可用（找不到 systemctl / systemd-run）")
        current = self.status(unit)
        if current.active:
            raise UnitError(f"systemd 单元 {unit} 已在运行")
        # A transient unit normally disappears with --collect, but a previous
        # failed start can leave it in the loaded state.
        self._run(["reset-failed", unit], timeout=8.0)

        command = f"exec {shlex.quote(str(script))} >> {shlex.quote(str(log_path))} 2>&1"
        args = [
            self.runner,
            "--user",
            "--unit",
            unit,
            "--collect",
            "--service-type=exec",
            "--working-directory",
            str(cwd),
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=30",
            "--property=Restart=no",
        ]
        for key, value in env.items():
            args.extend(["--setenv", f"{key}={value}"])
        args.extend(["--", "/bin/bash", "-lc", command])
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=15.0,
                env=self._base_env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise UnitError(f"找不到 {self.runner}") from exc
        except subprocess.TimeoutExpired as exc:
            raise UnitError("systemd-run 启动超时") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise UnitError(detail or f"systemd 单元 {unit} 启动失败")

    def stop(self, unit: str, *, timeout: float = 40.0) -> bool:
        result = self._run(["stop", unit], timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise UnitError(detail or f"停止 {unit} 失败")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.status(unit).active:
                return True
            time.sleep(0.2)
        return False
