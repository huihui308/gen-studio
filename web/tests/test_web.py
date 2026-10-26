"""Behavioural tests for the five specified requirements.

The tests drive the real FastAPI routes and the real process manager; only
``nvidia-smi`` and the model entry scripts are replaced (see conftest.py).
"""

from __future__ import annotations

import os
import re
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import app.status as status_module
from app import proc
from app.manager import Detection, ModelManager, StartError, StopError
from app.status import _HttpResult

from app.proc import ProcInfo
from app.status import StatusCollector

from conftest import FakeProbe, csrf_token, is_alive, login


def stopped(model_id: str) -> Detection:
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


def fields_by_label(fields: list[dict]) -> dict[str, dict]:
    return {item["label"]: item for item in fields}


# --------------------------------------------------------------- requirement 1
def test_unauthenticated_pages_redirect_to_login(make_app):
    client = TestClient(make_app())
    for path in ("/", "/control", "/status", "/password", "/status/partial", "/api/status"):
        response = client.get(path, follow_redirects=False)
        if path == "/api/status":
            assert response.status_code == 401
            assert response.json()["login_url"] == "/login"
        else:
            assert response.status_code == 303, path
            location = response.headers["location"]
            if path == "/":
                assert location == "/login"
            else:
                assert location.startswith("/login?next=")
                assert path in location


