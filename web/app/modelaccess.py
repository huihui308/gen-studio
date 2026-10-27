"""Per-model listen address (``127.0.0.1`` or ``0.0.0.0``).

This is deliberately separate from the console's own listen address, which is
deployment-level (``GEN_STUDIO_HOST`` / ``web/data/settings.json``).  A model can
therefore be reachable from the LAN while the console stays loopback-only, or
vice versa.

The value is persisted in ``web/data/model_access.json`` (mode 0600) and is
injected into the model entry script as ``GEN_STUDIO_MODEL_HOST`` on the next
start.  It never changes a process that is already running: the operator has
to stop and start the model again for the new address to take effect.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

from .catalog import MODEL_ORDER

#: Values accepted by the UI and by the API.
MODEL_ACCESS_HOSTS: tuple[str, ...] = ("127.0.0.1", "0.0.0.0")

#: Preserve the pre-existing behaviour (loopback only) until the operator
#: explicitly opts a model into LAN access.
DEFAULT_MODEL_ACCESS_HOST = "127.0.0.1"


class ModelAccessError(ValueError):
    """Invalid model access configuration supplied by the operator."""


def validate_access_host(value: str | None) -> str:
    host = (value or "").strip()
    if host not in MODEL_ACCESS_HOSTS:
        raise ModelAccessError("模型访问地址只能是 127.0.0.1 或 0.0.0.0")
    return host


class ModelAccessStore:
    """Small, thread-safe JSON store keyed by model id."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.error: str | None = None
        self._hosts = self._load()

    # ------------------------------------------------------------------ load
    def _defaults(self) -> dict[str, str]:
        return {model_id: DEFAULT_MODEL_ACCESS_HOST for model_id in MODEL_ORDER}

    def _load(self) -> dict[str, str]:
        hosts = self._defaults()
        if not self.path.exists():
            return hosts
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.error = f"读取模型访问地址配置失败，已使用默认值：{exc}"
            return hosts
        if not isinstance(raw, dict):
            self.error = "模型访问地址配置格式无效，已使用默认值"
            return hosts
        for model_id in MODEL_ORDER:
            value = raw.get(model_id)
            if value is None:
                continue
            if isinstance(value, str) and value.strip() in MODEL_ACCESS_HOSTS:
                hosts[model_id] = value.strip()
            else:
                self.error = f"{model_id} 的模型访问地址无效，已回退到 {DEFAULT_MODEL_ACCESS_HOST}"
        return hosts

    # ------------------------------------------------------------------- read
    def get(self, model_id: str) -> str:
        return self._hosts.get(model_id, DEFAULT_MODEL_ACCESS_HOST)

    def all(self) -> dict[str, str]:
        return {model_id: self._hosts.get(model_id, DEFAULT_MODEL_ACCESS_HOST) for model_id in MODEL_ORDER}

    # ------------------------------------------------------------------ write
    def set(self, model_id: str, host: str) -> None:
        if model_id not in MODEL_ORDER:
            raise ModelAccessError(f"未知模型：{model_id}")
        cleaned = validate_access_host(host)
        with self._lock:
            self._hosts[model_id] = cleaned
            self._persist()
        self.error = None

    def _persist(self) -> None:
        payload = self.all()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
