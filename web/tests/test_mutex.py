"""互斥强约束：本机 h3 与 qwen-image 只能同时运行一个（2026-11-12 David 设定）。

三层防线的测试：
  1. 控制台后端 manager.start() 在另一个模型活着时拒绝启动（不抢占、不杀对方）；
  2. 控制页把对方的运行状态显示出来并禁用启动按钮；
  3. 两个入口脚本（绕过控制台手动启动）自带 pgrep 闸门，检测到对方就退出。

脚本测试一律在一次性副本 + 假 pgrep / 假解释器下进行：闸门失效时最坏也只是
跑假解释器或死在缺失的 env312 上，绝不会真的拉起 sglang / ComfyUI。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.manager import ModelManager, StartError
from app.proc import ProcInfo

from conftest import FakeProbe, csrf_token, login

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_http_start_refuses_while_other_model_runs_and_recovers(make_app):
    """控制台层：qwen 运行时启动 h3 被拒（对方不受影响），停止后可正常启动。"""
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client, "/control")

    # 先起 qwen-image
    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    qwen_pid = app.state.manager.state("qwen-image2.0").pid
    assert qwen_pid is not None

    # 互斥：启动 h3 必须被拒绝
    response = client.post(
        "/control/minimax-h3/start",
        data={"csrf_token": token, "gpu_index": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "只能同时运行一个" in page.text
    assert "Qwen-Image 2.0 正在运行" in page.text
    assert app.state.manager.state("minimax-h3").pid is None
    # 拒绝不等于抢占：正在运行的 qwen 必须原样活着
    assert app.state.manager.state("qwen-image2.0").pid == qwen_pid
    # 控制页给出原因并禁用 h3 的启动按钮
    assert re.search(r'id="gpu-minimax-h3"[^>]*disabled', page.text)
    assert "需先停止它" in page.text

    # 停掉 qwen 后，h3 可以正常启动
    response = client.post(
        "/control/qwen-image2.0/stop",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert "notice=" in response.headers["location"]
    response = client.post(
        "/control/minimax-h3/start",
        data={"csrf_token": token, "gpu_index": "1"},
        follow_redirects=False,
    )
    assert "notice=" in response.headers["location"]
    h3_pid = app.state.manager.state("minimax-h3").pid
    assert h3_pid is not None

    # 反方向同样成立：h3 运行时启动 qwen 被拒，h3 不受影响
    response = client.post(
        "/control/qwen-image2.0/start",
        data={"csrf_token": token, "gpu_index": "1"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "只能同时运行一个" in page.text
    assert "MiniMax H3 正在运行" in page.text
    assert app.state.manager.state("qwen-image2.0").pid is None
    assert app.state.manager.state("minimax-h3").pid == h3_pid
    assert re.search(r'id="gpu-qwen-image2.0"[^>]*disabled', page.text)


def test_external_other_model_blocks_start(settings, monkeypatch):
    """对方进程在控制台之外启动（加载中、尚无端口）时同样拒绝。"""
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
    with pytest.raises(StartError, match="只能同时运行一个"):
        manager.start("qwen-image2.0", 0, "127.0.0.1")
    assert manager.state("qwen-image2.0").pid is None
    assert not (settings.repo_root / "gpu-qwen.txt").exists()


def test_other_models_port_held_blocks_start(make_app, monkeypatch):
    """对方端口被未知进程占着（detect 判不出归属）时也拒绝，宁可误杀不放行。"""
    app = make_app()
    monkeypatch.setattr("app.manager.proc.listener_pid", lambda port: None)
    monkeypatch.setattr("app.manager.proc.port_open", lambda port, **kw: port == 30010)
    with pytest.raises(StartError, match="只能同时运行一个"):
        app.state.manager.start("qwen-image2.0", 0, "127.0.0.1")
    assert app.state.manager.state("qwen-image2.0").pid is None


def _copy_script(tmp_path: Path, rel: str) -> Path:
    """把真实入口脚本拷进一次性仓库（脚本用相对路径找自己的 env/）。"""
    target_dir = tmp_path / Path(rel).parent
    target_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_path / rel
    target.write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o755)
    return target


def _fake_pgrep(tmp_path: Path, found: bool) -> dict:
    """假 pgrep：记录被调用的参数并按 found 退出——同时证明闸门真的求值过。"""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "pgrep.log"
    pgrep = bin_dir / "pgrep"
    pgrep.write_text(
        '#!/bin/sh\necho "$@" >> "%s"\nexit %d\n' % (log, 0 if found else 1),
        encoding="utf-8",
    )
    pgrep.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def _run(script: Path, env: dict, timeout: int = 60):
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_h3_script_gate_refuses_while_qwen_running(tmp_path):
    """手动跑 h3/serve_recipe_a.sh 且 ComfyUI 活着 → 闸门直接退出（真脚本副本）。"""
    script = _copy_script(tmp_path, "h3/serve_recipe_a.sh")
    env = _fake_pgrep(tmp_path, found=True)
    env["H3"] = str(tmp_path / "h3")  # 即便闸门失效也绝不会 exec 真 sglang
    result = _run(script, env)
    assert result.returncode == 1
    assert "互斥约束" in result.stderr
    assert (tmp_path / "pgrep.log").exists(), "必须由假 pgrep 判定（说明闸门求值了）"


def test_h3_script_gate_passes_when_qwen_absent(tmp_path):
    """对方不在时闸门放行（假 pgrep 找不到 → 继续走原脚本后续步骤）。"""
    script = _copy_script(tmp_path, "h3/serve_recipe_a.sh")
    env = _fake_pgrep(tmp_path, found=False)
    env["H3"] = str(tmp_path / "h3")
    result = _run(script, env)
    assert "互斥约束" not in result.stderr
    assert (tmp_path / "pgrep.log").exists(), "必须由假 pgrep 判定（说明闸门求值了）"
    # 一次性仓库没有 env312/ninja，脚本只可能死在自己的环境准备阶段——
    # 绝不能出现真 sglang 的痕迹。
    assert "MiniMax-H3" not in result.stderr + result.stdout


def test_qwen_script_gate_refuses_while_h3_running(tmp_path):
    """手动跑 qwen-image/serve.sh 且 SGLang 活着 → 闸门直接退出（真脚本副本）。"""
    script = _copy_script(tmp_path, "qwen-image/serve.sh")
    # 假解释器：闸门若失效就会 exec 它并留下痕迹，而不是真 ComfyUI。
    fake_py = tmp_path / "qwen-image" / "env" / "bin" / "python"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text("#!/bin/sh\necho FAKE_COMFY_LAUNCHED\nexit 42\n", encoding="utf-8")
    fake_py.chmod(0o755)
    env = _fake_pgrep(tmp_path, found=True)
    result = _run(script, env)
    assert result.returncode == 1
    assert "互斥约束" in result.stderr
    assert "FAKE_COMFY_LAUNCHED" not in result.stdout
    assert (tmp_path / "pgrep.log").exists(), "必须由假 pgrep 判定（说明闸门求值了）"


def test_qwen_script_gate_passes_when_h3_absent(tmp_path):
    """对方不在时闸门放行 → 真脚本一路 exec 到（假）解释器。"""
    script = _copy_script(tmp_path, "qwen-image/serve.sh")
    fake_py = tmp_path / "qwen-image" / "env" / "bin" / "python"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text("#!/bin/sh\necho FAKE_COMFY_LAUNCHED\nexit 42\n", encoding="utf-8")
    fake_py.chmod(0o755)
    env = _fake_pgrep(tmp_path, found=False)
    result = _run(script, env)
    assert "互斥约束" not in result.stderr
    assert "FAKE_COMFY_LAUNCHED" in result.stdout
    assert result.returncode == 42
    assert (tmp_path / "pgrep.log").exists(), "必须由假 pgrep 判定（说明闸门求值了）"
