"""Model lifecycle tests for the systemd-unit backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.manager import ModelManager, StartError
from app.units import UnitError, UnitStatus

from conftest import FakeProbe


class FakeUnits:
    """Stand-in for ``SystemdUserUnits``; no real systemd calls."""

    def available(self) -> bool:
        return True

    def __init__(self) -> None:
        self.active = False
        self.main_pid: int | None = None
        self.start_calls: list[dict] = []
        self.stop_calls: list[str] = []
        self.fail_start: str | None = None

    def unit_name(self, model_id: str) -> str:
        return f"gen-studio-{model_id}"

    def status(self, unit: str) -> UnitStatus:
        if self.active:
            return UnitStatus(unit, "loaded", "active", "running", self.main_pid)
        return UnitStatus(unit, "not-found", "inactive", "", None)

    def start(self, unit: str, script: Path, cwd: Path, env: dict, log_path: Path) -> None:
        if self.fail_start:
            raise UnitError(self.fail_start)
        self.start_calls.append(
            {
                "unit": unit,
                "script": script,
                "cwd": cwd,
                "env": dict(env),
                "log_path": log_path,
            }
        )
        self.active = True
        self.main_pid = 424242

    def stop(self, unit: str, *, timeout: float = 40.0) -> bool:
        self.stop_calls.append(unit)
        self.active = False
        self.main_pid = None
        return True


@pytest.fixture()
def manager(settings, monkeypatch):
    monkeypatch.setattr("app.manager.proc.port_open", lambda port, **kwargs: False)
    monkeypatch.setattr("app.manager.proc.listener_pid", lambda port: None)
    fake = FakeUnits()
    manager = ModelManager(settings, FakeProbe(), unit_controller=fake)
    return manager, fake, settings


def test_start_uses_an_independent_unit_and_records_it(manager):
    manager, fake, settings = manager
    state = manager.start("qwen-image2.0", 0, "0.0.0.0")

    assert fake.start_calls, "model should be handed to systemd-run"
    call = fake.start_calls[0]
    assert call["unit"] == "gen-studio-qwen-image2.0"
    assert call["env"]["CUDA_VISIBLE_DEVICES"] == "0"
    assert call["env"]["GEN_STUDIO_MODEL_HOST"] == "0.0.0.0"
    assert call["log_path"] == settings.model_log_path("qwen-image2.0")
    assert state.unit_name == "gen-studio-qwen-image2.0"
    assert state.pid == 424242
    assert state.last_status == "starting"

    detection = manager.detect("qwen-image2.0")
    assert detection.managed is True
    assert detection.unit_name == "gen-studio-qwen-image2.0"
    assert detection.unmanaged is False
    assert "重启控制台不会停止模型" in (detection.note or "")


def test_new_console_instance_reconnects_without_starting_again(manager):
    first, fake, settings = manager
    first.start("qwen-image2.0", 0, "127.0.0.1")
    assert len(fake.start_calls) == 1

    # Simulate a console restart: a new manager loads the same state file while
    # the systemd unit is still running.
    restarted = ModelManager(settings, FakeProbe(), unit_controller=fake)
    detection = restarted.detect("qwen-image2.0")
    assert detection.managed is True
    assert detection.unit_name == "gen-studio-qwen-image2.0"
    assert detection.pid == 424242
    assert len(fake.start_calls) == 1, "restart must not start the model again"

    restarted.stop("qwen-image2.0")
    assert fake.stop_calls == ["gen-studio-qwen-image2.0"]
    assert restarted.state("qwen-image2.0").last_status == "stopped"


def test_unit_start_failure_is_reported_and_state_is_failed(manager):
    manager, fake, _ = manager
    fake.fail_start = "unit refused to start"

    with pytest.raises(StartError, match="无法启动 Qwen-Image 2.0"):
        manager.start("qwen-image2.0", 0, "127.0.0.1")

    state = manager.state("qwen-image2.0")
    assert state.last_status == "failed"
    assert "unit refused to start" in state.last_error
    assert state.pid is None


def test_stop_uses_systemd_when_the_unit_is_active(manager):
    manager, fake, _ = manager
    fake.active = True
    fake.main_pid = 424242

    message = manager.stop("qwen-image2.0")

    assert "systemd 单元" in message
    assert fake.stop_calls == ["gen-studio-qwen-image2.0"]
    assert manager.state("qwen-image2.0").unit_name is None
