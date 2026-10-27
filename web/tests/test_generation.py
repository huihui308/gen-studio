"""Tests for the H3 video-generation page and its asynchronous job API."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.generation import H3Client, H3Error, H3Status, validate_request

from conftest import csrf_token, login


def stopped(model_id: str):
    from app.manager import Detection

    return Detection(
        model_id=model_id,
        managed=False,
        pid=None,
        pgid=None,
        cmdline=None,
        start_epoch=None,
        gpu_index=None,
        gpu_source=None,
        listener_pid=None,
        unmanaged=False,
        marker_match=False,
        port_open=False,
        log_path=None,
        note=None,
    )


class FakeH3:
    """Small H3 double; no network and no model process."""

    def __init__(self, statuses=("completed",), error: str | None = None) -> None:
        self.statuses = list(statuses)
        self.error = error
        self.submitted: tuple[str, int, int] | None = None
        self.downloaded = False
        self._status_index = 0
        self._lock = __import__("threading").Lock()

    def submit(self, prompt: str, resolution: int, duration: int) -> str:
        self.submitted = (prompt, resolution, duration)
        return "h3-job-1"

    def status(self, job_id: str) -> H3Status:
        with self._lock:
            index = min(self._status_index, len(self.statuses) - 1)
            value = self.statuses[index]
            if index < len(self.statuses) - 1:
                self._status_index += 1
        if value == "failed":
            return H3Status("failed", error=self.error or "H3 执行失败")
        progress = 10 if value == "in_progress" else None
        return H3Status(value, progress=progress)

    def download(self, job_id: str, destination) -> int:
        self.downloaded = True
        destination.write_bytes(b"fake-mp4-bytes")
        return len(b"fake-mp4-bytes")


class BrokenH3(FakeH3):
    def submit(self, prompt: str, resolution: int, duration: int) -> str:
        raise H3Error("连接被拒绝")


def make_client(make_app, monkeypatch, fake: FakeH3) -> TestClient:
    app = make_app()
    monkeypatch.setattr(app.state.manager, "detect", lambda model_id: stopped(model_id))
    manager = app.state.generation_manager
    manager.client = fake
    manager.poll_interval = 0.01
    manager.max_wait_seconds = 5
    return TestClient(app)


def wait_for_job(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    latest = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/generate/{job_id}")
        assert response.status_code == 200
        latest = response.json()["job"]
        if latest["status"] in {"completed", "failed"}:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {latest}")


# ------------------------------------------------------------------ page/auth
def test_generate_page_requires_login(make_app):
    client = TestClient(make_app())
    response = client.get("/generate", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_generate_page_defaults_and_form(make_app, monkeypatch):
    client = make_client(make_app, monkeypatch, FakeH3())
    login(client)
    page = client.get("/generate")
    assert page.status_code == 200
    assert '<option value="480" selected>' in page.text
    assert '<option value="10" selected>' in page.text
    assert 'name="prompt"' in page.text
    assert 'id="result-video"' in page.text
    assert 'id="result-prompt"' in page.text
    assert "/static/generate.js" in page.text


# -------------------------------------------------------------- API validation
def test_h3_client_submit_maps_resolution_and_duration(monkeypatch):
    """The UI parameters must land in H3's documented JSON fields."""

    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"id":"h3-job-xyz"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["payload"] = __import__("json").loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("app.generation.urlopen", fake_urlopen)
    client = H3Client(model="/models/MiniMax-H3")
    assert client.submit("窗边的猫", 736, 6) == "h3-job-xyz"

    payload = captured["payload"]
    assert payload["task"] == "t2va"
    assert payload["prompt"] == "窗边的猫"
    assert payload["seconds"] == 6
    assert payload["target"] == {
        "short_edge": 736,
        "aspect_ratio": "16:9",
        "duration_seconds": 6.0,
    }
    assert payload["num_inference_steps"] == 20


def test_generate_api_requires_login(make_app):
    client = TestClient(make_app())
    response = client.post("/api/generate", data={})
    assert response.status_code == 401


def test_blank_prompt_is_rejected(make_app, monkeypatch):
    client = make_client(make_app, monkeypatch, FakeH3())
    login(client)
    token = csrf_token(client, "/generate")
    response = client.post(
        "/api/generate",
        data={
            "csrf_token": token,
            "prompt": "   ",
            "resolution": "480",
            "duration": "10",
        },
    )
    assert response.status_code == 400
    assert "提示词不能为空" in response.json()["error"]