def test_login_with_default_credentials(client):
    response = login(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/control"
    cookie = response.headers.get("set-cookie", "")
    assert "gen_studio_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    page = client.get("/control")
    assert page.status_code == 200
    assert "模型控制" in page.text
    assert "运行状态" in page.text
    assert "密码设置" in page.text


def test_login_rejects_wrong_password(client):
    response = login(client, password="not-admin")
    assert response.status_code == 401
    assert "用户名或密码不正确" in response.text
    assert "gen_studio_session=" not in response.headers.get("set-cookie", "")
    assert client.get("/control", follow_redirects=False).status_code == 303


def test_login_refuses_open_redirect(make_app):
    client = TestClient(make_app())
    for target in ("//evil.example/x", "http://evil.example/x", "\\\\evil"):
        response = client.post(
            "/login",
            data={"username": "admin", "password": "admin", "next_path": target},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/control"


def test_logout_requires_csrf_and_clears_session(client):
    login(client)
    token = csrf_token(client, "/control")
    response = client.post("/logout", data={"csrf_token": "wrong"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert client.get("/control").status_code == 200

    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/control", follow_redirects=False).status_code == 303


# --------------------------------------------------------------- requirement 2
def test_password_change_flow(make_app):
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client)

    response = client.post(
        "/password",
        data={
            "csrf_token": token,
            "current_password": "admin",
            "new_password": "s3cret-pass",
            "confirm_password": "s3cret-pass",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]

    # Current session survives the change and sees the new state.
    page = client.get("/password")
    assert page.status_code == 200
    assert "密码已自定义" in page.text

    # Old credentials no longer work, new ones do.
    other = TestClient(app)
    assert login(other, password="admin").status_code == 401
    assert login(other, password="s3cret-pass").status_code == 303
    assert other.get("/control").status_code == 200


def test_password_change_revokes_other_sessions(make_app):
    app = make_app()
    first = TestClient(app)
    second = TestClient(app)
    login(first)
    login(second)
    assert second.get("/control").status_code == 200

    token = csrf_token(first)
    response = first.post(
        "/password",
        data={
            "csrf_token": token,
            "current_password": "admin",
            "new_password": "another-pass",
            "confirm_password": "another-pass",
        },
        follow_redirects=False,
    )
    assert "notice=" in response.headers["location"]
    assert second.get("/control", follow_redirects=False).status_code == 303
    assert first.get("/control").status_code == 200


@pytest.mark.parametrize(
    ("current", "new", "confirm", "message"),
    [
        ("admin", "new-pass", "new-pass-2", "两次输入的新密码不一致"),
        ("admin", "abc", "abc", "至少需要 6 个字符"),
        ("wrong-pass", "new-pass", "new-pass", "当前密码不正确"),
        ("admin", "", "", "新密码不能为空"),
    ],
)
def test_password_validation(client, current, new, confirm, message):
    login(client)
    token = csrf_token(client)
    response = client.post(
        "/password",
        data={
            "csrf_token": token,
            "current_password": current,
            "new_password": new,
            "confirm_password": confirm,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert message in page.text


def test_password_page_shows_local_timestamp(client):
    login(client)
    text = client.get("/password").text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text)


def test_password_csrf_is_enforced(client):
    login(client)
    response = client.post(
        "/password",
        data={
            "csrf_token": "forged",
            "current_password": "admin",
            "new_password": "new-pass",
            "confirm_password": "new-pass",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "会话校验失败" in page.text


# --------------------------------------------------------------- requirement 3
def test_control_page_lists_gpus_without_preselecting(client):
    login(client)
    page = client.get("/control")
    text = page.text
    assert "GPU 0 · NVIDIA GeForce RTX 3080 Ti" in text
    assert "GPU 1 · NVIDIA GeForce RTX 3080 Ti" in text
    assert 'name="gpu_index" required' in text
    assert 'value=""' in text  # placeholder: nothing is preselected


def test_control_page_warns_when_no_gpu_is_available(make_app):
    from conftest import FakeProbe

    app = make_app(FakeProbe(gpus=(), error="未找到 nvidia-smi 可执行文件（nvidia-smi）"))
    client = TestClient(app)
    login(client)
    page = client.get("/control")
    assert page.status_code == 200
    assert "未找到 nvidia-smi 可执行文件" in page.text
    assert "无法获取显卡信息" in page.text
    assert "disabled" in page.text
    assert "请选择显卡" in page.text


def test_start_without_gpu_is_refused(make_app, settings):
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client)

    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "必须选择一张显卡" in page.text
    assert app.state.manager.state("qwen-image2.0").pid is None
    assert not (settings.repo_root / "gpu-qwen.txt").exists()


def test_start_refused_when_gpu_probe_unavailable(make_app, settings):
    from conftest import FakeProbe

    app = make_app(FakeProbe(gpus=(), error="未找到 nvidia-smi 可执行文件（nvidia-smi）"))
    client = TestClient(app)
    login(client)
    token = csrf_token(client)

    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "无法获取显卡信息" in page.text
    assert "不会使用默认显卡" in page.text
    assert app.state.manager.state("qwen-image2.0").pid is None
    assert not (settings.repo_root / "gpu-qwen.txt").exists()


def test_start_rejects_unknown_gpu_index(make_app, settings):
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client)

    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": "9"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "所选显卡 9 不存在" in page.text
    assert app.state.manager.state("qwen-image2.0").pid is None


def test_start_passes_selected_gpu_and_stop_kills_it(make_app, settings):
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client)

    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "已在 GPU 1 上启动" in page.text

    state = app.state.manager.state("qwen-image2.0")
    assert state.pid is not None
    assert state.gpu_index == 1
    assert (settings.repo_root / "gpu-qwen.txt").read_text() == "1"

    detection = app.state.manager.detect("qwen-image2.0")
    assert detection.managed is True
    assert detection.pid == state.pid

    assert "运行中（本系统）" in client.get("/control").text

    pid = state.pid
    response = client.post(
        "/control/qwen-image2.0/stop",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    assert app.state.manager.state("qwen-image2.0").pid is None
    time.sleep(0.2)
    assert not is_alive(pid)


def test_start_refuses_when_port_is_occupied_by_unknown_process(make_app, monkeypatch):
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client)

    detection = Detection(
        model_id="qwen-image2.0",
        managed=False,
        pid=os.getpid(),
        pgid=os.getpgrp(),
        cmdline="python -m http.server 30020",
        start_epoch=time.time() - 5,
        gpu_index=None,
        gpu_source=None,
        listener_pid=os.getpid(),
        unmanaged=True,
        marker_match=False,
        port_open=True,
        log_path=None,
        note="外部进程",
    )
    monkeypatch.setattr(app.state.manager, "detect", lambda model_id: detection)

    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "已被占用" in page.text


def test_stop_refuses_unknown_process_without_killing_it(make_app, monkeypatch):
    app = make_app()
    manager = app.state.manager
    detection = Detection(
        model_id="qwen-image2.0",
        managed=False,
        pid=os.getpid(),
        pgid=os.getpgrp(),
        cmdline="python -m http.server 30020",
        start_epoch=time.time() - 5,
        gpu_index=None,
        gpu_source=None,
        listener_pid=os.getpid(),
        unmanaged=True,
        marker_match=False,
        port_open=True,
        log_path=None,
        note="外部进程",
    )
    monkeypatch.setattr(manager, "detect", lambda model_id: detection)

    def must_not_kill(*args, **kwargs):
        raise AssertionError("拒绝停止的路径不得调用 terminate")

    monkeypatch.setattr(proc, "terminate", must_not_kill)
    with pytest.raises(StopError):
        manager.stop("qwen-image2.0")


def test_start_and_stop_require_csrf(client):
    login(client)
    for action in ("start", "stop"):
        response = client.post(
            f"/control/qwen-image2.0/{action}",
            data={"csrf_token": "forged", "gpu_index": "0"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "会话校验失败" in page.text


# --------------------------------------------------------------- requirement 4
def test_status_page_marks_unavailable_instead_of_inventing(make_app, monkeypatch):
    app = make_app()
    client = TestClient(app)
    login(client)
    monkeypatch.setattr(app.state.manager, "detect", lambda model_id: stopped(model_id))

    payload = app.state.collector.collect()
    assert payload["gpus"]["available"] is True
    for model_id in ("minimax-h3", "qwen-image2.0"):
        model = payload["models"][model_id]
        runtime = fields_by_label(model["runtime_fields"])
        assert runtime["服务健康"]["available"] is False
        assert "未发起健康检查" in runtime["服务健康"]["note"]
        assert runtime["运行进程"]["available"] is False
        assert model["state"]["code"] == "stopped"

    h3 = fields_by_label(payload["models"]["minimax-h3"]["runtime_fields"])
    assert h3["队列 / 当前任务"]["available"] is False
    assert "未暴露" in h3["队列 / 当前任务"]["note"]
    assert h3["生成耗时统计"]["available"] is False

    qwen = fields_by_label(payload["models"]["qwen-image2.0"]["runtime_fields"])
    assert qwen["队列运行 / 等待"]["available"] is False

    page = client.get("/status")
    assert page.status_code == 200
    assert "不可用" in page.text
    assert "badge-unavailable" in page.text
    assert "显卡概览" in page.text


def test_h3_status_reports_real_api_values_and_queue_stays_unavailable(make_app, monkeypatch):
    app = make_app()
    login(TestClient(app))

    detection = Detection(
        model_id="minimax-h3",
        managed=True,
        pid=4242,
        pgid=4242,
        cmdline="sglang serve MiniMax-H3",
        start_epoch=time.time() - 120,
        gpu_index=1,
        gpu_source="本系统启动记录",
        listener_pid=4242,
        unmanaged=False,
        marker_match=False,
        port_open=True,
        log_path=None,
        note=None,
    )
    monkeypatch.setattr(
        app.state.manager,
        "detect",
        lambda model_id: detection if model_id == "minimax-h3" else stopped(model_id),
    )

    def fake_http(url: str, timeout: float):
        if url.endswith("/health"):
            return _HttpResult(True, 200, {"status": "ok"}, 4, None)
        if url.endswith("/server_info"):
            return _HttpResult(
                True,
                200,
                {
                    "served_model_name": "MiniMax-H3",
                    "version": "0.5.22",
                    "tp_size": 1,
                    "dp_size": 1,
                },
                5,
                None,
            )
        if url.endswith("/v1/models"):
            return _HttpResult(
                True,
                200,
                {
                    "data": [
                        {
                            "task_type": "TI2V",
                            "supported_task_types": ["TI2V"],
                            "pipeline_name": "fl2va",
                            "dit_precision": "bf16",
                            "vae_precision": "fp32",
                        }
                    ]
                },
                6,
                None,
            )
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(status_module, "_http_get_json", fake_http)
    payload = app.state.collector.collect()
    model = payload["models"]["minimax-h3"]
    runtime = fields_by_label(model["runtime_fields"])
    capability = fields_by_label(model["capability_fields"])

    assert model["state"]["code"] == "running"
    assert runtime["服务健康"]["available"] is True
    assert "正常" in runtime["服务健康"]["value"]
    assert runtime["队列 / 当前任务"]["available"] is False
    assert runtime["生成耗时统计"]["available"] is False
    assert capability["已加载模型"]["value"] == "MiniMax-H3"
    assert capability["运行期任务类型"]["value"].startswith("TI2V")
    assert capability["推理精度"]["value"] == "DiT bf16 · VAE fp32"
    assert all(item["available"] for item in model["capability_fields"])


def test_health_failure_while_loading_is_reported_as_not_ready(make_app, monkeypatch):
    app = make_app()
    detection = Detection(
        model_id="minimax-h3",
        managed=True,
        pid=4747,
        pgid=4747,
        cmdline="python -m sglang.launch_server MiniMax-H3",
        start_epoch=time.time() - 15,
        gpu_index=1,
        gpu_source="本系统启动记录",
        listener_pid=None,
        unmanaged=False,
        marker_match=True,
        port_open=False,
        log_path=None,
        note=None,
    )
    monkeypatch.setattr(
        app.state.manager,
        "detect",
        lambda model_id: detection if model_id == "minimax-h3" else stopped(model_id),
    )
    monkeypatch.setattr(
        status_module,
        "_http_get_json",
        lambda url, timeout: _HttpResult(False, None, None, None, "连接被拒绝"),
    )
    payload = app.state.collector.collect()
    model = payload["models"]["minimax-h3"]
    runtime = fields_by_label(model["runtime_fields"])
    assert model["state"]["code"] == "starting"
    assert runtime["服务健康"]["value"] == "未就绪（加载中）"
    assert "连接被拒绝" in runtime["服务健康"]["note"]


def test_qwen_status_reads_real_queue(make_app, monkeypatch):
    app = make_app()
    login(TestClient(app))

    def detect(model_id: str) -> Detection:
        if model_id == "minimax-h3":
            return stopped(model_id)
        return Detection(
            model_id=model_id,
            managed=False,
            pid=5151,
            pgid=5151,
            cmdline="python ComfyUI/main.py --port 30020",
            start_epoch=time.time() - 60,
            gpu_index=0,
            gpu_source="进程环境 CUDA_VISIBLE_DEVICES=0",
            listener_pid=5151,
            unmanaged=True,
            marker_match=True,
            port_open=True,
            log_path=None,
            note="外部进程",
        )

    monkeypatch.setattr(app.state.manager, "detect", detect)

    def fake_http(url: str, timeout: float):
        if url.endswith("/system_stats"):
            return _HttpResult(
                True,
                200,
                {
                    "system": {
                        "comfyui_version": "0.42.0",
                        "pytorch_version": "2.9.0",
                    },
                    "devices": [{"vram_total": 12 * 1024**3, "vram_free": 3 * 1024**3}],
                },
                5,
                None,
            )
        if url.endswith("/queue"):
            return _HttpResult(
                True,
                200,
                {
                    "queue_running": [[1, "prompt-1", {}, {}, {}]],
                    "queue_pending": [[2, "prompt-2"], [3, "prompt-3"]],
                },
                5,
                None,
            )
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(status_module, "_http_get_json", fake_http)
    payload = app.state.collector.collect()
    model = payload["models"]["qwen-image2.0"]
    runtime = fields_by_label(model["runtime_fields"])
    capability = fields_by_label(model["capability_fields"])

    assert runtime["队列运行 / 等待"]["value"] == "1 / 2"
    assert runtime["当前任务"]["value"] == "prompt_id prompt-1"
    assert model["gpu"]["available"] is True
    assert model["gpu"]["index"] == 0
    assert capability["ComfyUI 版本"]["value"] == "0.42.0"
    assert capability["后端可见显存"]["available"] is True


def test_status_partial_is_authenticated_fragment(client):
    login(client)
    response = client.get("/status/partial")
    assert response.status_code == 200
    assert "运行指标" in response.text
    assert "<!DOCTYPE html>" not in response.text


def test_api_status_returns_observed_data(client):
    login(client)
    response = client.get("/api/status", headers={"Accept": "application/json"})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"generated_at", "generated_at_local", "gpus", "models"}
    assert set(data["models"]) == {"minimax-h3", "qwen-image2.0"}
    assert data["gpus"]["available"] is True


# -------------------------------------------------------------- requirement 5
def test_business_style_assets_are_served(client):
    login(client)
    page = client.get("/control")
    assert 'href="/static/app.css"' in page.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "--navy-950" in css.text
    assert client.get("/static/favicon.svg").status_code == 200


def test_unguarded_health_endpoint_has_no_secrets(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ------------------------------------------------- external loading processes
def test_marker_scan_finds_process_without_listening_port():
    child = subprocess.Popen(["bash", "-c", 'exec -a "sglang MiniMax-H3" sleep 30'])
    try:
        found: list[int] = []
        deadline = time.time() + 5
        while time.time() < deadline:
            found = [item.pid for item in proc.find_processes_by_markers(("sglang", "MiniMax-H3"))]
            if child.pid in found:
                break
            time.sleep(0.05)
        assert child.pid in found
    finally:
        child.kill()
        child.wait()


def test_external_loading_process_blocks_start(settings, monkeypatch):
    info = ProcInfo(
        pid=4242,
        ppid=1,
        pgid=4242,
        state="S",
        start_ticks=1,
        cmdline="python /env/bin/sglang serve --model-path /models/MiniMax-H3",
    )
    monkeypatch.setattr("app.manager.proc.listener_pid", lambda port: None)
    monkeypatch.setattr("app.manager.proc.port_open", lambda port: False)
    manager = ModelManager(
        settings,
        FakeProbe(),
        process_finder=lambda markers: [info] if "MiniMax-H3" in markers else [],
    )
    detection = manager.detect("minimax-h3")
    assert detection.unmanaged is True
    assert detection.pid == 4242
    assert detection.marker_match is True
    assert detection.port_open is False
    with pytest.raises(StartError, match="已有控制台之外启动的进程"):
        manager.start("minimax-h3", 1)
    assert not (settings.repo_root / "gpu-h3.txt").exists()


def test_external_loading_state_is_starting(settings, monkeypatch):
    info = ProcInfo(
        pid=4243,
        ppid=1,
        pgid=4243,
        state="S",
        start_ticks=2,
        cmdline="python /env/bin/sglang serve --model-path /models/MiniMax-H3",
    )
    monkeypatch.setattr("app.manager.proc.listener_pid", lambda port: None)
    monkeypatch.setattr("app.manager.proc.port_open", lambda port: False)
    manager = ModelManager(
        settings,
        FakeProbe(),
        process_finder=lambda markers: [info] if "MiniMax-H3" in markers else [],
    )
    collector = StatusCollector(settings, FakeProbe(), manager)
    payload = collector.collect()["models"]["minimax-h3"]
    assert payload["state"]["code"] == "starting"
    assert payload["state"]["label"] == "启动中（外部进程）"
    assert "端口尚未监听" in payload["state"]["note"]
