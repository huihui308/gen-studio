"""GPU discovery through ``nvidia-smi``.

Design rule for requirement 3: there is **no silent default GPU**.  If the
probe cannot produce a list of GPUs, callers receive ``available=False`` with
a human-readable reason and must refuse to start a model.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Gpu:
    index: int
    uuid: str | None
    name: str
    memory_total_mb: int | None
    memory_used_mb: int | None
    memory_free_mb: int | None
    utilization_pct: int | None
    temperature_c: int | None


@dataclass(frozen=True)
class GpuList:
    available: bool
    gpus: tuple[Gpu, ...] = ()
    error: str | None = None


_QUERY_FIELDS = (
    "index,uuid,name,memory.total,memory.used,memory.free,"
    "utilization.gpu,temperature.gpu"
)


def _to_int(value: str) -> int | None:
    value = value.strip()
    if not value or value.upper() in {"N/A", "[N/A]", "NOT SUPPORTED", "UNKNOWN"}:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_row(row: str) -> Gpu | None:
    parts = [part.strip() for part in row.split(",")]
    if len(parts) < 8:
        return None
    index = _to_int(parts[0])
    if index is None:
        return None
    return Gpu(
        index=index,
        uuid=parts[1] or None,
        name=parts[2] or f"GPU {index}",
        memory_total_mb=_to_int(parts[3]),
        memory_used_mb=_to_int(parts[4]),
        memory_free_mb=_to_int(parts[5]),
        utilization_pct=_to_int(parts[6]),
        temperature_c=_to_int(parts[7]),
    )


class GpuProbe:
    """Queries the live GPU inventory; never invents devices."""

    def __init__(self, nvidia_smi: str = "nvidia-smi", timeout_seconds: float = 5.0) -> None:
        self.nvidia_smi = nvidia_smi
        self.timeout_seconds = timeout_seconds

    def list(self) -> GpuList:
        executable = shutil.which(self.nvidia_smi) or (
            self.nvidia_smi if "/" in self.nvidia_smi and shutil.which(self.nvidia_smi) is None else None
        )
        if not executable:
            return GpuList(
                available=False,
                error=f"未找到 nvidia-smi 可执行文件（{self.nvidia_smi}），无法获取显卡信息",
            )
        try:
            completed = subprocess.run(
                [executable, f"--query-gpu={_QUERY_FIELDS}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return GpuList(available=False, error=f"nvidia-smi 查询超时（>{self.timeout_seconds:g}s）")
        except OSError as exc:
            return GpuList(available=False, error=f"nvidia-smi 无法执行：{exc}")

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"退出码 {completed.returncode}"
            return GpuList(available=False, error=f"nvidia-smi 查询失败：{reason}")

        rows = [line for line in completed.stdout.splitlines() if line.strip()]
        gpus = tuple(gpu for gpu in (_parse_row(row) for row in rows) if gpu is not None)
        if not gpus:
            return GpuList(available=False, error="nvidia-smi 未返回任何可用显卡")

        gpus = tuple(sorted(gpus, key=lambda gpu: gpu.index))
        duplicates = {gpu.index for gpu in gpus if sum(1 for other in gpus if other.index == gpu.index) > 1}
        if duplicates:
            return GpuList(available=False, error=f"nvidia-smi 返回了重复的显卡索引：{sorted(duplicates)}")
        return GpuList(available=True, gpus=gpus)

    def get(self, index: int) -> tuple[Gpu | None, str | None]:
        """Return one GPU plus an error message when it cannot be resolved."""
        listing = self.list()
        if not listing.available:
            return None, listing.error
        for gpu in listing.gpus:
            if gpu.index == index:
                return gpu, None
        return None, f"显卡索引 {index} 不存在；当前可用显卡：{[gpu.index for gpu in listing.gpus]}"