@pytest.mark.parametrize(
    ("resolution", "duration", "message"),
    [
        ("999", "10", "分辨率"),
        ("480", "3", "时长"),
        ("480", "16", "时长"),
        ("abc", "10", "分辨率"),
    ],
)
def test_invalid_parameters_are_rejected(make_app, monkeypatch, resolution, duration, message):
    client = make_client(make_app, monkeypatch, FakeH3())
    login(client)
    token = csrf_token(client, "/generate")
    response = client.post(
        "/api/generate",
        data={
            "csrf_token": token,
            "prompt": "一只猫在窗边",
            "resolution": resolution,
            "duration": duration,
        },
    )
    assert response.status_code == 400
    assert message in response.json()["error"]


def test_api_requires_csrf(make_app, monkeypatch):
    client = make_client(make_app, monkeypatch, FakeH3())
    login(client)
    response = client.post(
        "/api/generate",
        data={"prompt": "一只猫", "resolution": "480", "duration": "10"},
    )
    assert response.status_code == 403
    assert "会话校验失败" in response.json()["error"]


# ---------------------------------------------------------------- job success
def test_generation_job_completes_and_serves_video(make_app, monkeypatch):
    fake = FakeH3(statuses=("queued", "in_progress", "completed"))
    client = make_client(make_app, monkeypatch, fake)
    login(client)
    token = csrf_token(client, "/generate")
    response = client.post(
        "/api/generate",
        data={
            "csrf_token": token,
            "prompt": "窗边的猫，午后逆光",
            "resolution": "736",
            "duration": "6",
        },
    )
    assert response.status_code == 201
    job = response.json()["job"]
    assert job["prompt"] == "窗边的猫，午后逆光"
    assert job["resolution"] == 736
    assert job["duration"] == 6

    finished = wait_for_job(client, job["id"])
    assert finished["status"] == "completed"
    assert finished["progress"] == 100
    assert finished["video_url"] == f"/api/generate/{job['id']}/video"
    assert fake.submitted == ("窗边的猫，午后逆光", 736, 6)
    assert fake.downloaded is True

    video = client.get(finished["video_url"])
    assert video.status_code == 200
    assert video.content == b"fake-mp4-bytes"
    assert video.headers["content-type"].startswith("video/mp4")


def test_failed_job_reports_error_and_has_no_video(make_app, monkeypatch):
    fake = FakeH3(statuses=("in_progress", "failed"), error="显存不足，H3 已停止")
    client = make_client(make_app, monkeypatch, fake)
    login(client)
    token = csrf_token(client, "/generate")
    response = client.post(
        "/api/generate",
        data={
            "csrf_token": token,
            "prompt": "测试失败",
            "resolution": "480",
            "duration": "10",
        },
    )
    assert response.status_code == 201
    finished = wait_for_job(client, response.json()["job"]["id"])
    assert finished["status"] == "failed"
    assert "显存不足" in finished["error"]
    assert finished["video_url"] is None
    assert client.get(f"/api/generate/{finished['id']}/video").status_code == 404


def test_submit_failure_returns_502(make_app, monkeypatch):
    client = make_client(make_app, monkeypatch, BrokenH3())
    login(client)
    token = csrf_token(client, "/generate")
    response = client.post(
        "/api/generate",
        data={
            "csrf_token": token,
            "prompt": "无法提交",
            "resolution": "480",
            "duration": "10",
        },
    )
    assert response.status_code == 502
    assert "提交生成任务失败" in response.json()["error"]
    assert "连接被拒绝" in response.json()["error"]


def test_unknown_job_and_video_return_404(make_app, monkeypatch):
    client = make_client(make_app, monkeypatch, FakeH3())
    login(client)
    assert client.get("/api/generate/not-a-job").status_code == 404
    assert client.get("/api/generate/not-a-job/video").status_code == 404


# --------------------------------------------------------------- validation unit
def test_validate_request_accepts_defaults_and_does_not_clamp():
    assert validate_request("  hello  ", "480", "10") == ("hello", 480, 10)
    with pytest.raises(Exception):
        validate_request("hello", "481", "10")
    with pytest.raises(Exception):
        validate_request("hello", "480", "15.5")
