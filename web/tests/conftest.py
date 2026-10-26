"""Shared fixtures: a throw-away repo, fake GPUs and app/client helpers.

The tests never touch the real H3 / ComfyUI processes or the real
``web/data`` directory.  Model entry scripts are replaced by tiny bash
launchers that record ``CUDA_VISIBLE_DEVICES`` and then sleep, which exercises
the real spawn / ownership / stop code paths without loading a model.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.gpu import Gpu, GpuList
from app.main import create_app

H3_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail
printf '%s' "$CUDA_VISIBLE_DEVICES" > "$(cd "$(dirname "$0")/.." && pwd)/gpu-h3.txt"
exec -a "sglang MiniMax-H3" sleep 60
"""

QWEN_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail
printf '%s' "$CUDA_VISIBLE_DEVICES" > "$(cd "$(dirname "$0")/.." && pwd)/gpu-qwen.txt"
exec -a "ComfyUI/main.py --port 30020" sleep 60
"""


class FakeProbe:
    """Deterministic GPU probe; ``available=False`` simulates a broken driver."""

    def __init__(self, gpus: tuple[Gpu, ...] | None = None, error: str | None = None) -> None:
        if gpus is None:
            gpus = (
                Gpu(0, "GPU-aaaa", "NVIDIA GeForce RTX 3080 Ti", 12288, 1024, 11264, 12, 41),
                Gpu(1, "GPU-bbbb", "NVIDIA GeForce RTX 3080 Ti", 12288, 11667, 621, 98, 72),
            )
        self._gpus = gpus
        self._error = error

    def list(self) -> GpuList:
        return GpuList(available=self._error is None, gpus=self._gpus, error=self._error)

    def get(self, index: int):
        if self._error is not None:
            return None, self._error
        for gpu in self._gpus:
            if gpu.index == index:
                return gpu, None
        return None, f"显卡索引 {index} 不存在"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    repo = tmp_path / "repo"
    h3_dir = repo / "h3"
    qwen_dir = repo / "qwen-image"
    h3_dir.mkdir(parents=True)
    qwen_dir.mkdir(parents=True)
    (h3_dir / "serve_recipe_a.sh").write_text(textwrap.dedent(H3_SCRIPT), encoding="utf-8")
    (qwen_dir / "serve.sh").write_text(textwrap.dedent(QWEN_SCRIPT), encoding="utf-8")
    result = Settings(
        repo_root=repo,
        data_dir=tmp_path / "data",
        health_timeout_seconds=0.3,
    )
    result.ensure_dirs()
    return result


@pytest.fixture()
def make_app(settings: Settings):
    """Build an app with an injected GPU probe and clean up spawned children."""

    apps = []

    def _make(probe: FakeProbe | None = None):
        app = create_app(settings)
        fake = probe or FakeProbe()
        app.state.gpu_probe = fake
        app.state.manager.gpu_probe = fake
        app.state.collector.gpu_probe = fake
        # Never let the host's real model processes leak into these tests.
        app.state.manager._process_finder = lambda markers: []
        apps.append(app)
        return app

    yield _make

    for app in apps:
        for model_id in ("minimax-h3", "qwen-image2.0"):
            try:
                app.state.manager.stop(model_id)
            except Exception:
                pass


@pytest.fixture()
def client(make_app):
    with TestClient(make_app()) as test_client:
        yield test_client


def login(client: TestClient, password: str = "admin", username: str = "admin"):
    return client.post(
        "/login",
        data={"username": username, "password": password, "next_path": "/control"},
        follow_redirects=False,
    )


def csrf_token(client: TestClient, path: str = "/password") -> str:
    page = client.get(path)
    marker = 'name="csrf_token" value="'
    start = page.text.index(marker) + len(marker)
    return page.text[start : page.text.index('"', start)]


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
