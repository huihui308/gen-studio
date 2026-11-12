"""Device gauges on the status page: /proc sampling, tones and placeholders.

The collector reads real system files (/proc/stat, /proc/meminfo); these
tests assert the payload contract, the level thresholds, the rendered SVG
section and the honest-unavailable paths (no NaN, no empty gauge).
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.status import _read_proc_stat, level

from conftest import FakeProbe, login


def test_api_status_includes_device_block(client):
    login(client)
    data = client.get("/api/status").json()
    assert set(data) == {"generated_at", "generated_at_local", "gpus", "device", "models"}

    cpu = data["device"]["cpu"]
    assert cpu["source"] == "/proc/stat"
    assert isinstance(cpu["cores"], int) and cpu["cores"] > 0
    if cpu["available"]:
        assert 0.0 <= cpu["percent"] <= 100.0
        assert cpu["level"] in {"ok", "warn", "danger"}
        assert cpu["note"] is None
    else:
        assert cpu["note"], "不可用时必须给出具体原因"

    mem = data["device"]["memory"]
    assert mem["source"] == "/proc/meminfo"
    assert mem["available"] is True
    assert mem["total_mb"] > 0
    assert 0 < mem["used_mb"] <= mem["total_mb"]
    assert 0.0 <= mem["percent"] <= 100.0
    assert mem["level"] in {"ok", "warn", "danger"}
    assert mem["swap_total_mb"] >= 0
    if mem["swap_total_mb"]:
        assert mem["swap_percent"] is not None


def test_cpu_percent_computed_from_injected_sample(make_app, monkeypatch):
    """差值采样：5 秒窗口内总 jiffies +1000、空闲 +900 → 10.0%。"""

    app = make_app()
    collector = app.state.collector
    monkeypatch.setattr("app.status._read_proc_stat", lambda: (11_000, 9_900))
    collector._cpu_prev = (time.time() - 5.0, 10_000, 9_000)

    cpu = collector._cpu()
    assert cpu["available"] is True
    assert cpu["percent"] == 10.0
    assert cpu["level"] == "ok"
    assert cpu["note"] is None


def test_cpu_first_sample_is_unavailable_with_reason(make_app):
    app = make_app()
    collector = app.state.collector
    collector._cpu_prev = None
    collector._cpu_last = None

    cpu = collector._cpu()
    assert cpu["available"] is False
    assert cpu["percent"] is None
    assert "刷新" in cpu["note"]


def test_cpu_stale_window_does_not_invent_a_value(make_app):
    """超过 60 秒的旧采样点不换算成“当前值”，留空并说明。"""

    app = make_app()
    collector = app.state.collector
    total, idle = _read_proc_stat()
    collector._cpu_prev = (time.time() - 300.0, total, idle)
    collector._cpu_last = None

    cpu = collector._cpu()
    assert cpu["available"] is False
    assert cpu["percent"] is None
    assert "过期" in cpu["note"] or "采样" in cpu["note"]


def test_level_thresholds():
    assert level("cpu", 69.9) == "ok"
    assert level("cpu", 70.0) == "warn"
    assert level("cpu", 90.0) == "danger"
    assert level("memory", 74.9) == "ok"
    assert level("memory", 90.0) == "danger"
    assert level("gpu_mem", 95.0) == "danger"
    assert level("temp_c", 74) == "ok"
    assert level("temp_c", 90) == "danger"
    assert level("cpu", None) is None


def test_status_page_renders_gauges_without_nan(make_app):
    app = make_app()
    # 让 CPU 也进入“有值”路径，验证 SVG 真的画出来
    total, idle = _read_proc_stat()
    app.state.collector._cpu_prev = (time.time() - 5.0, max(0, total - 2000), max(0, idle - 1900))
    client = TestClient(app)
    login(client)

    page = client.get("/status")
    assert page.status_code == 200
    assert "设备仪表盘" in page.text
    # CPU + 内存 + FakeProbe 的两块 GPU，各一张卡片
    assert page.text.count('class="gauge-card"') == 4
    # 至少内存与 CPU 画出了真实表盘（占位卡没有 svg class=gauge）
    assert page.text.count('<svg class="gauge"') >= 2
    assert "gauge-needle" in page.text
    assert "gauge-tick" in page.text
    assert "NaN" not in page.text
    assert "undefined" not in page.text
    # FakeProbe GPU1：98% 利用率、95% 显存 → 告警级
    assert "gauge-fill--danger" in page.text


def test_gpu_failure_keeps_cpu_memory_and_shows_placeholder(make_app):
    probe = FakeProbe(error="nvidia-smi 查询失败：测试注入")
    app = make_app(probe)
    total, idle = _read_proc_stat()
    app.state.collector._cpu_prev = (time.time() - 5.0, max(0, total - 2000), max(0, idle - 1900))
    client = TestClient(app)
    login(client)

    page = client.get("/status")
    assert page.status_code == 200
    # 显卡不可用：不画空仪表盘，只画 CPU + 内存两张
    assert page.text.count('class="gauge-card"') == 2
    assert page.text.count('<svg class="gauge"') == 2
    assert "显卡仪表不可用" in page.text
    assert "测试注入" in page.text
    # 原有「显卡概览」告警仍在
    assert "显卡信息不可用" in page.text
    assert "NaN" not in page.text


def test_status_partial_includes_gauges(client):
    login(client)
    fragment = client.get("/status/partial")
    assert fragment.status_code == 200
    assert "设备仪表盘" in fragment.text
    assert "gauge-grid" in fragment.text
