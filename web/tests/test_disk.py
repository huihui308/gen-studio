"""Disk block on the status page: /proc/mounts parsing, statvfs, tones.

The collector reads real system files (/proc/mounts + os.statvfs); these
tests assert the payload contract, the df(1) usage formula, the level
thresholds, the rendered table and the honest-unavailable paths (a broken
mount shows a reason, never a fabricated number).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import status as status_mod
from app.status import level

from conftest import login

GIB = 1024 ** 3


def test_api_status_includes_disk_block(client):
    login(client)
    data = client.get("/api/status").json()
    disk = data["device"]["disk"]
    assert disk["source"] == "/proc/mounts + statvfs"
    if disk["available"]:
        assert disk["items"], "可用时必须列出至少一个挂载点"
        assert disk["note"] is None
        for item in disk["items"]:
            assert item["mount"].startswith("/")
            assert item["fs"]
            if item["available"]:
                assert item["total_mb"] > 0
                assert 0 <= item["used_mb"] <= item["total_mb"]
                assert 0.0 <= item["percent"] <= 100.0
                assert item["level"] in {"ok", "warn", "danger"}
            else:
                assert item["note"], "单个挂载点不可用时必须给出原因"
    else:
        assert disk["note"], "整块不可用时必须给出原因"


def test_disk_level_thresholds():
    assert level("disk", 79.9) == "ok"
    assert level("disk", 80.0) == "warn"
    assert level("disk", 95.0) == "danger"
    assert level("disk", None) is None


def test_status_page_renders_disk_table(make_app, monkeypatch):
    """确定性挂载：根盘 60%（正常）、数据盘 95%（告警级进度条）。"""

    monkeypatch.setattr(
        status_mod, "_read_mounts",
        lambda: [("/dev/sda2", "/", "ext4"), ("/dev/sdb", "/mnt/sdb1", "ext4")],
    )

    def _usage(mount: str):
        if mount == "/":
            return 100 * GIB, 60 * GIB, 40 * GIB, 60.0
        return 200 * GIB, 190 * GIB, 10 * GIB, 95.0

    monkeypatch.setattr(status_mod, "_disk_usage", _usage)

    app = make_app()
    client = TestClient(app)
    login(client)

    page = client.get("/status")
    assert page.status_code == 200
    assert "磁盘空间" in page.text
    assert "/proc/mounts" in page.text and "statvfs" in page.text
    assert "/mnt/sdb1" in page.text
    assert "60.0 / 100.0 GiB" in page.text
    assert "190.0 / 200.0 GiB" in page.text
    assert "40.0 GiB" in page.text
    assert "60.0 %" in page.text and "95.0 %" in page.text
    # 95% 命中 danger 阈值 → 进度条按告警色渲染
    assert "gauge-bar-fill--danger" in page.text
    assert "NaN" not in page.text
    assert "undefined" not in page.text

    fragment = client.get("/status/partial")
    assert fragment.status_code == 200
    assert "磁盘空间" in fragment.text


def test_disk_unavailable_when_mounts_unreadable(make_app, monkeypatch):
    def _boom():
        raise OSError("注入的读取失败")

    monkeypatch.setattr(status_mod, "_read_mounts", _boom)
    app = make_app()
    client = TestClient(app)
    login(client)

    data = client.get("/api/status").json()["device"]["disk"]
    assert data["available"] is False
    assert data["items"] == []
    assert "注入的读取失败" in data["note"]

    page = client.get("/status")
    assert "磁盘信息不可用" in page.text
    assert "注入的读取失败" in page.text




def test_disk_usage_percent_matches_df_rounding(monkeypatch):
    """使用率向上取整，与 df(1) 的 Use% 完全一致（df 不做四舍五入）。"""

    class _Stat:
        f_frsize = 1
        f_blocks = 1000          # 总量 1000
        f_bfree = 116            # 空闲 116
        f_bavail = 116           # 普通用户可见 116（无 root 预留）

    monkeypatch.setattr(status_mod.os, "statvfs", lambda mount: _Stat())
    total, used, avail, percent = status_mod._disk_usage("/mnt/fake")
    assert (total, used, avail) == (1000, 884, 116)
    # 884 / 1000 = 88.4% → df 向上取整显示 89%（四舍五入会得 88，与 df 不一致）
    assert percent == 89.0

def test_single_mount_statvfs_failure_keeps_other_rows(make_app, monkeypatch):
    """一个挂载点读不到 → 该行「不可用」+ 原因，其余行照常显示。"""

    monkeypatch.setattr(
        status_mod, "_read_mounts",
        lambda: [("/dev/sda2", "/", "ext4"), ("/dev/sdb", "/mnt/sdb1", "ext4")],
    )

    def _usage(mount: str):
        if mount == "/mnt/sdb1":
            raise OSError("挂载点已消失")
        return 100 * GIB, 40 * GIB, 60 * GIB, 40.0

    monkeypatch.setattr(status_mod, "_disk_usage", _usage)
    app = make_app()
    client = TestClient(app)
    login(client)

    data = client.get("/api/status").json()["device"]["disk"]
    assert data["available"] is True
    by_mount = {item["mount"]: item for item in data["items"]}
    assert by_mount["/"]["available"] is True
    assert by_mount["/mnt/sdb1"]["available"] is False
    assert "挂载点已消失" in by_mount["/mnt/sdb1"]["note"]

    page = client.get("/status")
    assert "挂载点已消失" in page.text
    assert "badge-unavailable" in page.text
    assert "40.0 / 100.0 GiB" in page.text
