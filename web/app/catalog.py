"""The two generation models this console is allowed to control.

Only models named by the specification are present.  Every fact below is
taken from the deployment scripts in this repository (ports, entry scripts,
backend versions and VRAM profiles); nothing here is inferred at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class Capability:
    """A static, documented capability of a model (shown on the status page)."""

    label: str
    value: str


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    vendor: str
    summary: str
    kind: str  # "video_audio" | "image"
    capabilities: tuple[Capability, ...]
    backend: str
    script_rel: str
    port: int
    health_path: str
    health_label: str
    startup_timeout_seconds: int
    process_markers: tuple[str, ...]
    deployment_note: str

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.health_path}"

    def script_path(self, settings: Settings):
        return settings.repo_root / self.script_rel

    @property
    def working_dir_rel(self) -> str:
        # Entry scripts live at <model-dir>/serve*.sh and expect that directory
        # to be the working directory (they reference env/, models/, logs/).
        return str(self.script_rel.rsplit("/", 1)[0])


CATALOG: dict[str, ModelSpec] = {
    "minimax-h3": ModelSpec(
        id="minimax-h3",
        name="MiniMax H3",
        vendor="MiniMax",
        summary="文本 / 图像生成带音轨视频",
        kind="video_audio",
        capabilities=(
            Capability("生成模态", "视频 + 音频（带音轨）"),
            Capability("输入模态", "文本 / 图像（TI2V）"),
            Capability("服务后端", "SGLang 0.5.22 · FL2VA"),
            Capability("显存档位", "12 GB 单卡 · memory 模式（layerwise offload）"),
        ),
        backend="SGLang 0.5.22（FL2VA）",
        script_rel="h3/serve_recipe_a.sh",
        port=30010,
        health_path="/health",
        health_label="GET /health",
        # Measured on this workstation: ~12 min from launch to "ready".
        startup_timeout_seconds=1200,
        process_markers=("sglang", "MiniMax-H3"),
        deployment_note="由 h3/serve_recipe_a.sh 启动；监听 127.0.0.1:30010。",
    ),
    "qwen-image2.0": ModelSpec(
        id="qwen-image2.0",
        name="Qwen-Image 2.0",
        vendor="Qwen",
        summary="文本生成图像",
        kind="image",
        capabilities=(
            Capability("生成模态", "图像"),
            Capability("输入模态", "文本（文生图）"),
            Capability("服务后端", "ComfyUI 0.42.0 · Qwen-Image-2.1-Turbo int8"),
            Capability("显存档位", "12 GB 单卡"),
        ),
        backend="ComfyUI 0.42.0（Qwen-Image-2.1-Turbo int8）",
        script_rel="qwen-image/serve.sh",
        port=30020,
        health_path="/system_stats",
        health_label="GET /system_stats",
        startup_timeout_seconds=600,
        process_markers=("ComfyUI/main.py", "30020"),
        deployment_note="由 qwen-image/serve.sh 启动；监听 127.0.0.1:30020。",
    ),
}

MODEL_ORDER: tuple[str, ...] = ("minimax-h3", "qwen-image2.0")


def get_spec(model_id: str) -> ModelSpec | None:
    return CATALOG.get(model_id)
